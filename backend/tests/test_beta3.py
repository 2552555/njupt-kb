"""Beta3 regressions. Run: python -m unittest discover -s tests -v."""

import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.requests import Request

import app.models_registry  # noqa: F401
from app.core.enums import DerivativeStatus, JobStatus, ScanStatus
from app.core.errors import Forbidden, NotFound
from app.core.middleware import CSRFMiddleware
from app.modules.access import service as access
from app.modules.content import service as content
from app.modules.jobs import service as jobs


def resource_pair():
    now = datetime.now(timezone.utc)
    rid, vid = uuid.uuid4(), uuid.uuid4()
    resource = SimpleNamespace(
        id=rid,
        owner_id=uuid.uuid4(),
        course_id=uuid.uuid4(),
        offering_id=None,
        resource_type="note",
        publication_status="published",
        freshness_status="current",
        published_version_id=vid,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    version = SimpleNamespace(
        id=vid,
        resource_id=rid,
        version_no=1,
        title="private title",
        description="private text",
        change_summary=None,
        review_status="approved",
        preview_disabled=True,
        submitted_at=now,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    return resource, version


class OriginTests(unittest.TestCase):
    def test_normal_user_can_contribute_without_admin_promotion(self):
        from app.core.permissions import (
            permissions_for,
            RESOURCE_SUBMIT,
            RESOURCE_REVISE,
        )

        self.assertTrue(
            {RESOURCE_SUBMIT, RESOURCE_REVISE}.issubset(permissions_for("user"))
        )

    def allowed(self, value, header=b"origin"):
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/auth/login",
                "headers": [(header, value.encode())],
            }
        )
        return CSRFMiddleware._origin_allowed(request)

    def test_origin_requires_exact_host_and_port(self):
        for value in [
            "http://localhost:3000.attacker.invalid",
            "http://localhost:30001",
            "http://localhost:3000@attacker.invalid",
            "null",
        ]:
            with self.subTest(origin=value):
                self.assertFalse(self.allowed(value))

    def test_origin_and_referer_allow_real_site(self):
        self.assertTrue(self.allowed("http://localhost:3000"))
        self.assertTrue(self.allowed("http://localhost:3000/upload", b"referer"))


class AccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_detail_enforces_metadata_policy(self):
        resource, version = resource_pair()
        with (
            patch.object(content, "get_resource", AsyncMock(return_value=resource)),
            patch.object(content, "published_version", AsyncMock(return_value=version)),
            patch.object(
                access, "authorize", AsyncMock(side_effect=NotFound("private"))
            ),
        ):
            with self.assertRaises(NotFound):
                await content.get_resource_detail(
                    AsyncMock(), user=None, resource_id=resource.id
                )

    async def test_cached_download_is_reauthorized_and_resigned(self):
        rid, vid, fid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        user = SimpleNamespace(id=uuid.uuid4())
        payload = {"resource_id": str(rid), "version_id": str(vid), "file_id": str(fid)}
        existing = SimpleNamespace(request_hash=access.idem.request_hash(payload))
        cached = {
            "grant_id": str(uuid.uuid4()),
            "download_url": "https://example.invalid/old",
            "url_expires_at": "2026-01-01T00:00:00Z",
            "file_name": "test.pdf",
            "version_no": 1,
        }
        with (
            patch.object(access.idem, "lookup", AsyncMock(return_value=cached)),
            patch.object(access, "_grant_by_key", AsyncMock(return_value=existing)),
            patch.object(
                access, "_reissue", AsyncMock(return_value="new authorization")
            ) as reissue,
        ):
            result = await access.issue_download_grant(
                None,
                user=user,
                resource_id=rid,
                version_id=vid,
                file_id=fid,
                idempotency_key="stable-key",
                request_id="test",
            )
            self.assertEqual(result, "new authorization")
            reissue.assert_awaited_once()

    async def test_cached_download_cannot_bypass_revocation(self):
        rid, vid, fid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        user = SimpleNamespace(id=uuid.uuid4())
        payload = {"resource_id": str(rid), "version_id": str(vid), "file_id": str(fid)}
        existing = SimpleNamespace(
            request_hash=access.idem.request_hash(payload), status="revoked"
        )
        cached = {
            "grant_id": str(uuid.uuid4()),
            "download_url": "https://example.invalid/old",
            "url_expires_at": "2026-01-01T00:00:00Z",
            "file_name": "test.pdf",
            "version_no": 1,
        }
        with (
            patch.object(access.idem, "lookup", AsyncMock(return_value=cached)),
            patch.object(access, "_grant_by_key", AsyncMock(return_value=existing)),
        ):
            with self.assertRaises(Forbidden):
                await access.issue_download_grant(
                    None,
                    user=user,
                    resource_id=rid,
                    version_id=vid,
                    file_id=fid,
                    idempotency_key="stable-key",
                    request_id="test",
                )

    async def test_image_thumbnail_is_eligible_for_preview(self):
        db = AsyncMock()
        result = MagicMock()
        result.first.return_value = None
        db.execute.return_value = result
        await access._find_preview_derivative(db, source_file_id=uuid.uuid4())
        self.assertIn("'thumbnail'", str(db.execute.await_args.args[0]))

    async def test_author_disabled_preview_is_not_signed(self):
        resource, version = resource_pair()
        with (
            patch.object(access, "authorize", AsyncMock()),
            patch.object(content, "get_version", AsyncMock(return_value=version)),
            patch.object(
                access,
                "_resolve_file",
                AsyncMock(
                    return_value=(
                        None,
                        SimpleNamespace(id=uuid.uuid4(), original_name="test"),
                    )
                ),
            ),
            patch.object(
                access,
                "_find_preview_derivative",
                AsyncMock(return_value=(uuid.uuid4(), "preview")),
            ),
            patch.object(access.storage, "presign_download", AsyncMock()) as sign,
        ):
            with self.assertRaises(NotFound):
                await access.issue_preview_grant(
                    AsyncMock(),
                    user=None,
                    resource_id=resource.id,
                    version_id=version.id,
                    request_id="test",
                )
            sign.assert_not_awaited()


class JobTests(unittest.IsolatedAsyncioTestCase):
    async def check_outcome(self, job_type, outcome):
        db = AsyncMock()
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=db)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        job = SimpleNamespace(job_type=job_type, target_id=str(uuid.uuid4()))
        handler = (
            "process_file_scan" if job_type == "file.scan" else "process_file_derive"
        )
        with (
            patch.object(jobs, "async_session_factory", factory),
            patch.object(jobs, "_claim", AsyncMock(return_value=job)),
            patch.object(content, handler, AsyncMock(return_value=outcome)),
            patch.object(jobs, "_record_failure", AsyncMock(return_value="failed")),
        ):
            self.assertEqual(await jobs.run_job(str(uuid.uuid4())), "failed")
            db.rollback.assert_not_awaited()
            db.commit.assert_awaited_once()

    async def test_scan_rejection_preserves_business_result(self):
        await self.check_outcome("file.scan", ScanStatus.REJECTED)

    async def test_scan_unavailable_preserves_failure_reason(self):
        await self.check_outcome("file.scan", ScanStatus.FAILED)

    async def test_derivative_failure_preserves_failure_record(self):
        await self.check_outcome("file.derive", DerivativeStatus.FAILED)

    async def test_unexpected_task_error_still_rolls_back(self):
        db = AsyncMock()
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=db)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        job = SimpleNamespace(job_type="file.scan")
        with (
            patch.object(jobs, "async_session_factory", factory),
            patch.object(jobs, "_claim", AsyncMock(return_value=job)),
            patch.object(
                jobs, "_execute", AsyncMock(side_effect=RuntimeError("unexpected"))
            ),
            patch.object(
                jobs,
                "_record_failure",
                AsyncMock(return_value=JobStatus.RETRY_WAIT.value),
            ),
        ):
            self.assertEqual(await jobs.run_job(str(uuid.uuid4())), "retry_wait")
            db.rollback.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
