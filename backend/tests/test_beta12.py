"""Beta12 工作台、治理与核验契约；SQLite/内存存储不代替生产联调。"""
import asyncio
import hashlib
import io
import tempfile
import unittest
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import select

import test_content_workflow as workflow
from app.config import settings
from app.core.deps import get_current_user
from app.core.enums import ScanStatus
from app.core.errors import Conflict, DependencyUnavailable, Forbidden, InvalidInput, NotFound
from app.core.storage import ObjectSizeExceeded, ObjectStorage
from app.db import get_db
from app.main import app
from app.modules.access import service as access
from app.modules.access.models import AccessPolicy, DownloadGrant
from app.modules.access.schemas import EntitlementCreate
from app.modules.content import derivatives, scanning
from app.modules.content import service as content
from app.modules.content.models import FileDerivative, ResourceFile, ResourceVersion, VersionFile
from app.modules.identity.models import Session, UserProfile
from app.modules.interaction.models import Favorite, Follow, Rating
from app.modules.jobs import service as jobs
from app.modules.jobs.models import Job
from app.modules.moderation import service as moderation
from app.modules.moderation.models import AuditLog
from app.modules.notification.models import Notification
from app.modules.ops import accounts, personal


def pdf_bytes(pages=1, width=200, height=200):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=width, height=height)
    result = io.BytesIO()
    writer.write(result)
    return result.getvalue()


