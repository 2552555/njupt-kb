"""预览与传输安全（A 块）：短期预览 URL 与水印防篡改令牌（隔离 SQLite）。

仅验证本地可执行的业务逻辑：
- issue_preview_grant 使用 PREVIEW_URL_TTL_SECONDS 短时凭据、grant.valid_until 落在 ~300s 窗口；
- compute_watermark_token / verify_watermark_token 的正确与篡改判定；
- 不同用户的水印令牌互不相同。
真实 S3 预签名由 AsyncMock 替代（本机无 S3/MinIO），不构成生产联调结论。
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

import test_content_workflow as workflow
from app.core.errors import NotFound
from app.core.enums import DerivativeKind, DerivativeStatus, FilePurpose, ScanStatus
from app.core.storage import PresignedDownload
from app.modules.access import service as access_service
from app.modules.access.schemas import PreviewGrantResult
from app.modules.access.watermark_integrity import (
    compute_watermark_token,
    verify_watermark_token,
)
from app.modules.content import service as content_service
from app.modules.content.models import FileDerivative, ResourceFile, VersionFile


def _to_utc(dt: datetime) -> datetime:
    """归一化到 UTC（SQLite 回读可能丢失时区信息），便于安全比较。"""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class SecurityPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        # 发布资源：owner 即作者，authorize 走 bypass 通道（仍可校验文件安全状态）。
        self.resource.publication_status = "published"
        self.resource.published_version_id = self.version.id
        self.version.review_status = "approved"
        # 关联主文件且已通过安全扫描。
        self.file = ResourceFile(
            uploader_id=self.owner.id,
            storage_key="test-preview-key",
            original_name="lecture.pdf",
            scan_status=ScanStatus.PASSED.value,
        )
        self.db.add(self.file)
        await self.db.flush()
        self.db.add(
            VersionFile(
                version_id=self.version.id,
                file_id=self.file.id,
                purpose=FilePurpose.PRIMARY.value,
                display_order=0,
            )
        )
        # 回退预览：受限预览 PDF 已就绪（ensure_watermarked_preview 被模拟失败时走此分支）。
        self.db.add(
            FileDerivative(
                source_file_id=self.file.id,
                processor_version="derive/1",
                kind=DerivativeKind.PREVIEW_PDF.value,
                status=DerivativeStatus.READY.value,
                storage_key="preview-pdf-key",
            )
        )
        await self.db.commit()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def test_issue_preview_grant_uses_preview_ttl(self):
        """preview_url 对应的 grant.valid_until 落在 PREVIEW_URL_TTL_SECONDS 窗口内。"""
        with (
            patch.object(
                access_service.storage,
                "presign_preview_download",
                new=AsyncMock(
                    return_value=PresignedDownload(
                        url="https://example.test/preview",
                        expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
                    )
                ),
            ),
            patch.object(
                content_service,
                "ensure_watermarked_preview",
                new=AsyncMock(side_effect=NotFound("no watermark")),
            ),
        ):
            result = await access_service.issue_preview_grant(
                self.db,
                user=self.owner,
                resource_id=self.resource.id,
                version_id=self.version.id,
                request_id="sec-preview",
            )
        self.assertIsInstance(result, PreviewGrantResult)
        self.assertIsNotNone(result.watermark_token)
        grant = (
            await self.db.execute(
                select(access_service.PreviewGrant).where(
                    access_service.PreviewGrant.id == result.grant_id
                )
            )
        ).scalar_one()
        now = _to_utc(datetime.now(timezone.utc))
        valid = _to_utc(grant.valid_until)
        delta = abs((valid - now).total_seconds())
        self.assertLessEqual(delta, 300 + 5)
        self.assertGreaterEqual(delta, 300 - 5)

    def test_watermark_token_verifies_and_rejects_tamper(self):
        token = compute_watermark_token(
            user_id="u1", resource_id="r1", version_id="v1"
        )
        self.assertTrue(
            verify_watermark_token(token, user_id="u1", resource_id="r1", version_id="v1")
        )
        # 篡改任一入参都应失败。
        self.assertFalse(
            verify_watermark_token(token, user_id="u2", resource_id="r1", version_id="v1")
        )
        self.assertFalse(
            verify_watermark_token(token, user_id="u1", resource_id="rX", version_id="v1")
        )
        self.assertFalse(
            verify_watermark_token(token, user_id="u1", resource_id="r1", version_id="vX")
        )

    def test_watermark_tokens_differ_across_users(self):
        t_u1 = compute_watermark_token(
            user_id="u1", resource_id="r1", version_id="v1"
        )
        t_u2 = compute_watermark_token(
            user_id="u2", resource_id="r1", version_id="v1"
        )
        self.assertNotEqual(t_u1, t_u2)


if __name__ == "__main__":
    unittest.main()
