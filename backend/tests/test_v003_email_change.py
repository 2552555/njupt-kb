"""0.0.3 邮箱换绑（双确认）业务逻辑测试（隔离 SQLite）。

只验证本地可执行的业务逻辑：发起校验、令牌确认、会话撤销、并发与频控。
生产联调（SMTP/PostgreSQL）不在本机范围内；邮件发送以 mock 代替，只断言发送动作发生。
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

import test_content_workflow as workflow
from app.config import settings
from app.core.errors import Conflict, InvalidInput, NotEnabled, RateLimited
from app.core.security import hash_password
from app.modules.identity import service as identity_service
from app.modules.identity.models import (
    EmailChangeRequest,
    Session,
    User,
)

NEW_EMAIL = "new-address@example.test"
CURRENT_PASSWORD = "current-secret-123"


class _EmailChangeBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        # 复用脚手架已创建的用户（邮箱 owner@example.test），只设置一个已知密码，
        # 避免重复插入唯一 email 触发约束冲突。
        self.owner.password_hash = hash_password(CURRENT_PASSWORD)
        self.db.add(self.owner)
        await self.db.flush()
        self.user = self.owner
        self.session = Session(
            user_id=self.user.id,
            token_digest="sess-digest-email",
            issued_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=14),
            last_seen_at=datetime.now(timezone.utc),
            user_agent="test-agent",
            client_ip="127.0.0.1",
        )
        self.db.add(self.session)
        await self.db.flush()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    def _request(self, **kwargs):
        payload = {
            "new_email": NEW_EMAIL,
            "password": CURRENT_PASSWORD,
            "user": self.user,
            "session": self.session,
            "request_id": "v003-email",
        }
        payload.update(kwargs)
        return identity_service.request_email_change(self.db, **payload)


class EmailChangeRequestTests(_EmailChangeBase):
    async def test_request_requires_mail_enabled(self):
        with patch.object(settings, "MAIL_ENABLED", False):
            with self.assertRaises(NotEnabled):
                await self._request()
        # 未启用邮件时不写入任何申请行。
        rows = (await self.db.execute(select(EmailChangeRequest))).scalars().all()
        self.assertEqual(len(rows), 0)

    async def test_request_rejects_wrong_password(self):
        with patch.object(settings, "MAIL_ENABLED", True):
            with self.assertRaises(InvalidInput):
                await self._request(password="wrong-password")

    async def test_request_rejects_same_email(self):
        with patch.object(settings, "MAIL_ENABLED", True):
            with self.assertRaises(InvalidInput):
                await self._request(new_email="owner@example.test")

    async def test_request_rejects_taken_email(self):
        self.db.add(
            User(
                email=NEW_EMAIL,
                password_hash=hash_password("x"),
                role="user",
                status="active",
            )
        )
        await self.db.flush()
        with patch.object(settings, "MAIL_ENABLED", True):
            with self.assertRaises(Conflict):
                await self._request()

    async def test_request_creates_row_and_sends_two_mails(self):
        with patch.object(settings, "MAIL_ENABLED", True), patch(
            "app.modules.identity.service._deliver_mail", new=AsyncMock()
        ) as send_mock, patch(
            "app.modules.identity.service.generate_token", return_value="raw-token-abc"
        ):
            result = await self._request()

        self.assertIn("确认邮件", result.message)
        rows = (await self.db.execute(select(EmailChangeRequest))).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].new_email, NEW_EMAIL)
        self.assertEqual(rows[0].consumed_at, None)
        # 新邮箱确认信 + 旧邮箱安全告知，共两封。
        self.assertEqual(send_mock.await_count, 2)
        called_to = {call.kwargs["to"] for call in send_mock.await_args_list}
        self.assertEqual(called_to, {NEW_EMAIL, "owner@example.test"})

    async def test_request_cooldown_blocks_second_within_24h(self):
        with patch.object(settings, "MAIL_ENABLED", True), patch(
            "app.modules.identity.service._deliver_mail", new=AsyncMock()
        ), patch(
            "app.modules.identity.service.generate_token", return_value="raw-1"
        ):
            await self._request()
        with patch.object(settings, "MAIL_ENABLED", True), patch(
            "app.modules.identity.service._deliver_mail", new=AsyncMock()
        ), patch(
            "app.modules.identity.service.generate_token", return_value="raw-2"
        ):
            with self.assertRaises(RateLimited):
                await self._request()


class EmailChangeConfirmTests(_EmailChangeBase):
    async def _seed_request(self, *, token="raw-token-xyz", expires_in_hours=2):
        from app.core.security import token_digest

        row = EmailChangeRequest(
            user_id=self.user.id,
            new_email=NEW_EMAIL,
            token_digest=token_digest(token),
            expires_at=datetime.now(timezone.utc)
            + timedelta(hours=expires_in_hours),
        )
        self.db.add(row)
        await self.db.flush()
        return token

    async def test_confirm_invalid_token(self):
        with self.assertRaises(InvalidInput):
            await identity_service.confirm_email_change(
                self.db, raw_token="does-not-exist", request_id="v003"
            )

    async def test_confirm_expired_token(self):
        token = await self._seed_request(expires_in_hours=-1)
        with self.assertRaises(InvalidInput):
            await identity_service.confirm_email_change(
                self.db, raw_token=token, request_id="v003"
            )

    async def test_confirm_success_changes_email_and_revokes_sessions(self):
        # 再放一条其他会话，确认后也应当被撤销。
        other = Session(
            user_id=self.user.id,
            token_digest="sess-other-email",
            issued_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=14),
            last_seen_at=datetime.now(timezone.utc),
            user_agent="test-agent",
            client_ip="127.0.0.1",
        )
        self.db.add(other)
        await self.db.flush()

        token = await self._seed_request()
        result = await identity_service.confirm_email_change(
            self.db, raw_token=token, request_id="v003"
        )
        self.assertIn("重新登录", result.message)

        await self.db.refresh(self.user)
        self.assertEqual(self.user.email, NEW_EMAIL)
        self.assertIsNotNone(self.user.email_verified_at)

        consumed = (
            await self.db.execute(select(EmailChangeRequest))
        ).scalars().first()
        self.assertIsNotNone(consumed.consumed_at)

        for sess in (self.session, other):
            await self.db.refresh(sess)
            self.assertIsNotNone(sess.revoked_at)

    async def test_confirm_is_one_time(self):
        token = await self._seed_request()
        await identity_service.confirm_email_change(
            self.db, raw_token=token, request_id="v003"
        )
        # 同一令牌第二次确认应失败（已消费）。
        with self.assertRaises(InvalidInput):
            await identity_service.confirm_email_change(
                self.db, raw_token=token, request_id="v003"
            )


if __name__ == "__main__":
    unittest.main()