class Beta12DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.resource.publication_status = "published"
        self.resource.published_version_id = self.version.id
        self.version.review_status = "approved"
        self.db.add(AccessPolicy(resource_id=self.resource.id, metadata_scope="public"))
        self.bytes = pdf_bytes()
        self.file = ResourceFile(uploader_id=self.owner.id, original_name="safe.pdf", storage_key="private-snapshot",
            size=len(self.bytes), sha256=hashlib.sha256(self.bytes).hexdigest(), detected_mime=scanning.PDF_MIME, scan_status="passed")
        self.db.add(self.file)
        await self.db.flush()
        await self.db.commit()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def grant(self, user):
        row = DownloadGrant(user_id=user.id, resource_id=self.resource.id, version_id=self.version.id,
            file_id=self.file.id, idempotency_key=str(uuid.uuid4()), request_hash="f" * 64,
            valid_until=datetime.now(timezone.utc) + timedelta(minutes=1))
        self.db.add(row)
        await self.db.flush()
        return row

    async def test_overview_counts_only_current_user_and_creator_facts(self):
        self.db.add_all([Favorite(user_id=self.owner.id, resource_id=self.resource.id),
            Favorite(user_id=self.other.id, resource_id=self.resource.id),
            Notification(user_id=self.owner.id, type="version_published", title="本人通知", body="测试"),
            Notification(user_id=self.other.id, type="version_published", title="他人通知", body="测试"),
            Rating(user_id=self.other.id, resource_id=self.resource.id, version_id=self.version.id, score=4)])
        await self.grant(self.other)
        await self.db.commit()
        result = await personal.overview(self.db, self.owner)
        self.assertEqual((result.resources, result.published, result.favorites, result.unread_notifications), (1, 1, 1, 1))
        self.assertEqual((result.downloads, result.received_download_grants, result.received_ratings, result.average_rating), (0, 1, 1, 4.0))

    async def test_download_history_pagination_and_revoked_visibility(self):
        await self.grant(self.other)
        await self.grant(self.other)
        await self.grant(self.owner)
        await self.db.commit()
        page = await personal.downloads(self.db, self.other, limit=1)
        self.assertTrue(page.has_more)
        self.assertEqual(page.items[0].file_name, "safe.pdf")
        second = await personal.downloads(self.db, self.other, limit=1, cursor=page.next_cursor)
        self.assertFalse(second.has_more)
        self.assertNotEqual(page.items[0].id, second.items[0].id)
        policy = await self.db.scalar(select(AccessPolicy).where(AccessPolicy.resource_id == self.resource.id))
        policy.metadata_scope = "private"
        await self.db.commit()
        hidden = (await personal.downloads(self.db, self.other)).items[0]
        self.assertFalse(hidden.available)
        self.assertIsNone(hidden.resource_id)
        self.assertIsNone(hidden.title)
        self.assertIsNone(hidden.file_name)
        with self.assertRaises(InvalidInput):
            await personal.downloads(self.db, self.other, cursor="bad!")

    async def test_history_does_not_reveal_old_version_after_new_publication(self):
        await self.grant(self.other)
        newer = ResourceVersion(resource_id=self.resource.id, created_by=self.owner.id, version_no=2,
            title="新公开版本", review_status="approved")
        self.db.add(newer)
        await self.db.flush()
        self.resource.published_version_id = newer.id
        await self.db.commit()
        row = (await personal.downloads(self.db, self.other)).items[0]
        self.assertFalse(row.available)
        self.assertIsNone(row.version_no)
        self.assertIsNone(row.title)

    async def test_following_feed_deduplicates_and_filters_before_paging(self):
        self.db.add_all([Follow(user_id=self.other.id, target_type="course", target_id=self.course.id),
            Follow(user_id=self.other.id, target_type="creator", target_id=self.owner.id)])
        await self.db.commit()
        page = await personal.feed(self.db, self.other)
        self.assertEqual(len(page.items), 1)
        self.assertEqual(page.items[0].id, self.resource.id)
        self.assertFalse(page.has_more)
        self.assertFalse((await personal.feed(self.db, self.owner)).items)
        self.resource.publication_status = "withdrawn"
        await self.db.commit()
        self.assertFalse((await personal.feed(self.db, self.other)).items)

    async def test_users_filters_pagination_and_no_sensitive_fields(self):
        self.db.add(UserProfile(user_id=self.other.id, display_name="治理测试"))
        await self.db.commit()
        page = await accounts.list_users(self.db, keyword="治理", status="active")
        self.assertEqual(len(page.items), 1)
        self.assertEqual(page.items[0].id, self.other.id)
        self.assertNotIn("password_hash", page.items[0].model_dump())
        self.assertFalse((await accounts.list_users(self.db, keyword="%_")).items)

    async def test_account_suspend_reinstate_revokes_sessions_and_preserves_audit(self):
        self.owner.role = "admin"
        token = Session(user_id=self.other.id, token_digest="a" * 64, issued_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        self.db.add(token)
        await self.db.commit()
        await moderation.set_account_restriction(self.db, user_id=self.other.id, suspend=True,
            reason="人工复核", until=None, actor=self.owner, request_id="beta12")
        self.assertEqual(self.other.status, "suspended")
        await self.db.refresh(token)
        self.assertIsNotNone(token.revoked_at)
        with self.assertRaises(Conflict):
            await moderation.set_account_restriction(self.db, user_id=self.other.id, suspend=True,
                reason="重复操作", until=None, actor=self.owner, request_id="beta12")
        await moderation.set_account_restriction(self.db, user_id=self.other.id, suspend=False,
            reason="复核通过", until=None, actor=self.owner, request_id="beta12")
        self.assertEqual(self.other.status, "active")
        logs = list((await self.db.scalars(select(AuditLog).where(AuditLog.object_type == "user"))).all())
        self.assertEqual({row.action for row in logs}, {"account.suspend", "account.reinstate"})
        first = await moderation.list_audit_logs(self.db, object_type="user", limit=1)
        second = await moderation.list_audit_logs(self.db, object_type="user", limit=1, cursor=first.next_cursor)
        self.assertTrue(first.has_more)
        self.assertFalse(second.has_more)
        self.assertNotEqual(first.items[0].id, second.items[0].id)
        with self.assertRaises(Forbidden):
            await moderation.set_account_restriction(self.db, user_id=self.owner.id, suspend=True,
                reason="误操作", until=None, actor=self.owner, request_id="beta12")
        self.other.status = "deleted"
        await self.db.flush()
        with self.assertRaises(Conflict):
            await moderation.set_account_restriction(self.db, user_id=self.other.id, suspend=False,
                reason="禁止复活注销账号", until=None, actor=self.owner, request_id="beta12")

    async def test_entitlements_require_existing_target_and_future_expiry(self):
        values = dict(user_id=self.other.id, scope_type="explicit_grant", scope_id=self.resource.id,
            capability="download_original", source_type="admin_grant")
        now = datetime.now(timezone.utc)
        for starts, expires in ((None, now - timedelta(days=1)), (now - timedelta(days=2), now - timedelta(days=1))):
            with self.assertRaises(InvalidInput):
                await access.create_entitlement(self.db, actor=self.owner, payload=EntitlementCreate(**values,
                    starts_at=starts, expires_at=expires), request_id="beta12")
        with self.assertRaises(NotFound):
            await access.create_entitlement(self.db, actor=self.owner,
                payload=EntitlementCreate(**{**values, "scope_id": uuid.uuid4()}), request_id="beta12")
        row = await access.create_entitlement(self.db, actor=self.owner,
            payload=EntitlementCreate(**values, expires_at=now + timedelta(days=1)), request_id="beta12")
        self.assertEqual(row.source_type, "admin_grant")
        await access.revoke_entitlement(self.db, actor=self.owner, entitlement_id=row.id, reason="复核撤销", request_id="beta12")
        self.assertIsNotNone(row.revoked_at)
        await access.create_entitlement(self.db, actor=self.owner, payload=EntitlementCreate(**values), request_id="beta12")
        await self.db.commit()
        first = await access.list_entitlements(self.db, user_id=self.other.id, limit=1, cursor=None)
        second = await access.list_entitlements(self.db, user_id=self.other.id, limit=1, cursor=first.next_cursor)
        self.assertTrue(first.has_more)
        self.assertFalse(second.has_more)
        self.assertNotEqual(first.items[0].id, second.items[0].id)

    async def test_job_retry_cannot_restart_already_queued_work(self):
        job = Job(job_type="file.scan", target_id=str(self.file.id), processor_version="scan/2", status="failed", attempts=5)
        self.db.add(job)
        await self.db.commit()
        result = await jobs.retry_job(self.db, job_id=job.id, actor=self.owner, request_id="beta12")
        self.assertEqual((result.status, result.attempts), ("queued", 0))
        with self.assertRaises(Conflict):
            await jobs.retry_job(self.db, job_id=job.id, actor=self.owner, request_id="beta12")
        self.db.add(Job(job_type="file.scan", target_id=str(uuid.uuid4()), processor_version="scan/2", status="failed"))
        await self.db.commit()
        first = await jobs.list_jobs(self.db, limit=1)
        second = await jobs.list_jobs(self.db, limit=1, cursor=first.next_cursor)
        self.assertTrue(first.has_more)
        self.assertFalse(second.has_more)
        self.assertNotEqual(first.items[0].id, second.items[0].id)

    async def test_digest_mismatch_does_not_render_or_delete_existing_preview(self):
        self.db.add(FileDerivative(source_file_id=self.file.id, processor_version="derive/1", kind="preview_pdf",
            status="ready", storage_key="old-approved-preview"))
        await self.db.commit()

        async def download(key, handle, **kwargs):
            handle.write(b"changed")
            return 7

        with patch.object(content.storage, "download_to_file", download), patch.object(content, "build_derivatives", AsyncMock()) as build:
            self.assertEqual(await content.process_file_derive(self.db, file_id=self.file.id), "failed")
            build.assert_not_awaited()
        ready = await self.db.scalar(select(FileDerivative).where(FileDerivative.status == "ready"))
        self.assertEqual(ready.storage_key, "old-approved-preview")
        self.db.add(VersionFile(version_id=self.version.id, file_id=self.file.id, purpose="primary", display_order=0))
        await self.db.commit()
        async def withdrawn_during_generation(*args, **kwargs):
            self.resource.publication_status = "withdrawn"
            await self.db.flush()
            return None
        with patch.object(content, "ensure_watermarked_preview", withdrawn_during_generation), patch.object(access.storage, "presign_download", AsyncMock()) as sign:
            with self.assertRaises(NotFound):
                await access.issue_preview_grant(self.db, user=self.other, resource_id=self.resource.id,
                    version_id=self.version.id, request_id="beta12")
            sign.assert_not_awaited()

    async def test_ready_derive_is_reused_without_download_or_render(self):
        self.db.add(FileDerivative(source_file_id=self.file.id, processor_version=derivatives.PROCESSOR_VERSION,
            kind="preview_pdf", status="ready", storage_key="approved-preview"))
        await self.db.commit()
        with patch.object(content.storage, "download_to_file", AsyncMock()) as download:
            self.assertEqual(await content.process_file_derive(self.db, file_id=self.file.id), "ready")
            download.assert_not_awaited()

    async def test_http_personal_and_management_permissions(self):
        async def database():
            yield self.db
        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                self.assertEqual((await client.get('/api/v1/me/overview')).status_code, 401)
                async def current():
                    return self.other
                app.dependency_overrides[get_current_user] = current
                for path in ('/me/overview', '/me/downloads', '/me/feed'):
                    self.assertEqual((await client.get('/api/v1' + path)).status_code, 200)
                self.assertEqual((await client.get('/api/v1/admin/users')).status_code, 403)
                self.other.role = "reviewer"
                self.assertEqual((await client.get('/api/v1/admin/users')).status_code, 403)
                self.other.role = "admin"
                response = await client.get('/api/v1/admin/users', params={'limit': 1})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['has_more'])
        finally:
            app.dependency_overrides.clear()


