"""Beta5 contracts; database tests use isolated SQLite, not production services."""
import unittest
import uuid

from pydantic import ValidationError
from sqlalchemy import select

from app.core.enums import CorrectionStatus, ResourceType
from app.core.errors import Conflict, Forbidden, NotFound
from app.modules.content.classification import ClassificationIn, classify_name
from app.modules.identity.schemas import MeUpdateRequest
from app.modules.identity import service as identity
from app.modules.interaction import service as interaction
from app.modules.interaction.models import Correction
import test_content_workflow as workflow


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.courses = [
            {"id": "a", "name": "数据结构", "code": "CS1", "aliases": ["数构"]},
            {"id": "b", "name": "数据结构", "code": "CS10", "aliases": []},
        ]

    def test_duplicate_names_require_manual_choice(self):
        result = classify_name("数据结构笔记.pdf", self.courses)
        self.assertIsNone(result["course_id"])
        self.assertEqual(len(result["candidates"]), 2)

    def test_code_boundary_and_fullwidth_normalization(self):
        result = classify_name("ＣＳ１０_期末.pdf", self.courses)
        self.assertEqual(result["course_id"], "b")
        self.assertEqual(result["resource_type"], "exam")

    def test_alias_and_pptx(self):
        result = classify_name("数构_第三章.pptx", self.courses)
        self.assertEqual(result["course_id"], "a")
        self.assertEqual(result["resource_type"], "slides")

    def test_unknown_and_conflicting_types_are_not_guessed(self):
        result = classify_name("其他期末复习.pdf", self.courses)
        self.assertIsNone(result["course_id"])
        self.assertEqual(result["resource_type"], "other")
        self.assertTrue(result["needs_confirmation"])

    def test_batch_limit_and_avatar_protocols(self):
        for names in ([], ["x"] * 51):
            with self.assertRaises(ValidationError):
                ClassificationIn(names=names)
        for url in ("javascript:alert(1)", "data:image/svg+xml,test", "http://example.test/a", "https://u:p@example.test/a"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                MeUpdateRequest(avatar_url=url)
        self.assertEqual(MeUpdateRequest(avatar_url="https://example.test/a.png").avatar_url, "https://example.test/a.png")
        with self.assertRaises(ValidationError):
            MeUpdateRequest(gender="invalid")


class Beta5DatabaseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = workflow.ContentWorkflowTests.asyncSetUp
    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def published(self, private=False):
        from app.modules.access.models import AccessPolicy
        self.resource.publication_status = "published"
        self.resource.published_version_id = self.version.id
        self.version.review_status = "approved"
        self.db.add(AccessPolicy(resource_id=self.resource.id, metadata_scope="private" if private else "public"))
        await self.db.commit()

    async def correction(self):
        return await interaction.create_correction(
            self.db, user_id=self.other.id, resource_id=self.resource.id,
            version_id=self.version.id, description="资料类型应为试卷",
            suggested_resource_type=ResourceType.EXAM,
        )

    async def test_new_profile_roundtrip_and_clear(self):
        # Load profile eagerly just as the authentication dependency does.
        user = await identity.get_user(self.db, self.owner.id)
        result = await identity.update_me(self.db, user=user, payload={
            "display_name": "测试昵称", "gender": "female", "bio": "个人简介",
            "avatar_url": "https://example.test/avatar.png", "enrollment_year": 2025,
        }, request_id="beta5")
        self.assertEqual((result.display_name, result.gender, result.bio), ("测试昵称", "female", "个人简介"))
        await self.db.commit()
        await self.db.refresh(user)
        result = await identity.me_summary(self.db, user=user)
        self.assertEqual(result.enrollment_year, 2025)
        result = await identity.update_me(self.db, user=user, payload={"bio": None, "gender": None, "avatar_url": None}, request_id="beta5")
        self.assertIsNone(result.bio)
        self.assertIsNone(result.gender)
        self.assertIsNone(result.avatar_url)
        self.assertEqual(result.display_name, "测试昵称")

    async def test_avatar_upload_validates_and_stores_public_object(self):
        from io import BytesIO
        from unittest.mock import AsyncMock, patch

        from PIL import Image

        from app.core import storage as storage_module
        from app.core.errors import InvalidInput

        user = await identity.get_user(self.db, self.owner.id)
        with self.assertRaises(InvalidInput):
            await identity.update_avatar(self.db, user=user, data=b"<html>", content_type="text/html", request_id="beta5")
        with self.assertRaises(InvalidInput):
            await identity.update_avatar(self.db, user=user, data=b"", content_type="image/png", request_id="beta5")
        with self.assertRaises(InvalidInput):
            await identity.update_avatar(
                self.db, user=user, data=b"x" * (2 * 1024 * 1024 + 1), content_type="image/png", request_id="beta5"
            )
        # 声明为图片但内容不是可解码图片，必须拒绝，避免任意内容进入公开桶。
        with self.assertRaises(InvalidInput):
            await identity.update_avatar(self.db, user=user, data=b"not-an-image", content_type="image/png", request_id="beta5")

        buffer = BytesIO()
        Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
        with patch.object(storage_module.storage, "put_public_object", new=AsyncMock()) as put, patch.object(
            storage_module.storage, "public_url", return_value="https://cdn.test/avatars/a.png"
        ):
            result = await identity.update_avatar(
                self.db, user=user, data=buffer.getvalue(), content_type="image/png", request_id="beta5"
            )
        self.assertEqual(result.avatar_url, "https://cdn.test/avatars/a.png")
        put.assert_awaited_once()
        self.assertEqual(put.await_args.args[2], "image/png")

    async def test_classification_fix_closes_notifies_audits_and_does_not_replace_file(self):
        from app.core.enums import DomainEventType
        from app.modules.notification import service as notifications
        from app.modules.notification.models import DomainEvent, Notification
        from app.modules.moderation.models import AuditLog
        await self.published()
        correction = await self.correction()
        result = await interaction.apply_classification_fix(
            self.db, correction_id=correction.id, actor_id=self.owner.id,
            expected_revision=1, resolution_note="已核对为试卷", request_id="beta5",
        )
        await self.db.flush()
        self.assertEqual(result.status, CorrectionStatus.FIXED)
        await self.db.refresh(self.resource)
        self.assertEqual(self.resource.resource_type, "exam")
        self.assertEqual(self.resource.revision, 2)
        self.assertEqual(self.resource.published_version_id, self.version.id)
        # 处理结果随业务提交进入 outbox，再由独立消费者投递一次通知。
        await self.db.commit()
        event_id = await self.db.scalar(select(DomainEvent.id).where(
            DomainEvent.event_type == DomainEventType.CORRECTION_RESOLVED.value,
            DomainEvent.subject_id == correction.id,
        ))
        self.assertIsNotNone(event_id)
        async with self.factory() as consumer_db:
            self.assertTrue(await notifications._process_event(consumer_db, event_id))
            self.assertFalse(await notifications._process_event(consumer_db, event_id))
        notices = (await self.db.scalars(select(Notification).where(
            Notification.user_id == self.other.id,
            Notification.type == "correction_result",
        ))).all()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].data["correction_id"], str(correction.id))
        self.assertEqual(notices[0].data["status"], "fixed")
        self.assertTrue((await self.db.execute(select(AuditLog).where(AuditLog.action == "resource.classification_corrected"))).scalars().all())
        with self.assertRaises(Conflict):
            await interaction.apply_classification_fix(self.db, correction_id=correction.id,
                actor_id=self.owner.id, expected_revision=2, resolution_note="重复", request_id="beta5")

    async def test_stale_revision_does_not_resolve_or_modify(self):
        await self.published()
        correction = await self.correction()
        with self.assertRaises(Conflict):
            await interaction.apply_classification_fix(self.db, correction_id=correction.id,
                actor_id=self.owner.id, expected_revision=99, resolution_note="过期", request_id="beta5")
        await self.db.refresh(self.resource)
        saved = await self.db.get(Correction, correction.id)
        self.assertEqual(saved.status, "open")
        self.assertEqual(self.resource.resource_type, "note")

    async def test_private_metadata_does_not_accept_other_users_feedback(self):
        await self.published(private=True)
        with self.assertRaises((NotFound, Forbidden)):
            await self.correction()

    async def test_avatar_endpoint_accepts_multipart_upload(self):
        import httpx
        from io import BytesIO
        from unittest.mock import AsyncMock, patch

        from PIL import Image

        from app.main import app
        from app.config import settings
        from app.core import storage as storage_module
        from app.core.deps import get_current_user
        from app.db import get_db
        from app.modules.identity.router import current_session

        async def database():
            yield self.db

        user = await identity.get_user(self.db, self.owner.id)
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[current_session] = lambda: None
        buffer = BytesIO()
        Image.new("RGB", (8, 8), "blue").save(buffer, format="JPEG")
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000",
                headers={"Origin": "http://localhost:3000", "X-CSRF-Token": "test"},
                cookies={settings.CSRF_COOKIE_NAME: "test"},
            ) as client:
                with patch.object(storage_module.storage, "put_public_object", new=AsyncMock()), patch.object(
                    storage_module.storage, "public_url", return_value="https://cdn.test/avatars/b.jpg"
                ):
                    response = await client.post(
                        "/api/v1/me/avatar", files={"file": ("a.jpg", buffer.getvalue(), "image/jpeg")}
                    )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["avatar_url"], "https://cdn.test/avatars/b.jpg")
                bad = await client.post("/api/v1/me/avatar", files={"file": ("a.txt", b"nope", "text/plain")})
                self.assertEqual(bad.status_code, 422, bad.text)
        finally:
            app.dependency_overrides.clear()

    async def test_admin_only_endpoints_reject_reviewer_and_suspended_admin(self):
        import httpx
        from app.main import app
        from app.core.deps import get_current_user
        from app.db import get_db

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: self.other
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000", headers={"Origin": "http://localhost:3000", "X-CSRF-Token": "test"}, cookies={"csrf_token": "test"}) as client:
                from app.config import settings
                client.cookies.set(settings.CSRF_COOKIE_NAME, "test")
                for role, status in (("user", "active"), ("reviewer", "active"), ("admin", "suspended")):
                    self.other.role, self.other.status = role, status
                    for path, body in (("/admin/imports/classify", {"names": ["CS1笔记.pdf"]}), ("/admin/imports/drafts", {"course_id": str(self.course.id), "title": "t", "resource_type": "note"}), (f"/admin/corrections/{uuid.uuid4()}/classification", {"expected_revision": 1, "resolution_note": "t"})):
                        response = await client.post("/api/v1" + path, json=body)
                        self.assertEqual(response.status_code, 403, response.text)
                self.other.role, self.other.status = "admin", "active"
                response = await client.post("/api/v1/admin/imports/classify", json={"names": ["Test course笔记.pdf"]})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["items"][0]["course_id"], str(self.course.id))
        finally:
            app.dependency_overrides.clear()
