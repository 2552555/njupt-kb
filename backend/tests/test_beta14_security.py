"""Beta14 管理员安全运营视图（隔离 SQLite；不宣称生产并发结论）。

覆盖 `GET /admin/security/overview` 的三块只读聚合：近 1 小时登录失败 Top（按账号）、
近 24 小时新 IP 登录（Session.client_ip 历史对比）、近 7 天下载速率告警审计条数；
另覆盖无数据时的空结构与 `audit.view` 权限校验。生产联调（PostgreSQL/Redis）不在本机范围。
"""

import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.errors import Forbidden
from app.core.permissions import AUDIT_VIEW, require
from app.modules.identity.models import Session, User
from app.modules.moderation.models import AuditLog
from app.modules.ops import service as ops_service

import test_content_workflow as workflow


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Beta14SecurityOverviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.admin = User(
            email="admin@example.test", password_hash="unused", role="admin", status="active"
        )
        self.plain = User(
            email="plain@example.test", password_hash="unused", role="user", status="active"
        )
        self.db.add_all([self.admin, self.plain])
        await self.db.flush()
        await self.db.commit()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    def _login_failure(self, *, actor_id, at: datetime):
        row = AuditLog(
            actor_id=actor_id,
            action="identity.login_failed",
            object_type="user",
            object_id=actor_id,
            reason="bad_credentials",
            created_at=at,
        )
        self.db.add(row)
        return row

    def _velocity_audit(self, *, at: datetime):
        row = AuditLog(
            action="security.download_velocity_flagged",
            object_type="user",
            object_id=uuid4(),
            reason="velocity",
            created_at=at,
        )
        self.db.add(row)
        return row

    def _session(self, *, user_id, client_ip: str | None, issued_at: datetime):
        row = Session(
            user_id=user_id,
            token_digest=uuid4().hex + uuid4().hex,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(hours=12),
            last_seen_at=issued_at,
            user_agent="beta14-agent",
            client_ip=client_ip,
        )
        self.db.add(row)
        return row

    # --- 登录失败 Top -------------------------------------------------------
    async def test_login_failure_top_groups_by_account_within_one_hour(self):
        # 窗口内：owner 3 次、plain 2 次、邮箱未注册（actor 为空）1 次；
        # 窗口外：owner 2 小时前 5 次，不得计入。
        for _ in range(3):
            self._login_failure(actor_id=self.owner.id, at=_now() - timedelta(minutes=30))
        for _ in range(2):
            self._login_failure(actor_id=self.plain.id, at=_now() - timedelta(minutes=10))
        self._login_failure(actor_id=None, at=_now() - timedelta(minutes=5))
        for _ in range(5):
            self._login_failure(actor_id=self.owner.id, at=_now() - timedelta(hours=2))
        await self.db.commit()

        result = await ops_service.security_overview(self.db)

        self.assertEqual(result.login_failures_last_hour, 6)
        self.assertEqual(len(result.login_failure_top), 3)
        self.assertEqual(
            result.login_failure_top[0].account, self.owner.email,
        )
        self.assertEqual(result.login_failure_top[0].count, 3)
        self.assertEqual(result.login_failure_top[0].account_id, self.owner.id)
        self.assertIsNotNone(result.login_failure_top[0].last_failed_at)
        self.assertEqual(result.login_failure_top[1].count, 2)
        unknown = result.login_failure_top[2]
        self.assertEqual(unknown.account, "未知账号")
        self.assertIsNone(unknown.account_id)
        self.assertEqual(unknown.count, 1)

    # --- 新 IP 登录 ---------------------------------------------------------
    async def test_new_ip_logins_compares_against_session_history(self):
        # owner 48 小时前从 10.0.0.1 登录（历史 IP）；
        # owner 2 小时前从 10.0.0.2 登录（新 IP）；1 小时前再从 10.0.0.1（旧 IP，不计）；
        # plain 3 小时前从 10.0.0.2（不同用户，算新 IP）；40 小时前的会话不在窗口内。
        self._session(user_id=self.owner.id, client_ip="10.0.0.1", issued_at=_now() - timedelta(hours=48))
        self._session(user_id=self.owner.id, client_ip="10.0.0.2", issued_at=_now() - timedelta(hours=2))
        self._session(user_id=self.owner.id, client_ip="10.0.0.1", issued_at=_now() - timedelta(hours=1))
        self._session(user_id=self.plain.id, client_ip="10.0.0.2", issued_at=_now() - timedelta(hours=3))
        self._session(user_id=self.plain.id, client_ip="10.0.0.9", issued_at=_now() - timedelta(hours=40))
        # client_ip 为空的会话不参与新 IP 判定。
        self._session(user_id=self.plain.id, client_ip=None, issued_at=_now() - timedelta(hours=2))
        await self.db.commit()

        result = await ops_service.security_overview(self.db)

        self.assertEqual(result.new_ip_login_count, 2)
        pairs = {(item.account, item.client_ip) for item in result.new_ip_logins}
        self.assertEqual(
            pairs, {(self.owner.email, "10.0.0.2"), (self.plain.email, "10.0.0.2")}
        )
        owner_item = next(i for i in result.new_ip_logins if i.account == self.owner.email)
        self.assertEqual(owner_item.account_id, self.owner.id)
        self.assertIsNotNone(owner_item.first_seen_at)

    # --- 下载速率告警审计 ----------------------------------------------------
    async def test_download_velocity_flagged_counted_within_seven_days(self):
        self._velocity_audit(at=_now() - timedelta(days=1))
        self._velocity_audit(at=_now() - timedelta(days=6, hours=23))
        # 超出 7 天窗口与其他 action 均不计入。
        self._velocity_audit(at=_now() - timedelta(days=8))
        self.db.add(
            AuditLog(
                action="identity.login",
                object_type="session",
                object_id=uuid4(),
                created_at=_now() - timedelta(days=1),
            )
        )
        await self.db.commit()

        result = await ops_service.security_overview(self.db)

        self.assertEqual(result.download_velocity_flagged_last_7d, 2)

    # --- 空数据 ---------------------------------------------------------------
    async def test_empty_data_returns_empty_structures(self):
        result = await ops_service.security_overview(self.db)

        self.assertEqual(result.login_failures_last_hour, 0)
        self.assertEqual(result.login_failure_top, [])
        self.assertEqual(result.new_ip_login_count, 0)
        self.assertEqual(result.new_ip_logins, [])
        self.assertEqual(result.download_velocity_flagged_last_7d, 0)
        self.assertIsNotNone(result.generated_at)

    # --- 权限 ------------------------------------------------------------------
    async def test_security_overview_requires_audit_view(self):
        from starlette.requests import Request
        request = Request({"type": "http", "method": "GET", "path": "/api/v1/admin/security/overview", "headers": []})
        dependency = require(AUDIT_VIEW)
        # beta15 起 require() 按「角色权限 ∪ 未过期按需授权」判定，需要数据库会话解析授权；
        # 本用例直接调用依赖，因此显式传入会话。
        # 无 audit.view 的普通用户被拒绝（账号状态检查之后的权限判定）。
        with self.assertRaises(Forbidden):
            await dependency(request=request, user=self.plain, db=self.db)
        # 管理员拥有全部权限，放行。
        self.assertIs(await dependency(request=request, user=self.admin, db=self.db), self.admin)
        # 审核员按 §15 权限表持有 audit.view，同样放行。
        reviewer = User(
            email="reviewer@example.test",
            password_hash="unused",
            role="reviewer",
            status="active",
        )
        self.db.add(reviewer)
        await self.db.flush()
        self.assertIs(await dependency(request=request, user=reviewer, db=self.db), reviewer)


if __name__ == "__main__":
    unittest.main()
