"""beta15 三级 RBAC 与资源级隔离测试（用户权责体系 §3、§15.2、§19 T02）。

复用 `test_content_workflow.ContentWorkflowTests` 的内存 SQLite 脚手架（owner/other
两个 contributor、课程、资源、草稿版本），另建 admin 与 manager 两个账号。
JSONB 在 SQLite 已编译为 JSON；`async_session_factory` 为 autoflush=False，连续调用
服务后手动 `await db.flush()`。
"""

import unittest

import httpx

import test_content_workflow as workflow  # noqa: F401  注册 JSONB->JSON 编译
from app.core.enums import AssignmentCapability
from app.core.errors import InvalidInput, NotFound
from app.core.permissions import RESOURCE_REVIEW, effective_permissions
from app.modules.assignment import service as assignment
from app.modules.assignment.schemas import (
    PermissionAssignmentCreate,
    ResourceAssignmentCreate,
)
from app.modules.content import management
from app.modules.content import service as content
from app.modules.identity.models import User


class Beta15RbacTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 复用既有脚手架：engine/db/owner/other/school/course/resource/version。
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.admin = User(
            email="admin@example.test",
            password_hash="unused",
            role="admin",
            status="active",
        )
        self.manager = User(
            email="manager@example.test",
            password_hash="unused",
            role="contributor",
            status="active",
        )
        self.db.add_all([self.admin, self.manager])
        await self.db.flush()
        await self.db.commit()

    async def asyncTearDown(self):
        await workflow.ContentWorkflowTests.asyncTearDown(self)

    async def _grant_permission(self, permission=RESOURCE_REVIEW, **kwargs):
        row = await assignment.create_permission_assignment(
            self.db,
            actor=self.admin,
            payload=PermissionAssignmentCreate(
                user_id=self.manager.id,
                permission=permission,
                reason="测试授予",
                **kwargs,
            ),
            request_id="test",
        )
        await self.db.flush()
        return row

    async def _assign_resource(self, **kwargs):
        row = await assignment.create_resource_assignment(
            self.db,
            actor=self.admin,
            payload=ResourceAssignmentCreate(
                user_id=self.manager.id,
                capability=AssignmentCapability.MAINTAIN,
                reason="测试分配",
                **kwargs,
            ),
            request_id="test",
        )
        await self.db.flush()
        return row

    # --- 授予 / 生效 / 过期 / 撤销 ---
    async def test_permission_grant_takes_effect(self):
        await self._grant_permission()
        self.assertIn(RESOURCE_REVIEW, await effective_permissions(self.db, self.manager))

    async def test_permission_valid_until_expiry_disables(self):
        from datetime import datetime, timedelta, timezone

        row = await self._grant_permission(
            valid_until=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        self.assertIn(RESOURCE_REVIEW, await effective_permissions(self.db, self.manager))
        # 到期后失效（不改表结构，直接推进到期时间）。
        row.valid_until = datetime.now(timezone.utc) - timedelta(hours=1)
        await self.db.flush()
        self.assertNotIn(RESOURCE_REVIEW, await effective_permissions(self.db, self.manager))

    async def test_permission_revoke_disables(self):
        row = await self._grant_permission()
        self.assertIn(RESOURCE_REVIEW, await effective_permissions(self.db, self.manager))
        await assignment.revoke_permission_assignment(
            self.db,
            actor=self.admin,
            assignment_id=row.id,
            reason="收回权限",
            request_id="test",
        )
        await self.db.flush()
        self.assertNotIn(RESOURCE_REVIEW, await effective_permissions(self.db, self.manager))

    async def test_regrant_reuses_row_instead_of_duplicate(self):
        first = await self._grant_permission()
        await assignment.revoke_permission_assignment(
            self.db,
            actor=self.admin,
            assignment_id=first.id,
            reason="收回",
            request_id="test",
        )
        await self.db.flush()
        again = await self._grant_permission()
        # 唯一约束覆盖已撤销记录，重新授予必须复用同一行。
        self.assertEqual(again.id, first.id)
        self.assertIn(RESOURCE_REVIEW, await effective_permissions(self.db, self.manager))

    async def test_unknown_permission_is_rejected(self):
        with self.assertRaises(InvalidInput):
            await self._grant_permission(permission="not.a.real.permission")

    # --- 非管理员不能授予/撤销（403）---
    async def test_non_admin_cannot_grant_or_revoke_via_api(self):
        from app.config import settings
        from app.core.deps import get_current_user
        from app.db import get_db
        from app.main import app

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
                body = {
                    "user_id": str(self.manager.id),
                    "permission": RESOURCE_REVIEW,
                    "reason": "尝试越权授予",
                }
                app.dependency_overrides[get_current_user] = lambda: self.manager
                denied = await client.post("/api/v1/admin/permission-assignments", json=body)
                self.assertEqual(denied.status_code, 403, denied.text)

                app.dependency_overrides[get_current_user] = lambda: self.admin
                created = await client.post("/api/v1/admin/permission-assignments", json=body)
                self.assertEqual(created.status_code, 201, created.text)
                assignment_id = created.json()["id"]

                app.dependency_overrides[get_current_user] = lambda: self.manager
                denied = await client.post(
                    f"/api/v1/admin/permission-assignments/{assignment_id}/revoke",
                    json={"reason": "尝试越权撤销"},
                )
                self.assertEqual(denied.status_code, 403, denied.text)

                app.dependency_overrides[get_current_user] = lambda: self.admin
                revoked = await client.post(
                    f"/api/v1/admin/permission-assignments/{assignment_id}/revoke",
                    json={"reason": "收回"},
                )
                self.assertEqual(revoked.status_code, 200, revoked.text)
        finally:
            app.dependency_overrides.clear()

    # --- §19 T02：资料管理员改他人 resource_id，列表/详情/版本全部拒绝 ---
    async def test_manager_cannot_access_unassigned_resource(self):
        await workflow.ContentWorkflowTests.publish_private(self)
        self.assertEqual(
            (await management.list_my_resources(self.db, user=self.manager)).items, []
        )
        # 公开列表同样按作用域遮蔽（私有已发布内容不因他人身份出现）。
        self.assertEqual(
            (await content.list_resources(self.db, user=self.manager)).items, []
        )
        with self.assertRaises(NotFound):
            await management.list_versions(
                self.db, user=self.manager, resource_id=self.resource.id
            )
        with self.assertRaises(NotFound):
            await management.get_version_detail(
                self.db,
                user=self.manager,
                resource_id=self.resource.id,
                version_id=self.version.id,
            )
        self.assertFalse(
            await assignment.can_manage_resource(
                self.db, self.manager, self.resource.id, AssignmentCapability.MAINTAIN
            )
        )

    # --- 授予后可管理，撤销后立即不可 ---
    async def test_resource_assignment_grants_then_revoke_stops_immediately(self):
        await workflow.ContentWorkflowTests.publish_private(self)
        row = await self._assign_resource(resource_id=self.resource.id)

        self.assertTrue(
            await assignment.can_manage_resource(
                self.db, self.manager, self.resource.id, AssignmentCapability.MAINTAIN
            )
        )
        self.assertIn(
            self.resource.id,
            await assignment.active_assignment_resource_ids(
                self.db, self.manager, AssignmentCapability.MAINTAIN
            ),
        )
        # 列表 / 详情 / 版本使用同一作用域，结果一致。
        self.assertIn(
            self.resource.id,
            [item.id for item in (await management.list_my_resources(self.db, user=self.manager)).items],
        )
        versions = await management.list_versions(
            self.db, user=self.manager, resource_id=self.resource.id
        )
        self.assertEqual([v.id for v in versions], [self.version.id])
        detail = await management.get_version_detail(
            self.db,
            user=self.manager,
            resource_id=self.resource.id,
            version_id=self.version.id,
        )
        self.assertTrue(detail.can_edit)
        self.assertIn(
            self.resource.id,
            [item.id for item in (await content.list_resources(self.db, user=self.manager)).items],
        )

        await assignment.revoke_resource_assignment(
            self.db,
            actor=self.admin,
            assignment_id=row.id,
            reason="结束指派",
            request_id="test",
        )
        await self.db.flush()
        self.assertFalse(
            await assignment.can_manage_resource(
                self.db, self.manager, self.resource.id, AssignmentCapability.MAINTAIN
            )
        )
        with self.assertRaises(NotFound):
            await management.get_version_detail(
                self.db,
                user=self.manager,
                resource_id=self.resource.id,
                version_id=self.version.id,
            )

    async def test_course_assignment_covers_course_resources(self):
        await self._assign_resource(course_id=self.course.id)
        self.assertIn(
            self.course.id,
            await assignment.active_assignment_course_ids(
                self.db, self.manager, AssignmentCapability.MAINTAIN
            ),
        )
        self.assertTrue(
            await assignment.can_manage_resource(
                self.db, self.manager, self.resource.id, AssignmentCapability.MAINTAIN
            )
        )

    async def test_resource_assignment_requires_exactly_one_target(self):
        with self.assertRaises(InvalidInput):
            await self._assign_resource(resource_id=self.resource.id, course_id=self.course.id)
        with self.assertRaises(InvalidInput):
            await self._assign_resource()

    async def test_resource_assignment_target_must_exist(self):
        import uuid

        with self.assertRaises(NotFound):
            await self._assign_resource(resource_id=uuid.uuid4())

    # --- GET /me/assignments 只返回本人 ---
    async def test_me_assignments_only_returns_own(self):
        await self._grant_permission()
        mine = await assignment.my_assignments(self.db, user=self.manager)
        self.assertEqual([p.permission for p in mine.permissions], [RESOURCE_REVIEW])
        others = await assignment.my_assignments(self.db, user=self.other)
        self.assertEqual(others.permissions, [])
        self.assertEqual(others.resource_assignments, [])

    async def test_me_assignments_api_scoped_to_caller(self):
        from app.core.deps import get_current_user
        from app.db import get_db
        from app.main import app

        await self._grant_permission()
        await self._assign_resource(resource_id=self.resource.id)

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                app.dependency_overrides[get_current_user] = lambda: self.manager
                mine = await client.get("/api/v1/me/assignments")
                self.assertEqual(mine.status_code, 200, mine.text)
                payload = mine.json()
                self.assertEqual([p["permission"] for p in payload["permissions"]], [RESOURCE_REVIEW])
                self.assertEqual(len(payload["resource_assignments"]), 1)

                app.dependency_overrides[get_current_user] = lambda: self.other
                empty = await client.get("/api/v1/me/assignments")
                self.assertEqual(empty.status_code, 200, empty.text)
                self.assertEqual(empty.json()["permissions"], [])
                self.assertEqual(empty.json()["resource_assignments"], [])
        finally:
            app.dependency_overrides.clear()

    async def test_revoked_assignment_absent_from_me_assignments(self):
        row = await self._assign_resource(resource_id=self.resource.id)
        await assignment.revoke_resource_assignment(
            self.db,
            actor=self.admin,
            assignment_id=row.id,
            reason="结束",
            request_id="test",
        )
        await self.db.flush()
        mine = await assignment.my_assignments(self.db, user=self.manager)
        self.assertEqual(mine.resource_assignments, [])


if __name__ == "__main__":
    unittest.main()
