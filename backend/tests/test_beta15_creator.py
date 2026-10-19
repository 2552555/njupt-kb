"""Beta15 贡献者入驻（用户权责体系 §3.4、§14.1、§16.2；隔离 SQLite）。

覆盖：
- 普通用户可直接申请入驻；限频与未关闭申请上限生效；
- 已是贡献者或已有 ACTIVE 申请时重复申请抛 Conflict；
- 审核 active 后 `user.role` 提升为 contributor；rejected 不改变角色；
- 状态机校验（applied 不能直接 active）；理由必填且长度受限；
- 非 `contributor.review` 权限审批返回 403；本人申请列表与后台状态过滤。

复用 `test_content_workflow.ContentWorkflowTests` 的内存 SQLite 脚手架；`autoflush=False`，
连续调用服务后手动 `await db.flush()`。本文件只验证本地业务逻辑，不宣称生产并发结论。
"""

import unittest

import httpx
from sqlalchemy import select

from app.config import settings
from app.core.enums import DomainEventType
from app.core.errors import Conflict, InvalidInput, RateLimited
from app.core.ratelimit import reset_rate_limit
from app.modules.creator import service as creator
from app.modules.creator.models import ContributorApplication
from app.modules.creator.schemas import ContributorApplicationCreate
from app.modules.identity.models import User
from app.modules.moderation.models import AuditLog
from app.modules.notification.models import DomainEvent

import test_content_workflow as workflow  # noqa: F401  注册 JSONB->JSON 编译


def _payload(**overrides) -> ContributorApplicationCreate:
    data = {
        "capability_summary": "长期整理并维护操作系统课程资料",
        "rights_materials_ref": "materials/rights/os-2024.pdf",
    }
    data.update(overrides)
    return ContributorApplicationCreate(**data)


