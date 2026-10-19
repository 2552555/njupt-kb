"""MFA proof uses synthetic secrets and isolated SQLite; no real authenticator/account."""

import base64
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from cryptography.fernet import Fernet

from app.config import settings
from app.core.errors import Conflict, Forbidden, NotEnabled, Unauthenticated
from app.core.security import hash_password, token_digest
from app.modules.identity import mfa
from app.modules.identity.models import Session, UserMFA

import test_content_workflow as workflow


class MFATests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.key = patch.object(
            settings, "MFA_ENCRYPTION_KEY", Fernet.generate_key().decode()
        )
        self.key.start()
        self.addCleanup(self.key.stop)
        self.password = "synthetic-test-password-129!"
        self.owner.password_hash = hash_password(self.password)
        now = datetime.now(timezone.utc)
        self.login = Session(
            user_id=self.owner.id,
            token_digest="a" * 64,
            issued_at=now,
            expires_at=now + timedelta(hours=1),
        )
        self.other_login = Session(
            user_id=self.owner.id,
            token_digest="b" * 64,
            issued_at=now,
            expires_at=now + timedelta(hours=1),
        )
        self.db.add_all([self.login, self.other_login])
        await self.db.commit()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def _enable(self):
        self.setup = await mfa.begin_setup(self.db, self.owner, self.password, "test")
        code = mfa.totp(self.setup["secret"], int(time.time()) // 30)
        self.codes, self.raw = await mfa.confirm_setup(
            self.db, self.owner, self.login, code, "test"
        )
        await self.db.commit()
        return code

    async def test_seed_encrypted_recovery_hashed_and_other_sessions_revoked(self):
        await self._enable()
        row = await self.db.get(UserMFA, self.owner.id)
        self.assertNotIn(self.setup["secret"], row.secret_ciphertext)
        self.assertEqual(len(self.codes), 8)
        self.assertNotIn(self.codes[0], row.recovery_hashes)
        self.assertEqual(token_digest(self.codes[0]), row.recovery_hashes[0])
        await self.db.refresh(self.other_login)
        self.assertIsNotNone(self.other_login.revoked_at)
        self.assertEqual(self.login.token_digest, token_digest(self.raw))

    async def test_setup_password_key_and_expiry(self):
        with self.assertRaises(Forbidden):
            await mfa.begin_setup(self.db, self.owner, "wrong", "test")
        with patch.object(settings, "MFA_ENCRYPTION_KEY", ""):
            with self.assertRaises(NotEnabled):
                await mfa.begin_setup(self.db, self.owner, self.password, "test")
        setup = await mfa.begin_setup(self.db, self.owner, self.password, "test")
        row = await self.db.get(UserMFA, self.owner.id)
        row.setup_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await self.db.flush()
        with self.assertRaises(Conflict):
            await mfa.confirm_setup(
                self.db,
                self.owner,
                self.login,
                mfa.totp(setup["secret"], int(time.time()) // 30),
                "test",
            )

    async def test_used_totp_cannot_be_replayed(self):
        code = await self._enable()
        with self.assertRaises(Unauthenticated):
            await mfa.verify(self.db, self.owner.id, code)
        with self.assertRaises(Unauthenticated) as error:
            await mfa.verify(self.db, self.owner.id, None)
        self.assertEqual(error.exception.code, "MFA_REQUIRED")

    async def test_recovery_code_is_one_use(self):
        await self._enable()
        self.assertTrue(await mfa.verify(self.db, self.owner.id, self.codes[0]))
        await self.db.commit()
        with self.assertRaises(Unauthenticated):
            await mfa.verify(self.db, self.owner.id, self.codes[0])
        row = await self.db.get(UserMFA, self.owner.id)
        self.assertEqual(len(row.recovery_hashes), 7)

    async def test_login_requires_second_factor(self):
        from app.modules.identity import service

        await self._enable()
        with (
            patch.object(service.ratelimit, "check_rate_limit", return_value=True),
            patch.object(service.ratelimit, "reset_rate_limit"),
        ):
            with self.assertRaises(Unauthenticated) as error:
                await service.login(
                    self.db,
                    email=self.owner.email,
                    password=self.password,
                    user_agent=None,
                    client_ip=None,
                    request_id="test",
                )
            self.assertEqual(error.exception.code, "MFA_REQUIRED")
            _, token, _ = await service.login(
                self.db,
                email=self.owner.email,
                password=self.password,
                otp=self.codes[0],
                user_agent=None,
                client_ip=None,
                request_id="test",
            )
        self.assertTrue(token)

    async def test_recent_authentication_and_revoked_session(self):
        await self._enable()
        await mfa.require_recent(self.db, self.owner, self.login)
        self.login.mfa_verified_at = datetime.now(timezone.utc) - timedelta(hours=1)
        with self.assertRaises(Forbidden) as error:
            await mfa.require_recent(self.db, self.owner, self.login)
        self.assertEqual(error.exception.code, "MFA_REAUTH_REQUIRED")
        self.login.revoked_at = datetime.now(timezone.utc)
        await self.db.commit()
        with self.assertRaises(Unauthenticated):
            await mfa.reauthenticate(
                self.db, self.owner, self.login, self.password, self.codes[0], "test"
            )

    async def test_disable_requires_original_factors_and_clears_material(self):
        await self._enable()
        with self.assertRaises(Unauthenticated):
            await mfa.disable(
                self.db, self.owner, self.login, "wrong", self.codes[0], "test"
            )
        await mfa.disable(
            self.db, self.owner, self.login, self.password, self.codes[0], "test"
        )
        row = await self.db.get(UserMFA, self.owner.id)
        self.assertIsNone(row.enabled_at)
        self.assertIsNone(row.secret_ciphertext)
        self.assertEqual(row.recovery_hashes, [])
        self.assertIsNone(self.login.mfa_verified_at)

    async def test_production_sensitive_guard_requires_setup_and_recent_mfa(self):
        from starlette.requests import Request
        from app.core.permissions import require

        self.owner.role = "admin"
        await self.db.commit()
        guard = require("assignment.manage")
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/test",
                "headers": [
                    (
                        b"cookie",
                        f"{settings.SESSION_COOKIE_NAME}=test-session".encode(),
                    ),
                ],
            }
        )
        self.login.token_digest = token_digest("test-session")
        await self.db.commit()
        with patch.object(settings, "ENV", "prod"):
            with self.assertRaises(Forbidden) as error:
                await guard(request=request, user=self.owner, db=self.db)
            self.assertEqual(error.exception.code, "MFA_SETUP_REQUIRED")
        await self._enable()
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/test",
                "headers": [
                    (b"cookie", f"{settings.SESSION_COOKIE_NAME}={self.raw}".encode()),
                ],
            }
        )
        with patch.object(settings, "ENV", "prod"):
            self.assertEqual(
                (await guard(request=request, user=self.owner, db=self.db)).id,
                self.owner.id,
            )
            self.login.mfa_verified_at = datetime.now(timezone.utc) - timedelta(hours=1)
            await self.db.flush()
            with self.assertRaises(Forbidden) as error:
                await guard(request=request, user=self.owner, db=self.db)
            self.assertEqual(error.exception.code, "MFA_REAUTH_REQUIRED")


class TOTPVectorTests(unittest.TestCase):
    def test_rfc6238_sha1_vectors(self):
        seed = base64.b32encode(b"12345678901234567890").decode()
        for moment, expected in [
            (59, "94287082"),
            (1111111109, "07081804"),
            (1111111111, "14050471"),
            (1234567890, "89005924"),
            (2000000000, "69279037"),
            (20000000000, "65353130"),
        ]:
            self.assertEqual(mfa.totp(seed, moment // 30, digits=8), expected)
