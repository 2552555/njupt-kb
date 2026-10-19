"""beta11：求资料解决资源失效展示与复核重开（隔离 SQLite）。

覆盖：撤销发布/无可见性时不泄露标题、批量列表一次解析、发起者/管理员可重开、
非发起者 403、匿名 401、open 重开 409、revision 不匹配 409、通知复用既有链路。
"""

import unittest
from unittest.mock import patch

import httpx
from sqlalchemy import select

from app.config import settings
from app.core.deps import get_current_user, get_db
from app.core.errors import Conflict, Forbidden
from app.main import app
from app.modules.access.models import AccessPolicy
from app.modules.content.models import Resource, ResourceVersion
from app.modules.interaction import request_management as requests
from app.modules.interaction import service
from app.modules.interaction.models import ResourceRequestFollow
from app.modules.interaction.schemas import RequestReopen, RequestStatusUpdate
from app.modules.moderation.models import AuditLog
from app.modules.notification.models import DomainEvent, Notification
from app.modules.notification import service as notifications

try:
    import test_content_workflow as workflow
except ModuleNotFoundError:  # 以包方式加载（python -m unittest tests.xxx）
    from tests import test_content_workflow as workflow


class Beta11Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.resource.publication_status = "published"
        self.resource.published_version_id = self.version.id
        self.version.review_status = "approved"
        self.policy = AccessPolicy(
            resource_id=self.resource.id, metadata_scope="public"
        )
        self.db.add(self.policy)
        await self.db.flush()
        # 同课程第二个公开资料，用于批量列表的混合可访问性场景。
        self.resource2 = Resource(
            course_id=self.course.id, owner_id=self.owner.id, resource_type="note"
        )
        self.db.add(self.resource2)
        await self.db.flush()
        self.version2 = ResourceVersion(
            resource_id=self.resource2.id,
            created_by=self.owner.id,
            version_no=1,
            title="second note",
            review_status="approved",
        )
        self.db.add(self.version2)
        await self.db.flush()
        self.resource2.published_version_id = self.version2.id
        self.resource2.publication_status = "published"
        self.db.add(
            AccessPolicy(resource_id=self.resource2.id, metadata_scope="public")
        )
        await self.db.flush()
        result = await service.create_request(
            self.db,
            user_id=self.owner.id,
            course_id=self.course.id,
            scope=None,
            description="需要课程笔记",
        )
        self.request_id = result.request.id

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def fulfill(self, request_id=None, resource_id=None):
        return await requests.change_status(
            self.db,
            user=self.owner,
            request_id=request_id or self.request_id,
            payload=RequestStatusUpdate(
                status="fulfilled",
                expected_revision=1,
                resource_id=resource_id or self.resource.id,
                reason="核实后解决",
            ),
            request_trace_id="beta11",
        )

    async def test_withdrawn_resolution_hides_title_and_keeps_state(self):
        fulfilled = await self.fulfill()
        self.assertTrue(fulfilled.resolved_resource_accessible)
        self.resource.publication_status = "withdrawn"
        await self.db.flush()

        # 撤销发布：状态不被动过，但解决资源一律不可访问，且不泄露标题。
        detail = await requests.request_detail(self.db, self.request_id)
        self.assertEqual(detail.status, "fulfilled")
        self.assertFalse(detail.resolved_resource_accessible)
        self.assertFalse(detail.resolution_available)
        self.assertIsNone(detail.resolved_resource_title)
        self.assertIsNone(detail.resolved_resource_id)
        self.assertNotIn("private test", detail.model_dump_json())

        # 查看者维度：资料 owner 自己也看不到（撤销发布对所有人不可访问）。
        as_owner = await requests.request_detail(self.db, self.request_id, viewer=self.owner)
        self.assertFalse(as_owner.resolved_resource_accessible)

    async def test_private_resolution_depends_on_viewer_visibility(self):
        await self.fulfill()
        self.policy.metadata_scope = "private"
        await self.db.flush()

        anonymous = await requests.request_detail(self.db, self.request_id)
        self.assertFalse(anonymous.resolved_resource_accessible)
        self.assertIsNone(anonymous.resolved_resource_title)

        # 资源 owner 可见性内：仍能拿到摘要（查看者按本人权限判定）。
        as_owner = await requests.request_detail(
            self.db, self.request_id, viewer=self.owner
        )
        self.assertTrue(as_owner.resolved_resource_accessible)
        self.assertEqual(as_owner.resolved_resource_title, "private test")

        # 其他登录用户不在可见性内：不可访问，不泄露标题。
        as_other = await requests.request_detail(
            self.db, self.request_id, viewer=self.other
        )
        self.assertFalse(as_other.resolved_resource_accessible)
        self.assertIsNone(as_other.resolved_resource_title)

    async def test_published_resolution_keeps_summary(self):
        await self.fulfill()
        detail = await requests.request_detail(self.db, self.request_id)
        self.assertTrue(detail.resolved_resource_accessible)
        self.assertTrue(detail.resolution_available)
        self.assertEqual(detail.resolved_resource_id, self.resource.id)
        self.assertEqual(detail.resolved_resource_title, "private test")

    async def test_requester_can_reopen_fulfilled_request(self):
        await self.fulfill()
        reopened = await requests.reopen(
            self.db,
            user=self.owner,
            request_id=self.request_id,
            payload=RequestReopen(
                expected_revision=2, reason="原解决资料已不可访问，复核后重开"
            ),
            request_trace_id="beta11",
        )
        self.assertEqual(reopened.status, "open")
        self.assertIsNone(reopened.resolved_resource_id)
        self.assertFalse(reopened.resolved_resource_accessible)
        self.assertEqual(reopened.revision, 3)

        row = await requests.get_request(self.db, self.request_id)
        self.assertEqual(row.status, "open")
        self.assertIsNone(row.resolved_resource_id)
        self.assertEqual(row.revision, 3)
        self.assertEqual(row.handled_by, self.owner.id)
        self.assertIsNotNone(row.handled_at)

        audits = list(
            (
                await self.db.scalars(
                    select(AuditLog).where(AuditLog.action == "request.reopened")
                )
            ).all()
        )
        self.assertEqual(len(audits), 1)
        audit = audits[0]
        self.assertEqual(audit.actor_id, self.owner.id)
        self.assertEqual(audit.before_state["status"], "fulfilled")
        self.assertEqual(audit.after_state["status"], "open")
        self.assertEqual(audit.reason, "原解决资料已不可访问，复核后重开")
        self.assertNotIn("private test", str(audit.after_state))

        event = await self.db.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == "request.updated",
                DomainEvent.dedupe_key == f"request.reopen:{self.request_id}:3",
            )
        )
        self.assertIsNotNone(event)
        # payload 只放需求 ID 与修订号，不复制资源标题、不新增事件类型。
        self.assertEqual(set(event.payload.keys()), {"request_id", "revision"})
        self.assertEqual(event.payload["request_id"], str(self.request_id))
        self.assertEqual(event.payload["revision"], 3)

    async def test_closed_request_requires_admin_to_reopen(self):
        await requests.change_status(
            self.db,
            user=self.owner,
            request_id=self.request_id,
            payload=RequestStatusUpdate(
                status="closed", expected_revision=1, reason="暂时关闭"
            ),
            request_trace_id="beta11",
        )
        with self.assertRaises(Forbidden):
            await requests.reopen(
                self.db,
                user=self.owner,
                request_id=self.request_id,
                payload=RequestReopen(expected_revision=2, reason="重新寻找"),
                request_trace_id="beta11",
            )
        self.other.role = "admin"
        await self.db.flush()
        reopened = await requests.reopen(
            self.db,
            user=self.other,
            request_id=self.request_id,
            payload=RequestReopen(expected_revision=2, reason="重新寻找"),
            request_trace_id="beta11",
        )
        self.assertEqual(reopened.status, "open")
        self.assertEqual(reopened.revision, 3)

    async def test_admin_can_reopen(self):
        await self.fulfill()
        self.other.role = "admin"
        await self.db.flush()
        reopened = await requests.reopen(
            self.db,
            user=self.other,
            request_id=self.request_id,
            payload=RequestReopen(expected_revision=2, reason="复核"),
            request_trace_id="beta11",
        )
        self.assertEqual(reopened.status, "open")
        audit = await self.db.scalar(
            select(AuditLog).where(AuditLog.action == "request.reopened")
        )
        self.assertEqual(audit.actor_id, self.other.id)

    async def test_non_requester_non_admin_cannot_reopen(self):
        await self.fulfill()
        with self.assertRaises(Forbidden):
            await requests.reopen(
                self.db,
                user=self.other,
                request_id=self.request_id,
                payload=RequestReopen(expected_revision=2, reason="越权"),
                request_trace_id="beta11",
            )

    async def test_open_request_cannot_be_reopened(self):
        with self.assertRaises(Conflict):
            await requests.reopen(
                self.db,
                user=self.owner,
                request_id=self.request_id,
                payload=RequestReopen(expected_revision=1, reason="无需重开"),
                request_trace_id="beta11",
            )

    async def test_stale_revision_conflicts(self):
        await self.fulfill()
        with self.assertRaises(Conflict) as ctx:
            await requests.reopen(
                self.db,
                user=self.owner,
                request_id=self.request_id,
                payload=RequestReopen(expected_revision=1, reason="版本过期"),
                request_trace_id="beta11",
            )
        self.assertEqual(ctx.exception.code, "STALE_REVISION")

    async def test_reopen_notifies_followers_via_existing_event_chain(self):
        self.db.add(
            ResourceRequestFollow(request_id=self.request_id, user_id=self.other.id)
        )
        await self.fulfill()
        await requests.reopen(
            self.db,
            user=self.owner,
            request_id=self.request_id,
            payload=RequestReopen(expected_revision=2, reason="复核后重开"),
            request_trace_id="beta11",
        )
        event = await self.db.scalar(
            select(DomainEvent).where(
                DomainEvent.dedupe_key == f"request.reopen:{self.request_id}:3"
            )
        )
        event_id = event.id
        await self.db.commit()
        self.assertTrue(await notifications._process_event(self.db, event_id))
        rows = list((await self.db.scalars(select(Notification))).all())
        self.assertEqual(
            {row.user_id for row in rows}, {self.owner.id, self.other.id}
        )
        self.assertTrue(all("private test" not in row.body for row in rows))

    async def test_batch_list_resolves_once_with_mixed_availability(self):
        second = await service.create_request(
            self.db,
            user_id=self.owner.id,
            course_id=self.course.id,
            scope=None,
            description="需要第二份笔记",
        )
        await self.fulfill(request_id=self.request_id, resource_id=self.resource.id)
        await requests.change_status(
            self.db,
            user=self.owner,
            request_id=second.request.id,
            payload=RequestStatusUpdate(
                status="fulfilled",
                expected_revision=1,
                resource_id=self.resource2.id,
                reason="核实后解决",
            ),
            request_trace_id="beta11",
        )
        # 第一个解决资源撤销发布，第二个仍公开。
        self.resource.publication_status = "withdrawn"
        await self.db.flush()

        original = requests.public_summaries
        calls: list[int] = []

        async def counting(db, pairs, user=None):
            calls.append(len(pairs))
            return await original(db, pairs, user=user)

        with patch.object(requests, "public_summaries", counting):
            page = await service.list_requests(
                self.db,
                course_id=None,
                status=None,
                cursor=None,
                limit=None,
                viewer=None,
            )
        # 批量列表一次解析全部解决资源，不逐条查询（N+1 防护）。
        self.assertEqual(len(calls), 1)
        by_id = {str(item.id): item for item in page.items}
        first, second_item = by_id[str(self.request_id)], by_id[str(second.request.id)]
        self.assertFalse(first.resolved_resource_accessible)
        self.assertIsNone(first.resolved_resource_title)
        self.assertTrue(second_item.resolved_resource_accessible)
        self.assertEqual(second_item.resolved_resource_title, "second note")

    async def test_http_reopen_contract(self):
        await self.fulfill()

        async def database():
            yield self.db

        async def other_user():
            return self.other

        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:3000",
                headers={"Origin": "http://localhost:3000", "X-CSRF-Token": "test"},
                cookies={settings.CSRF_COOKIE_NAME: "test"},
            ) as client:
                path = f"/api/v1/requests/{self.request_id}/reopen"
                payload = {"expected_revision": 2, "reason": "复核后重开"}
                # 匿名：401。
                self.assertEqual((await client.post(path, json=payload)).status_code, 401)
                # 非发起者非管理员：403。
                app.dependency_overrides[get_current_user] = other_user
                self.assertEqual((await client.post(path, json=payload)).status_code, 403)
                app.dependency_overrides.clear()
                app.dependency_overrides[get_db] = database

                async def owner_user():
                    return self.owner

                app.dependency_overrides[get_current_user] = owner_user
                response = await client.post(path, json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(body["status"], "open")
                self.assertIsNone(body["resolved_resource_id"])
                self.assertFalse(body["resolved_resource_accessible"])
                self.assertEqual(body["revision"], 3)
                # 已是打开状态：409。
                conflict = await client.post(path, json={**payload, "expected_revision": 3})
                self.assertEqual(conflict.status_code, 409)
                self.assertEqual(conflict.json()["error"]["code"], "CONFLICT")
                # 版本不匹配：409 STALE_REVISION。
                await requests.change_status(
                    self.db,
                    user=self.owner,
                    request_id=self.request_id,
                    payload=RequestStatusUpdate(
                        status="fulfilled",
                        expected_revision=3,
                        resource_id=self.resource2.id,
                        reason="再次解决",
                    ),
                    request_trace_id="beta11",
                )
                stale = await client.post(path, json={**payload, "expected_revision": 3})
                self.assertEqual(stale.status_code, 409)
                self.assertEqual(stale.json()["error"]["code"], "STALE_REVISION")
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