class Beta15CreatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.user = User(
            email="applicant@example.test", password_hash="unused", role="user", status="active"
        )
        self.reviewer = User(
            email="reviewer@example.test",
            password_hash="unused",
            role="reviewer",
            status="active",
        )
        self.db.add_all([self.user, self.reviewer])
        await self.db.flush()
        await self.db.commit()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def _apply(self, *, user=None, **overrides):
        return await creator.submit_application(
            self.db,
            user=user or self.user,
            payload=_payload(**overrides),
            request_id="beta15",
        )

    async def _decide(self, application_id, decision, reason="审核说明", actor=None):
        return await creator.decide_application(
            self.db,
            application_id=application_id,
            actor=actor or self.reviewer,
            decision=decision,
            reason=reason,
            request_id="beta15",
        )

    # --- 申请 ---------------------------------------------------------------

    async def test_plain_user_can_apply_with_audit_and_event(self):
        application = await self._apply()
        await self.db.flush()
        self.assertEqual(application.status, "applied")
        self.assertIsNotNone(application.submitted_at)

        audit = await self.db.scalar(
            select(AuditLog).where(AuditLog.action == "contributor_application.submitted")
        )
        self.assertIsNotNone(audit)
        event = await self.db.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == DomainEventType.CONTRIBUTOR_APPLICATION_SUBMITTED.value
            )
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.subject_id, application.id)

    async def test_rate_limit_and_open_cap(self):
        await reset_rate_limit(f"contributor-application:{self.user.id}")
        await self._apply()  # 第 1 次：成功
        # 默认未关闭申请上限为 1：第 2、3 次在限频窗口内先被上限拦截。
        with self.assertRaises(Conflict):
            await self._apply()
        with self.assertRaises(Conflict):
            await self._apply()
        # 第 4 次：超过 RATE_LIMIT（默认 3）。
        with self.assertRaises(RateLimited):
            await self._apply()

    async def test_active_contributor_cannot_reapply(self):
        self.user.role = "contributor"
        await self.db.flush()
        with self.assertRaises(Conflict):
            await self._apply()

    async def test_user_with_active_application_cannot_reapply(self):
        other = User(
            email="active@example.test", password_hash="unused", role="user", status="active"
        )
        self.db.add(other)
        await self.db.flush()
        from datetime import datetime, timezone

        self.db.add(
            ContributorApplication(
                user_id=other.id,
                status="active",
                capability_summary="x",
                rights_materials_ref="y",
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await self.db.flush()
        with self.assertRaises(Conflict):
            await self._apply(user=other)

    # --- 审核 ---------------------------------------------------------------

    async def test_active_decision_promotes_role(self):
        application = await self._apply()
        await self.db.flush()
        await self._decide(application.id, "under_review", reason="材料齐备，进入审核")
        await self.db.flush()
        result = await self._decide(application.id, "active", reason="能力与材料符合要求")
        await self.db.flush()

        self.assertEqual(result.status, "active")
        self.assertIsNotNone(result.decided_at)
        self.assertIsNotNone(result.closed_at)
        self.assertEqual(result.decided_by, self.reviewer.id)

        await self.db.refresh(self.user)
        self.assertEqual(self.user.role, "contributor")

        decided = await self.db.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type == DomainEventType.CONTRIBUTOR_APPLICATION_DECIDED.value
            )
        )
        self.assertIsNotNone(decided)
        self.assertEqual(decided.payload["status"], "active")
        self.assertEqual(await creator.onboarding_status(self.db, self.user.id), "active")

    async def test_rejected_does_not_change_role(self):
        application = await self._apply()
        await self.db.flush()
        result = await self._decide(application.id, "rejected", reason="权利材料不足，可补充后重新申请")
        await self.db.flush()

        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.decision_reason, "权利材料不足，可补充后重新申请")
        await self.db.refresh(self.user)
        self.assertEqual(self.user.role, "user")
        self.assertEqual(await creator.onboarding_status(self.db, self.user.id), "rejected")

    async def test_invalid_transition_applied_to_active(self):
        application = await self._apply()
        await self.db.flush()
        with self.assertRaises(Conflict):
            await self._decide(application.id, "active")

    async def test_reason_required_and_bounded(self):
        application = await self._apply()
        await self.db.flush()
        with self.assertRaises(InvalidInput):
            await self._decide(application.id, "under_review", reason="   ")
        with self.assertRaises(InvalidInput):
            await self._decide(application.id, "under_review", reason="x" * 256)

    async def test_unknown_decision_rejected(self):
        application = await self._apply()
        await self.db.flush()
        with self.assertRaises(InvalidInput):
            await self._decide(application.id, "suspended")

    # --- 列表 ---------------------------------------------------------------

    async def test_lists_are_scoped_and_filterable(self):
        first = await self._apply()
        await self.db.flush()
        mine = await creator.list_my_applications(
            self.db, user_id=self.user.id, limit=None, cursor=None
        )
        self.assertEqual([item.id for item in mine.items], [first.id])

        other = User(
            email="other2@example.test", password_hash="unused", role="user", status="active"
        )
        self.db.add(other)
        await self.db.flush()
        self.assertEqual(
            (
                await creator.list_my_applications(
                    self.db, user_id=other.id, limit=None, cursor=None
                )
            ).items,
            [],
        )

        filtered = await creator.list_applications(
            self.db, statuses=["rejected"], limit=None, cursor=None
        )
        self.assertEqual(filtered.items, [])
        all_rows = await creator.list_applications(self.db, limit=None, cursor=None)
        self.assertEqual([item.id for item in all_rows.items], [first.id])

    # --- 权限（HTTP 403）----------------------------------------------------

    async def test_non_reviewer_cannot_decide_via_api(self):
        from app.core.deps import get_current_user
        from app.db import get_db
        from app.main import app

        application = await self._apply()
        await self.db.flush()

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:3000",
                headers={"Origin": "http://localhost:3000", "X-CSRF-Token": "test"},
                cookies={settings.CSRF_COOKIE_NAME: "test"},
            ) as client:
                body = {"decision": "under_review", "reason": "受理"}
                app.dependency_overrides[get_current_user] = lambda: self.user
                denied = await client.post(
                    f"/api/v1/admin/contributor-applications/{application.id}/decisions",
                    json=body,
                )
                self.assertEqual(denied.status_code, 403, denied.text)

                app.dependency_overrides[get_current_user] = lambda: self.reviewer
                allowed = await client.post(
                    f"/api/v1/admin/contributor-applications/{application.id}/decisions",
                    json=body,
                )
                self.assertEqual(allowed.status_code, 201, allowed.text)
                self.assertEqual(allowed.json()["status"], "under_review")
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