class Beta12PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_promotes_exact_scanned_snapshot_after_upload_object_changes(self):
        original = pdf_bytes()
        objects = {'upload': original}
        async def download(key, target, **kwargs):
            target.write(objects[key])
            return len(objects[key])
        async def scan(path):
            objects['upload'] = b'overwritten after download'
            return scanning.ScanVerdict(ScanStatus.PASSED)
        async def put(key, source, mime, **kwargs):
            objects[key] = source.read()
        with patch.object(scanning.storage, 'download_to_file', download), patch.object(scanning.storage, 'put_file', put), patch.object(scanning, 'get_scan_provider', return_value=MagicMock(scan=scan)):
            result = await scanning.scan_quarantined_object('upload', original_name='safe.pdf', verified_storage_key='frozen')
        self.assertEqual(result.status, ScanStatus.PASSED)
        self.assertEqual(objects['frozen'], original)
        self.assertNotEqual(objects['frozen'], objects['upload'])
        self.assertEqual(result.sha256, hashlib.sha256(objects['frozen']).hexdigest())

    async def test_scanner_and_storage_failure_never_publish_a_snapshot(self):
        original = pdf_bytes()
        async def download(key, target, **kwargs):
            target.write(original)
            return len(original)
        provider = MagicMock(scan=AsyncMock(return_value=scanning.ScanVerdict(ScanStatus.FAILED, 'unavailable')))
        with patch.object(scanning.storage, 'download_to_file', download), patch.object(scanning, 'get_scan_provider', return_value=provider), patch.object(scanning.storage, 'put_file', AsyncMock()) as put:
            result = await scanning.scan_quarantined_object('upload', original_name='safe.pdf', verified_storage_key='frozen')
            self.assertEqual(result.status, ScanStatus.FAILED)
            put.assert_not_awaited()
            provider.scan.return_value = scanning.ScanVerdict(ScanStatus.PASSED)
            put.side_effect = DependencyUnavailable('offline')
            with self.assertRaises(DependencyUnavailable):
                await scanning.scan_quarantined_object('upload', original_name='safe.pdf', verified_storage_key='frozen')

    async def test_storage_stops_at_limit_and_closes_response(self):
        storage = ObjectStorage.__new__(ObjectStorage)
        storage._semaphore = asyncio.Semaphore(1)
        body = io.BytesIO(b'a' * 200)
        storage._client = MagicMock(get_object=MagicMock(return_value={'Body': body}))
        target = io.BytesIO()
        with patch.object(settings, 'MAX_UPLOAD_SIZE_BYTES', 100):
            with self.assertRaises(ObjectSizeExceeded):
                await storage.download_to_file('key', target)
        self.assertTrue(body.closed)
        self.assertLessEqual(len(target.getvalue()), 100)

    async def test_document_image_and_archive_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'test.pdf'
            source.write_bytes(pdf_bytes(3))
            with patch.object(settings, 'MAX_DOCUMENT_PAGES', 2), self.assertRaises(scanning.ParseFailure):
                scanning._pdf_page_count(source)
            picture = root / 'test.png'
            exif = Image.Exif()
            exif[0x010E] = 'private metadata'
            Image.new('RGB', (20, 20)).save(picture, exif=exif)
            with patch.object(settings, 'MAX_IMAGE_PIXELS', 100), self.assertRaises(scanning.ParseFailure):
                scanning._image_page_count(picture)
            thumb = derivatives._image_thumbnail(picture, root)
            with Image.open(thumb.path) as image:
                self.assertFalse(image.getexif())
            for unsafe in ('../outside', '/absolute', 'C:drive', '..\\outside'):
                archive = root / 'test.docx'
                with zipfile.ZipFile(archive, 'w') as output:
                    output.writestr('[Content_Types].xml', '<Types/>')
                    output.writestr('word/document.xml', '<document/>')
                    output.writestr(unsafe, 'x' * 100)
                with self.assertRaises(scanning.ParseFailure):
                    scanning._detect_ooxml(archive)
            with patch.object(settings, 'MAX_ARCHIVE_ENTRIES', 1), self.assertRaises(scanning.ParseFailure):
                scanning._detect_ooxml(archive)
            with patch.object(settings, 'MAX_ARCHIVE_UNPACKED_BYTES', 1), self.assertRaises(scanning.ParseFailure):
                scanning._detect_ooxml(archive)

    async def test_extreme_pdf_aspect_ratio_keeps_thumbnail_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'tall.pdf'
            source.write_bytes(pdf_bytes(width=20, height=10000))
            artifact = derivatives._render_pdf_thumbnail(source, root)
            with Image.open(artifact.path) as image:
                self.assertLessEqual(max(image.size), settings.PREVIEW_THUMBNAIL_WIDTH)


if __name__ == '__main__':
    unittest.main()
