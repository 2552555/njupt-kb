"""Beta10 account deletion and personal data export contracts on isolated SQLite."""

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.enums import AccountStatus
from app.core.errors import Conflict, Forbidden, InvalidInput
from app.core.security import hash_password, token_digest
from app.modules.content.models import Resource
from app.modules.identity import service as identity
from app.modules.identity.models import Session, UserProfile
from app.modules.identity.schemas import ActionMessage
from app.modules.interaction.models import Comment
from app.modules.moderation.models import AuditLog
import test_content_workflow as workflow


class Beta10AccountTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.password = "correct-horse-battery"
        self.owner.password_hash = hash_password(self.password)
        self.owner.email_verified_at = datetime.now(timezone.utc)
        self.db.add(
            UserProfile(
                user_id=self.owner.id,
                display_name="原昵称",
                avatar_url="https://cdn.example.test/a.png",
                gender="male",
                bio="个人简介",
                enrollment_year=2022,
                preferences={"theme": "dark"},
            )
        )
        for index in range(2):
            self.db.add(
                Session(
                    user_id=self.owner.id,
                    token_digest=token_digest(f"token-{index}-{uuid.uuid4()}"),
                    issued_at=datetime.now(timezone.utc),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                )
            )
        self.db.add(
            Comment(resource_id=self.resource.id, user_id=self.owner.id, body="我的评论")
        )
        self.db.add(
            Comment(resource_id=self.resource.id, user_id=self.other.id, body="他人评论")
        )
        await self.db.commit()
        await self.db.refresh(self.owner)

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def _delete(self):
        return await identity.delete_account(
            self.db,
            user=self.owner,
            password=self.password,
            confirm="注销账号",
            request_id="beta10",
        )

    async def test_http_requires_login(self):
        import httpx

        from app.config import settings
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
                deletion = await client.post(
                    "/api/v1/me/deletion",
                    json={"password": self.password, "confirm": "注销账号"},
                )
                self.assertEqual(deletion.status_code, 401, deletion.text)
                export = await client.get("/api/v1/me/export")
                self.assertEqual(export.status_code, 401, export.text)
        finally:
            app.dependency_overrides.clear()

    async def test_wrong_password_is_rejected(self):
        with self.assertRaises(Forbidden):
            await identity.delete_account(
                self.db,
                user=self.owner,
                password="wrong-password",
                confirm="注销账号",
                request_id="beta10",
            )
        await self.db.refresh(self.owner)
        self.assertNotEqual(self.owner.status, AccountStatus.DELETED.value)
        self.assertIsNone(self.owner.deleted_at)

    async def test_wrong_confirm_phrase_is_rejected(self):
        with self.assertRaises(InvalidInput):
            await identity.delete_account(
                self.db,
                user=self.owner,
                password=self.password,
                confirm="注销",
                request_id="beta10",
            )

    async def test_deletion_anonymizes_revokes_and_blocks_login(self):
        result = await self._delete()
        self.assertIsInstance(result, ActionMessage)
        await self.db.commit()
        await self.db.refresh(self.owner)

        self.assertEqual(self.owner.status, AccountStatus.DELETED.value)
        self.assertIsNotNone(self.owner.deleted_at)

        profile = await self.db.scalar(
            select(UserProfile).where(UserProfile.user_id == self.owner.id)
        )
        self.assertEqual(profile.display_name, "已注销用户")
        self.assertIsNone(profile.avatar_url)
        self.assertIsNone(profile.gender)
        self.assertIsNone(profile.bio)
        self.assertIsNone(profile.enrollment_year)
        self.assertEqual(profile.preferences, {})

        active_sessions = await self.db.scalar(
            select(func.count(Session.id)).where(
                Session.user_id == self.owner.id, Session.revoked_at.is_(None)
            )
        )
        self.assertEqual(active_sessions, 0)

        with self.assertRaises(Forbidden):
            await identity.login(
                self.db,
                email=self.owner.email,
                password=self.password,
                user_agent=None,
                client_ip=None,
                request_id="beta10",
            )

    async def test_business_facts_are_retained(self):
        await self._delete()
        await self.db.commit()

        resource = await self.db.get(Resource, self.resource.id)
        self.assertIsNotNone(resource)
        self.assertEqual(resource.owner_id, self.owner.id)

        comments = list(
            (
                await self.db.scalars(
                    select(Comment).where(Comment.resource_id == self.resource.id)
                )
            ).all()
        )
        self.assertEqual(len(comments), 2)
        mine = [row for row in comments if row.user_id == self.owner.id]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0].body, "我的评论")

    async def test_export_contains_only_own_data(self):
        data = await identity.export_my_data(self.db, user=self.owner)

        self.assertEqual(data["account"]["id"], str(self.owner.id))
        bodies = [comment["body"] for comment in data["comments"]]
        self.assertEqual(bodies, ["我的评论"])
        self.assertNotIn("他人评论", bodies)

        self.assertEqual(
            [item["id"] for item in data["resources"]], [str(self.resource.id)]
        )
        for item in data["resources"]:
            self.assertNotIn("storage_key", item)
            self.assertNotIn("sha256", item)
            self.assertNotIn("download_url", item)

        # 会话只导出本人会话，且除 IP / user_agent 外不额外泄露令牌。
        self.assertEqual(len(data["sessions"]), 2)
        for item in data["sessions"]:
            self.assertNotIn("token_digest", item)
        self.assertEqual(data["retention"]["retention_days"], 30)

    async def test_repeated_deletion_is_rejected(self):
        await self._delete()
        await self.db.commit()
        with self.assertRaises(Conflict):
            await self._delete()

    async def test_deleted_account_cannot_export(self):
        await self._delete()
        await self.db.commit()
        with self.assertRaises(Forbidden):
            await identity.export_my_data(self.db, user=self.owner)

    async def test_audit_record_written_without_password(self):
        await self._delete()
        await self.db.commit()

        log = await self.db.scalar(
            select(AuditLog).where(AuditLog.action == "identity.account_deleted")
        )
        self.assertIsNotNone(log)
        self.assertEqual(log.actor_id, self.owner.id)
        self.assertEqual(log.after_state["status"], "deleted")
        self.assertNotIn(self.password, str(log.after_state))
        self.assertNotIn(self.password, str(log.before_state))

    async def test_export_http_sets_attachment(self):
        import httpx

        from app.core.deps import get_current_user
        from app.db import get_db
        from app.main import app

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: self.owner
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/v1/me/export")
                self.assertEqual(response.status_code, 200, response.text)
                disposition = response.headers["content-disposition"]
                self.assertIn("attachment", disposition)
                self.assertIn("njupt-kb-export-", disposition)
                self.assertEqual(
                    response.json()["account"]["id"], str(self.owner.id)
                )
        finally:
            app.dependency_overrides.clear()


if __name__ == "__main__":
    unittest.main()
