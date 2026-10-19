"""Beta14 身份与会话安全：登录态改密、一键退出其他设备、并发会话上限（隔离 SQLite）。

只验证本地可执行的业务逻辑；生产联调（PostgreSQL/Redis）不在本机范围内。
"""

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from unittest.mock import patch

from app.config import settings
from app import db as database
from app.core.errors import InvalidInput, Unauthenticated
from app.core.security import hash_password, verify_password
from app.modules.identity import service as identity_service
from app.modules.identity.models import Session, User
from app.modules.moderation.models import AuditLog

import test_content_workflow as workflow

OLD_PASSWORD = "old-password-123"
NEW_PASSWORD = "new-password-456"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _Beta14IdentityBase(unittest.IsolatedAsyncioTestCase):
    """复用内容工作流测试的内存库脚手架；本组测试只需要用户与会话两张表。"""

    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.user = User(
            email="beta14@example.test",
            password_hash=hash_password(OLD_PASSWORD),
            role="user",
            status="active",
        )
        self.db.add(self.user)
        await self.db.flush()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    def _session(self, *, issued_at: datetime, revoked: bool = False) -> Session:
        """构造一条会话；issued_at 用于并发上限的淘汰顺序断言。"""
        return Session(
            user_id=self.user.id,
            token_digest=f"digest-{issued_at.isoformat()}-{id(self)}",
            issued_at=issued_at,
            expires_at=issued_at + timedelta(hours=24 * 14),
            last_seen_at=issued_at,
            revoked_at=issued_at if revoked else None,
            revoke_reason="test_revoked" if revoked else None,
            user_agent="test-agent",
            client_ip="127.0.0.1",
        )

    async def _add_sessions(self, count: int, *, revoked: bool = False) -> list[Session]:
        """按 issued_at 递增添加 count 条会话，返回列表（升序）。"""
        base = _now() - timedelta(hours=count + 1)
        rows = [
            self._session(issued_at=base + timedelta(minutes=index), revoked=revoked)
            for index in range(count)
        ]
        self.db.add_all(rows)
        await self.db.flush()
        return rows

    async def _active_ids(self) -> set:
        rows = (
            (
                await self.db.execute(
                    select(Session).where(
                        Session.user_id == self.user.id, Session.revoked_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        return {row.id for row in rows}

    async def _audit_actions(self, action: str) -> list[AuditLog]:
        rows = (
            (await self.db.execute(select(AuditLog).where(AuditLog.action == action)))
            .scalars()
            .all()
        )
        return list(rows)

    async def _login(self, email: str | None = None) -> Session:
        """走 service.login 完整流程，返回新创建的会话记录。"""
        from app.core.security import token_digest

        user, raw_token, _expires_at = await identity_service.login(
            self.db,
            email=email or self.user.email,
            password=OLD_PASSWORD,
            user_agent="login-agent",
            client_ip="127.0.0.1",
            request_id="beta14",
        )
        assert user.id == self.user.id
        return (
            await self.db.execute(
                select(Session).where(Session.token_digest == token_digest(raw_token))
            )
        ).scalar_one()


class Beta14ChangePasswordTests(_Beta14IdentityBase):
    async def test_change_password_revokes_other_sessions(self):
        sessions = await self._add_sessions(3)
        current = sessions[-1]

        result = await identity_service.change_password(
            self.db,
            user=self.user,
            current_session_id=current.id,
            current_password=OLD_PASSWORD,
            new_password=NEW_PASSWORD,
            request_id="beta14",
        )

        self.assertIn("其他设备", result.message)
        await self.db.refresh(self.user)
        self.assertTrue(verify_password(NEW_PASSWORD, self.user.password_hash))

        # 仅保留当前会话；其余按 password_change 撤销。
        self.assertEqual(await self._active_ids(), {current.id})
        for row in sessions[:-1]:
            await self.db.refresh(row)
            self.assertIsNotNone(row.revoked_at)
            self.assertEqual(row.revoke_reason, "password_change")

        audits = await self._audit_actions("identity.password_changed")
        self.assertEqual(len(audits), 1)
        # 只记撤销数量，不记录任何密码内容。
        self.assertEqual(audits[0].after_state, {"revoked_other_sessions": 2})

    async def test_change_password_rejects_wrong_current_password(self):
        sessions = await self._add_sessions(2)
        before_hash = self.user.password_hash

        with self.assertRaises(InvalidInput):
            await identity_service.change_password(
                self.db,
                user=self.user,
                current_session_id=sessions[-1].id,
                current_password="wrong-password-000",
                new_password=NEW_PASSWORD,
                request_id="beta14",
            )

        await self.db.refresh(self.user)
        self.assertEqual(self.user.password_hash, before_hash)
        # 拒绝不改动任何会话。
        self.assertEqual(len(await self._active_ids()), 2)

    async def test_change_password_rejects_weak_new_password(self):
        sessions = await self._add_sessions(1)

        with self.assertRaises(InvalidInput):
            await identity_service.change_password(
                self.db,
                user=self.user,
                current_session_id=sessions[0].id,
                current_password=OLD_PASSWORD,
                new_password="short",
                request_id="beta14",
            )

        await self.db.refresh(self.user)
        self.assertTrue(verify_password(OLD_PASSWORD, self.user.password_hash))


class Beta14RevokeOthersTests(_Beta14IdentityBase):
    async def test_revoke_other_sessions_keeps_current(self):
        sessions = await self._add_sessions(3)
        current = sessions[-1]

        revoked = await identity_service.revoke_other_sessions(
            self.db, user_id=self.user.id, current_session_id=current.id, request_id="beta14"
        )

        self.assertEqual(revoked, 2)
        self.assertEqual(await self._active_ids(), {current.id})
        for row in sessions[:-1]:
            await self.db.refresh(row)
            self.assertEqual(row.revoke_reason, "user_revoked_others")

        audits = await self._audit_actions("identity.other_sessions_revoked")
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].after_state, {"revoked_sessions": 2})

    async def test_revoke_others_without_other_sessions(self):
        current = self._session(issued_at=_now())
        self.db.add(current)
        await self.db.flush()

        revoked = await identity_service.revoke_other_sessions(
            self.db, user_id=self.user.id, current_session_id=current.id, request_id="beta14"
        )

        self.assertEqual(revoked, 0)
        self.assertEqual(await self._active_ids(), {current.id})


class Beta14SessionLimitTests(_Beta14IdentityBase):
    async def test_expired_sessions_do_not_count_towards_active_limit(self):
        sessions = await self._add_sessions(2)
        expired = self._session(issued_at=_now() - timedelta(days=20))
        self.db.add(expired)
        await self.db.flush()
        with patch.object(settings, "SESSION_MAX_ACTIVE_PER_USER", 3):
            await self._login()
        for row in [*sessions, expired]:
            await self.db.refresh(row)
            self.assertIsNone(row.revoked_at)
        self.assertEqual(await self._audit_actions("identity.session_limit_evicted"), [])

    async def test_login_evicts_oldest_over_limit(self):
        old_sessions = await self._add_sessions(3)
        # 预置一条已撤销会话：不应参与计数，也不应被"淘汰"。
        stale = self._session(issued_at=_now() - timedelta(days=30), revoked=True)
        self.db.add(stale)
        await self.db.flush()

        with patch.object(settings, "SESSION_MAX_ACTIVE_PER_USER", 3):
            new_session = await self._login()

        active = await self._active_ids()
        self.assertIn(new_session.id, active)
        # 超限 1 条：仅淘汰 issued_at 最早的旧会话，其余旧会话保留。
        self.assertNotIn(old_sessions[0].id, active)
        self.assertIn(old_sessions[1].id, active)
        self.assertIn(old_sessions[2].id, active)
        self.assertEqual(len(active), 3)

        await self.db.refresh(old_sessions[0])
        self.assertEqual(old_sessions[0].revoke_reason, "session_limit")
        self.assertIsNotNone(old_sessions[0].revoked_at)

        audits = await self._audit_actions("identity.session_limit_evicted")
        self.assertEqual(len(audits), 1)
        # 审计 object_id 为字符串列。
        self.assertEqual(audits[0].object_id, str(old_sessions[0].id))

    async def test_session_limit_zero_means_unlimited(self):
        await self._add_sessions(6)

        with patch.object(settings, "SESSION_MAX_ACTIVE_PER_USER", 0):
            new_session = await self._login()

        # 0 表示不限：旧会话全部保留，新会话也写入。
        self.assertEqual(len(await self._active_ids()), 7)
        self.assertIn(new_session.id, await self._active_ids())

    async def test_under_limit_no_eviction(self):
        await self._add_sessions(2)

        with patch.object(settings, "SESSION_MAX_ACTIVE_PER_USER", 5):
            new_session = await self._login()

        self.assertEqual(len(await self._active_ids()), 3)
        self.assertIn(new_session.id, await self._active_ids())
        self.assertEqual(await self._audit_actions("identity.session_limit_evicted"), [])


class Beta14LoginAuditTests(_Beta14IdentityBase):
    async def test_failed_login_audit_survives_request_rollback(self):
        await self.db.commit()
        user_id = self.user.id
        with (
            patch.object(database, "async_session_factory", self.factory),
            patch.object(identity_service, "async_session_factory", self.factory),
        ):
            dependency = database.get_db()
            request_db = await anext(dependency)
            user = await request_db.get(User, user_id)
            user.email = "uncommitted@example.test"
            try:
                with request_db.no_autoflush:
                    await identity_service.login(
                        request_db, email="missing@example.test", password="wrong-password",
                        user_agent="test", client_ip=None, request_id="failed-login-test",
                    )
            except Unauthenticated as exc:
                with self.assertRaises(Unauthenticated):
                    await dependency.athrow(exc)
            else:
                await dependency.aclose()
                self.fail("wrong credentials must be rejected")
        async with self.factory() as check_db:
            rows = list((await check_db.scalars(select(AuditLog).where(
                AuditLog.action == "identity.login_failed"
            ))).all())
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].request_id, "failed-login-test")
            user = await check_db.get(User, user_id)
            self.assertEqual(user.email, "beta14@example.test")


if __name__ == "__main__":
    unittest.main()
