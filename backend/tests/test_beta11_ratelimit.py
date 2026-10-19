"""Beta11 API 分级限流契约（隔离内存 SQLite；不构成 PostgreSQL 并发与迁移结论）。

覆盖（规划设计/pachong.md §三、§四）：
- 同一 IP 下两个登录用户不互相挤占；
- 连续超限 -> 429 + Retry-After + 统一错误码 RATE_LIMITED，并写审计；
- 窗口过期后计数重置，过期行被顺手清理；
- 匿名按 IP 计数，登录后按 user 计数，会话其次；
- 分级差异：admin 额度高于 anon；
- RATE_LIMIT_ENABLED=False 全放行且不写计数；
- 端到端：登录/搜索接口超限后 429，登录响应不泄露账号是否存在。
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from starlette.requests import Request

import app.models_registry  # noqa: F401  # 确保全部表进入 Base.metadata
from app.config import settings
from app.core.errors import RateLimited
from app.core.security import hash_password
from app.db import Base, get_db
from app.modules.identity.models import Session, User
from app.modules.moderation.models import AuditLog
from app.modules.ratelimit import service as ratelimit
from app.modules.ratelimit.deps import get_counter_db
from app.modules.ratelimit.models import RateLimitCounter


@compiles(JSONB, "sqlite")
def jsonb_for_test(type_, compiler, **kwargs):
    return "JSON"


def make_request(ip: str = "203.0.113.7", forwarded: str | None = None) -> Request:
    """构造最小 ASGI 请求：client 即来源 IP；forwarded 模拟代理追加的首个 hop。"""
    headers = [(b"host", b"testserver")]
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/search",
            "headers": headers,
            "client": (ip, 51234),
        }
    )


class Beta11RateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 冻结限流模块时钟：固定窗口边界不受真实时间影响，计数断言完全确定。
        self._time_patch = patch.object(
            ratelimit,
            "_now",
            lambda: datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc),
        )
        self._time_patch.start()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.factory()
        self.owner = User(
            email="owner@example.org",
            password_hash=hash_password("owner-pw"),
            role="contributor",
            status="active",
        )
        self.other = User(
            email="other@example.org",
            password_hash=hash_password("other-pw"),
            role="user",
            status="active",
        )
        self.admin = User(
            email="admin@example.org",
            password_hash=hash_password("admin-pw"),
            role="admin",
            status="active",
        )
        self.db.add_all([self.owner, self.other, self.admin])
        await self.db.flush()

    async def asyncTearDown(self):
        self._time_patch.stop()
        await self.db.close()
        await self.engine.dispose()

    async def enforce(self, *, action="search", user=None, session=None, request=None):
        return await ratelimit.enforce(
            self.db,
            action=action,
            user=user,
            session=session,
            request=request or make_request(),
        )

    @staticmethod
    def tier_limits(**overrides):
        base = {"anon": 2, "user": 2, "contributor": 2, "reviewer": 2, "admin": 2}
        base.update(overrides)
        return base

    def _http_client(self):
        """业务、限流与独立登录审计会话都指向测试内存库。"""
        from app.main import app
        from app.modules.identity import service as identity

        self.enterContext(patch.object(identity, "async_session_factory", self.factory))

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_counter_db] = database
        return app, httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000"
        )

    # ------------------------------------------------------------------ #
    # subject 解析与分级
    # ------------------------------------------------------------------ #

    async def test_subject_priority_user_session_ip(self):
        request = make_request()
        self.assertEqual(
            ratelimit.resolve_subject(self.owner, None, request),
            ("user", str(self.owner.id)),
        )
        session = Session(
            user_id=self.owner.id,
            token_digest="d" * 64,
            issued_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        self.db.add(session)
        await self.db.flush()
        self.assertEqual(
            ratelimit.resolve_subject(None, session, request),
            ("session", str(session.id)),
        )
        self.assertEqual(
            ratelimit.resolve_subject(None, None, request), ("ip", "203.0.113.7")
        )
        # 客户端伪造代理头不能更换限流桶。
        self.assertEqual(
            ratelimit.resolve_subject(
                None, None, make_request(forwarded="198.51.100.9, 10.0.0.1")
            ),
            ("ip", "203.0.113.7"),
        )

    async def test_tier_mapping(self):
        self.assertEqual(ratelimit.tier_for(None), "anon")
        self.assertEqual(ratelimit.tier_for("user"), "user")
        self.assertEqual(ratelimit.tier_for("contributor"), "contributor")
        self.assertEqual(ratelimit.tier_for("reviewer"), "reviewer")
        self.assertEqual(ratelimit.tier_for("admin"), "admin")
        self.assertEqual(ratelimit.tier_for("unknown-role"), "anon")

    # ------------------------------------------------------------------ #
    # 核心行为
    # ------------------------------------------------------------------ #

    async def test_same_ip_users_do_not_contend(self):
        """同一公网 IP 下两个登录用户各记各的账，不互相挤占（pachong.md §四）。"""
        with patch.object(settings, "RATE_LIMIT_SEARCH", self.tier_limits(user=2)):
            request = make_request(ip="203.0.113.9")
            for _ in range(2):
                await self.enforce(user=self.owner, request=request)
            # 若按 IP 共享计数，other 的第一次调用就会超限；实际应各自放行 2 次。
            for _ in range(2):
                await self.enforce(user=self.other, request=request)
            with self.assertRaises(RateLimited):
                await self.enforce(user=self.owner, request=request)
        rows = (await self.db.execute(select(RateLimitCounter))).scalars().all()
        self.assertEqual(
            {(row.subject_kind, row.subject) for row in rows},
            {("user", str(self.owner.id)), ("user", str(self.other.id))},
        )

    async def test_over_limit_returns_429_retry_after_and_audit(self):
        with patch.object(settings, "RATE_LIMIT_SEARCH", self.tier_limits(anon=2)):
            request = make_request()
            await self.enforce(request=request)
            await self.enforce(request=request)
            with self.assertRaises(RateLimited) as ctx:
                await self.enforce(request=request)
        exc = ctx.exception
        self.assertEqual(exc.http_status, 429)
        self.assertEqual(exc.code, "RATE_LIMITED")
        self.assertEqual(exc.details["tier"], "anon")
        self.assertGreaterEqual(exc.details["retry_after"], 1)
        self.assertLessEqual(
            exc.details["retry_after"], settings.RATE_LIMIT_WINDOW_SECONDS
        )
        self.assertEqual(exc.headers["Retry-After"], str(exc.details["retry_after"]))
        # 超限写审计：只记动作/主体种类/档位等非敏感信息，不记 IP 与 subject。
        audits = (
            await self.db.scalars(
                select(AuditLog).where(AuditLog.action == "ratelimit.blocked")
            )
        ).all()
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].after_state["action"], "search")
        self.assertEqual(audits[0].after_state["subject_kind"], "ip")
        self.assertEqual(audits[0].after_state["tier"], "anon")
        self.assertNotIn("subject", audits[0].after_state)
        self.assertNotIn("ip", audits[0].after_state)

    async def test_window_expiry_resets_count_and_cleans_stale_rows(self):
        request = make_request()
        # 先打满当前窗口（临时调小 user 档）。
        with patch.object(settings, "RATE_LIMIT_SEARCH", self.tier_limits(contributor=1)):
            await self.enforce(user=self.owner, request=request)
            with self.assertRaises(RateLimited):
                await self.enforce(user=self.owner, request=request)
        # 把当前窗口行改写成“两个窗口之前”，确定性地模拟窗口过期（不做真实 sleep）。
        row = (
            await self.db.execute(
                select(RateLimitCounter).where(
                    RateLimitCounter.subject == str(self.owner.id)
                )
            )
        ).scalar_one()
        row.window_start = row.window_start - timedelta(
            seconds=2 * settings.RATE_LIMIT_WINDOW_SECONDS + 5
        )
        await self.db.commit()
        # 窗口过期：同一 subject 放行；过期行被顺手清理，只剩新窗口一行。
        await self.enforce(user=self.owner, request=request)
        remaining = await self.db.scalar(
            select(func.count())
            .select_from(RateLimitCounter)
            .where(RateLimitCounter.subject == str(self.owner.id))
        )
        self.assertEqual(remaining, 1)

    async def test_anonymous_counts_by_ip_login_by_user(self):
        with patch.object(
            settings, "RATE_LIMIT_SEARCH", self.tier_limits(anon=2, user=2)
        ):
            request = make_request(ip="203.0.113.10")
            await self.enforce(request=request)
            await self.enforce(request=request)
            with self.assertRaises(RateLimited):
                await self.enforce(request=request)
            # 同一 IP 登录后按 user 计账，不受匿名额度影响。
            await self.enforce(user=self.owner, request=request)
            await self.enforce(user=self.owner, request=request)
            with self.assertRaises(RateLimited):
                await self.enforce(user=self.owner, request=request)
            # 另一个匿名 IP 独立计账。
            await self.enforce(request=make_request(ip="203.0.113.11"))

    async def test_admin_tier_has_higher_quota_than_anon(self):
        with patch.object(
            settings, "RATE_LIMIT_SEARCH", self.tier_limits(anon=1, admin=3)
        ):
            request = make_request()
            await self.enforce(request=request)
            with self.assertRaises(RateLimited):
                await self.enforce(request=request)
            for _ in range(3):
                await self.enforce(user=self.admin, request=request)
            with self.assertRaises(RateLimited):
                await self.enforce(user=self.admin, request=request)

    async def test_disabled_allows_everything(self):
        with patch.object(settings, "RATE_LIMIT_ENABLED", False):
            for _ in range(20):
                await self.enforce(request=make_request())
        total = await self.db.scalar(
            select(func.count()).select_from(RateLimitCounter)
        )
        self.assertEqual(total, 0)

    # ------------------------------------------------------------------ #
    # 端到端（HTTP）
    # ------------------------------------------------------------------ #

    async def test_login_success_still_works_under_limit(self):
        app, client = self._http_client()
        try:
            async with client:
                response = await client.post(
                    "/api/v1/auth/login",
                    json={"email": self.owner.email, "password": "owner-pw"},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("njupt_session", response.headers.get("set-cookie", ""))
        finally:
            app.dependency_overrides.clear()

    async def test_login_endpoint_blocks_after_limit_without_leaking_account(self):
        app, client = self._http_client()
        try:
            async with client:
                for _ in range(5):
                    response = await client.post(
                        "/api/v1/auth/login",
                        json={
                            "email": "login@example.org",
                            "password": "wrong-password",
                        },
                    )
                    # 不存在的账号与错误密码同响应：不泄露账号是否存在。
                    self.assertEqual(response.status_code, 401, response.text)
                    self.assertEqual(
                        response.json()["error"]["code"], "UNAUTHENTICATED"
                    )
                    self.assertEqual(
                        response.json()["error"]["message"], "邮箱或密码不正确"
                    )
                blocked = await client.post(
                    "/api/v1/auth/login",
                    json={"email": "login@example.org", "password": "wrong-password"},
                )
                self.assertEqual(blocked.status_code, 429, blocked.text)
                self.assertIn("Retry-After", blocked.headers)
                self.assertGreaterEqual(int(blocked.headers["Retry-After"]), 1)
                error = blocked.json()["error"]
                self.assertEqual(error["code"], "RATE_LIMITED")
                self.assertEqual(error["details"]["action"], "login")
                self.assertNotIn("login@example.org", blocked.text)
                # 不存在的账号走同一 IP 桶：同样被限流，响应一致。
                ghost = await client.post(
                    "/api/v1/auth/login",
                    json={"email": "ghost@example.org", "password": "wrong-password"},
                )
                self.assertEqual(ghost.status_code, 429)
                self.assertEqual(ghost.json()["error"]["code"], "RATE_LIMITED")
        finally:
            app.dependency_overrides.clear()
        audits = (
            await self.db.scalars(
                select(AuditLog).where(AuditLog.action == "ratelimit.blocked")
            )
        ).all()
        self.assertEqual(len(audits), 2)
        self.assertEqual({audit.after_state["action"] for audit in audits}, {"login"})
        self.assertEqual(await self.db.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "identity.login_failed")
        ), 5)

    async def test_search_endpoint_blocks_over_limit(self):
        """搜索端到端：匿名超限后 429，带 Retry-After 头。"""
        app, client = self._http_client()
        try:
            with patch.object(
                settings, "RATE_LIMIT_SEARCH", self.tier_limits(anon=2)
            ):
                async with client:
                    for _ in range(2):
                        response = await client.get(
                            "/api/v1/search", params={"q": "数据结构"}
                        )
                        self.assertEqual(response.status_code, 200, response.text)
                    blocked = await client.get(
                        "/api/v1/search", params={"q": "数据结构"}
                    )
                    self.assertEqual(blocked.status_code, 429, blocked.text)
                    self.assertIn("Retry-After", blocked.headers)
                    self.assertEqual(
                        blocked.json()["error"]["code"], "RATE_LIMITED"
                    )
        finally:
            app.dependency_overrides.clear()
