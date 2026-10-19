"""beta16 通知实时流（SSE）测试：只测 service 层生成器与端点入口。

不用真实 HTTP 客户端消费 SSE（ASGITransport 会等待流结束而挂起）：
事件格式、心跳、未读数变化、时长与连接计数都直接对异步生成器断言；
仅 401/503 这类不会进入流式响应的分支用 HTTP 客户端验证。
"""

import asyncio
import json
import unittest
from datetime import datetime
from unittest.mock import patch

from app.config import settings
from app.core.errors import Conflict, NotEnabled
from app.modules.notification import router as notification_router
from app.modules.notification import stream
from app.modules.notification.models import Notification

try:
    import test_content_workflow as workflow
except ModuleNotFoundError:  # 以包方式加载（python -m unittest tests.xxx）
    from tests import test_content_workflow as workflow


class Beta16StreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)

    async def asyncTearDown(self):
        # 断言失败或中途退出时也不把进程内计数留给下一个用例。
        while stream.active_connections():
            stream.release_connection()
        await workflow.ContentWorkflowTests.asyncTearDown(self)

    # --- 辅助 ---------------------------------------------------------------

    def _notification(self, *, title="测试通知标题", body="测试通知正文"):
        return Notification(
            user_id=self.owner.id,
            type="review_result",
            title=title,
            body=body,
            data={},
        )

    async def _commit_notification(self, **kwargs):
        notification = self._notification(**kwargs)
        self.db.add(notification)
        await self.db.commit()
        return notification

    def _gen(self, *, heartbeat=0.01, max_seconds=0.05, initial_unread=None):
        return stream.notification_stream(
            self.factory,
            user_id=self.owner.id,
            heartbeat=heartbeat,
            max_seconds=max_seconds,
            initial_unread=initial_unread,
        )

    async def _next(self, gen, timeout=1):
        return await asyncio.wait_for(gen.__anext__(), timeout=timeout)

    async def _collect(self, gen, timeout=1):
        chunks = []
        while True:
            try:
                chunks.append(await self._next(gen, timeout=timeout))
            except StopAsyncIteration:
                return chunks

    @staticmethod
    def _parse(chunk):
        lines = chunk.splitlines()
        assert lines[0].startswith("event: "), chunk
        assert lines[1].startswith("data: "), chunk
        return lines[0][len("event: "):], json.loads(lines[1][len("data: "):])

    @staticmethod
    def _client():
        import httpx
        from app.main import app

        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost:3000",
            headers={"Origin": "http://localhost:3000", "X-CSRF-Token": "test"},
            cookies={settings.CSRF_COOKIE_NAME: "test"},
        )

    # --- 开关与鉴权 ---------------------------------------------------------

    async def test_disabled_raises_not_enabled(self):
        with patch.object(settings, "NOTIFICATION_STREAM_ENABLED", False):
            with self.assertRaises(NotEnabled) as ctx:
                await notification_router.notification_stream(user=self.owner)
        self.assertEqual(ctx.exception.http_status, 503)
        self.assertEqual(stream.active_connections(), 0)

    async def test_requires_login(self):
        from app.main import app

        try:
            async with self._client() as client:
                response = await client.get("/api/v1/notifications/stream")
            self.assertEqual(response.status_code, 401)
        finally:
            app.dependency_overrides.clear()

    # --- 连接计数 -----------------------------------------------------------

    async def test_rejects_new_connection_when_limit_reached(self):
        with patch.object(settings, "NOTIFICATION_STREAM_MAX_CONNECTIONS", 1):
            stream.acquire_connection()
            try:
                with self.assertRaises(Conflict) as ctx:
                    await notification_router.notification_stream(user=self.owner)
                self.assertEqual(ctx.exception.http_status, 409)
            finally:
                stream.release_connection()
        self.assertEqual(stream.active_connections(), 0)

    async def test_counter_released_after_normal_completion(self):
        stream.acquire_connection()
        self.assertEqual(stream.active_connections(), 1)

        chunks = await self._collect(self._gen(max_seconds=0.02))

        self.assertTrue(chunks)
        self.assertEqual(stream.active_connections(), 0)

    async def test_counter_released_when_generator_raises(self):
        def broken_factory():
            raise RuntimeError("db down")

        stream.acquire_connection()
        gen = stream.notification_stream(
            broken_factory,
            user_id=self.owner.id,
            heartbeat=0.01,
            max_seconds=0.05,
        )
        with self.assertRaises(RuntimeError):
            await gen.__anext__()
        self.assertEqual(stream.active_connections(), 0)

    # --- 事件格式 -----------------------------------------------------------

    async def test_first_event_is_unread_with_integer_count(self):
        await self._commit_notification()
        await self._commit_notification()

        gen = self._gen()
        try:
            event, payload = self._parse(await self._next(gen))
        finally:
            await gen.aclose()

        self.assertEqual(event, "unread")
        self.assertIsInstance(payload["unread"], int)
        self.assertNotIsInstance(payload["unread"], bool)
        self.assertEqual(payload["unread"], 2)

    async def test_second_unread_event_when_count_changes(self):
        gen = self._gen(heartbeat=0.01, max_seconds=1)
        try:
            event, payload = self._parse(await self._next(gen))
            self.assertEqual((event, payload["unread"]), ("unread", 0))

            await self._commit_notification()

            for _ in range(20):
                event, payload = self._parse(await self._next(gen))
                if event == "unread":
                    break
            else:
                self.fail("未读数变化后没有产生第二个 unread 事件")
            self.assertEqual(payload["unread"], 1)
        finally:
            await gen.aclose()

    async def test_unchanged_unread_is_not_repeated(self):
        chunks = await self._collect(self._gen(max_seconds=0.06, heartbeat=0.01))
        unread_events = [
            self._parse(chunk) for chunk in chunks if chunk.startswith("event: unread")
        ]
        self.assertEqual(len(unread_events), 1)
        self.assertEqual(unread_events[0][1]["unread"], 0)

    async def test_ping_events_follow_heartbeat(self):
        chunks = await self._collect(self._gen(max_seconds=0.06, heartbeat=0.01))
        pings = [
            self._parse(chunk) for chunk in chunks if chunk.startswith("event: ping")
        ]
        self.assertGreaterEqual(len(pings), 1)
        for event, payload in pings:
            self.assertEqual(event, "ping")
            self.assertIsInstance(datetime.fromisoformat(payload["t"]), datetime)

    async def test_stream_ends_after_max_seconds(self):
        gen = self._gen(max_seconds=0.02, heartbeat=0.01)
        self._parse(await self._next(gen))

        with self.assertRaises(StopAsyncIteration):
            while True:
                await self._next(gen)

        self.assertEqual(stream.active_connections(), 0)

    # --- 不泄露正文 ---------------------------------------------------------

    async def test_stream_contains_no_notification_content(self):
        notification = await self._commit_notification(
            title="机密标题-9f3c", body="机密正文-7a1d"
        )

        chunks = await self._collect(self._gen(max_seconds=0.04, heartbeat=0.01))
        joined = "".join(chunks)

        self.assertIn("event: unread", joined)
        self.assertNotIn(notification.title, joined)
        self.assertNotIn(notification.body, joined)
        for chunk in chunks:
            _, payload = self._parse(chunk)
            self.assertNotIn("title", payload)
            self.assertNotIn("body", payload)


if __name__ == "__main__":
    unittest.main()
