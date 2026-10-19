"""Beta11 第一页图片预览与用户水印（prevew.md 五、十二）。

在隔离 SQLite 上验证：
- 真实生成的小 PDF 走生成函数：产出 WebP 派生产物行（kind=watermarked_preview、
  status=ready、storage_key 含用户短码），Pillow 打开验证尺寸/模式；
- 水印按用户：不同用户生成不同行；
- 二次请求复用已有 ready 行，不重复渲染；
- 开关关闭 / 生成失败时回退既有 preview_pdf / thumbnail，不让预览链路失败；
- 无预览授予资格的用户仍被拒绝（权限不回退）。

存储全部用内存打桩，不连接真实 S3。
"""

import io
import hashlib
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from PIL import Image
from sqlalchemy import func, select

import test_content_workflow as workflow
from app.config import settings
from app.core import storage as storage_module
from app.core.enums import DerivativeKind, DerivativeStatus, ScanStatus
from app.core.errors import DependencyUnavailable, Forbidden
from app.core.storage import PresignedDownload
from app.modules.access import service as access
from app.modules.access.models import AccessPolicy
from app.modules.content import service as content
from app.modules.content.derivatives import _apply_watermark
from app.modules.content.models import FileDerivative, ResourceFile, VersionFile

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _make_pdf(pages: int = 2) -> bytes:
    """测试内用 pypdf 生成真实小 PDF（默认 2 页）。"""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


PDF_BYTES = _make_pdf(2)


class Beta11PreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.file = ResourceFile(
            uploader_id=self.owner.id,
            storage_key="test-source",
            original_name="test.pdf",
            detected_mime="application/pdf",
            size=len(PDF_BYTES),
            sha256=hashlib.sha256(PDF_BYTES).hexdigest(),
            scan_status=ScanStatus.PASSED.value,
        )
        self.db.add(self.file)
        await self.db.flush()
        self.db.add(
            VersionFile(
                version_id=self.version.id,
                file_id=self.file.id,
                purpose="primary",
                display_order=0,
            )
        )
        await self.db.flush()
        self.version.review_status = "approved"
        self.resource.published_version_id = self.version.id
        self.resource.publication_status = "published"
        self.policy = AccessPolicy(
            resource_id=self.resource.id,
            metadata_scope="public",
            preview_scope="authenticated",
        )
        self.db.add(self.policy)
        await self.db.commit()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    def _stub_storage(self, *, source_bytes: bytes = PDF_BYTES) -> dict[str, bytes]:
        """内存存储打桩：下载写回 source_bytes，上传记录键与内容，签名返回假 URL。"""
        uploaded: dict[str, bytes] = {}

        async def fake_download(storage_key, target, *, bucket=None):
            target.write(source_bytes)
            return len(source_bytes)

        async def fake_put(storage_key, source, content_type, *, bucket):
            uploaded[storage_key] = source.read()

        async def fake_presign(storage_key, *, bucket, file_name=None, expires_in=None):
            return PresignedDownload(
                url=f"https://storage.test/{storage_key}",
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
            )

        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            patch.object(storage_module.storage, "download_to_file", fake_download)
        )
        stack.enter_context(patch.object(storage_module.storage, "put_file", fake_put))
        stack.enter_context(
            patch.object(storage_module.storage, "presign_download", fake_presign)
        )
        return uploaded

    async def _grant(self, user):
        return await access.issue_preview_grant(
            self.db,
            user=user,
            resource_id=self.resource.id,
            version_id=self.version.id,
            request_id="beta11",
        )

    async def _watermarked_rows(self):
        return list(
            (
                await self.db.scalars(
                    select(FileDerivative).where(
                        FileDerivative.source_file_id == self.file.id,
                        FileDerivative.kind == DerivativeKind.WATERMARKED_PREVIEW.value,
                    )
                )
            ).all()
        )

    async def _watermarked_count(self) -> int:
        return await self.db.scalar(
            select(func.count(FileDerivative.id)).where(
                FileDerivative.source_file_id == self.file.id,
                FileDerivative.kind == DerivativeKind.WATERMARKED_PREVIEW.value,
            )
        )

    # ------------------------------------------------------------------ #
    # 生成与复用
    # ------------------------------------------------------------------ #
    async def test_preview_grant_prefers_watermarked_image_for_user(self):
        uploaded = self._stub_storage()
        grant = await self._grant(self.owner)

        self.assertEqual(grant.preview_type, "image")
        self.assertTrue(grant.preview_url.startswith("https://storage.test/"))

        rows = await self._watermarked_rows()
        # 多页预览：默认渲染前 PREVIEW_IMAGE_MAX_PAGES 页，测试 PDF 只有 2 页。
        expected_pages = min(2, settings.PREVIEW_IMAGE_MAX_PAGES)
        self.assertEqual(len(rows), expected_pages)
        self.assertEqual(sorted(row.page_no for row in rows), list(range(1, expected_pages + 1)))
        self.assertEqual(len({row.storage_key for row in rows}), expected_pages)
        # 返回体逐页签发 URL，第一页仍是 preview_url（旧客户端兼容）。
        self.assertEqual([page.page_no for page in grant.pages], list(range(1, expected_pages + 1)))
        self.assertEqual(grant.pages[0].url, grant.preview_url)
        self.assertEqual(len({page.url for page in grant.pages}), expected_pages)
        self.assertTrue(grant.watermarked)

        row = rows[0]
        self.assertEqual(row.kind, DerivativeKind.WATERMARKED_PREVIEW.value)
        self.assertEqual(row.status, DerivativeStatus.READY.value)
        self.assertEqual(row.page_count, 1)
        # storage_key 含用户短码与页码，用于按用户、按页区分。
        short_code = content.user_short_code(self.owner.id)
        self.assertIn(short_code, row.storage_key)
        self.assertIn("-p1-", row.storage_key)

        # 上传内容确为有效 WebP 图片：尺寸不超限定宽度、模式 RGB（Pillow 验证）。
        self.assertIn(row.storage_key, uploaded)
        with Image.open(io.BytesIO(uploaded[row.storage_key])) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertEqual(image.mode, "RGB")
            self.assertGreater(image.width, 0)
            self.assertGreater(image.height, 0)
            self.assertLessEqual(image.width, settings.PREVIEW_IMAGE_WIDTH)
            # 水印低可见度生效：整页不是纯白。
            self.assertLess(image.getextrema()[0][0], 255)

    async def test_different_users_get_separate_watermarked_rows(self):
        self._stub_storage()
        first = await self._grant(self.owner)
        second = await self._grant(self.other)

        rows = await self._watermarked_rows()
        expected_pages = min(2, settings.PREVIEW_IMAGE_MAX_PAGES)
        # 两个用户 × 每用户 N 页：不同用户、不同页都是独立对象。
        self.assertEqual(len(rows), expected_pages * 2)
        self.assertEqual(len({row.storage_key for row in rows}), expected_pages * 2)

        owner_short = content.user_short_code(self.owner.id)
        other_short = content.user_short_code(self.other.id)
        by_short = {}
        for row in rows:
            for short in (owner_short, other_short):
                if short in row.storage_key:
                    by_short.setdefault(short, []).append(row)
        self.assertEqual(set(by_short), {owner_short, other_short})
        self.assertEqual({len(items) for items in by_short.values()}, {expected_pages})
        # 两个用户各自拿到自己的图（签名 URL 指向各自对象）。
        self.assertNotEqual(first.preview_url, second.preview_url)

    async def test_second_request_reuses_ready_derivative_without_rerender(self):
        self._stub_storage()
        first = await self._grant(self.owner)

        # 复用路径：若再次调用渲染函数，测试立即失败。
        with patch.object(
            content,
            "build_watermarked_pages",
            AsyncMock(side_effect=AssertionError("ready 行不应重复渲染")),
        ):
            second = await self._grant(self.owner)

        self.assertEqual(second.preview_type, "image")
        self.assertEqual(second.preview_url, first.preview_url)
        self.assertEqual(
            await self._watermarked_count(), min(2, settings.PREVIEW_IMAGE_MAX_PAGES)
        )

    # ------------------------------------------------------------------ #
    # 开关与失败回退
    # ------------------------------------------------------------------ #
    async def test_image_disabled_falls_back_to_preview_pdf(self):
        self._stub_storage()
        self.db.add(
            FileDerivative(
                source_file_id=self.file.id,
                processor_version="derive/1",
                kind="preview_pdf",
                status="ready",
                storage_key="preview-pdf-key",
            )
        )
        await self.db.commit()

        with patch.object(settings, "PREVIEW_IMAGE_ENABLED", False):
            grant = await self._grant(self.owner)

        self.assertEqual(grant.preview_type, "pdf")
        self.assertEqual(grant.preview_url, "https://storage.test/preview-pdf-key")
        self.assertEqual(await self._watermarked_count(), 0)

    async def test_watermark_disabled_skips_image_preview(self):
        result = None
        with patch.object(settings, "PREVIEW_WATERMARK_ENABLED", False):
            result = await content.ensure_watermarked_preview(
                self.db, source_file=self.file, user=self.owner
            )
        self.assertIsNone(result)
        self.assertEqual(await self._watermarked_count(), 0)

    async def test_corrupt_source_falls_back_to_preview_pdf(self):
        # 源文件损坏：水印生成失败但不阻断预览，回退既有 preview_pdf。
        uploaded = self._stub_storage(source_bytes=b"%PDF- broken")
        self.db.add(
            FileDerivative(
                source_file_id=self.file.id,
                processor_version="derive/1",
                kind="preview_pdf",
                status="ready",
                storage_key="fallback-pdf",
            )
        )
        await self.db.commit()

        grant = await self._grant(self.owner)

        self.assertEqual(grant.preview_type, "pdf")
        self.assertEqual(grant.preview_url, "https://storage.test/fallback-pdf")
        self.assertEqual(await self._watermarked_count(), 0)
        self.assertEqual(uploaded, {})  # 没有上传任何水印对象

    async def test_builder_failure_falls_back_to_preview_pdf(self):
        self._stub_storage()
        self.db.add(
            FileDerivative(
                source_file_id=self.file.id,
                processor_version="derive/1",
                kind="preview_pdf",
                status="ready",
                storage_key="builder-failed-fallback",
            )
        )
        await self.db.commit()

        with patch.object(
            content, "build_watermarked_pages", AsyncMock(return_value=[])
        ):
            grant = await self._grant(self.owner)

        self.assertEqual(grant.preview_type, "pdf")
        self.assertEqual(grant.preview_url, "https://storage.test/builder-failed-fallback")
        self.assertEqual(await self._watermarked_count(), 0)

    async def test_thumbnail_is_last_fallback(self):
        self._stub_storage()
        self.db.add(
            FileDerivative(
                source_file_id=self.file.id,
                processor_version="derive/1",
                kind="thumbnail",
                status="ready",
                storage_key="thumb-key",
            )
        )
        await self.db.commit()

        with patch.object(settings, "PREVIEW_IMAGE_ENABLED", False):
            grant = await self._grant(self.owner)

        self.assertEqual(grant.preview_type, "thumbnail")
        self.assertEqual(grant.preview_url, "https://storage.test/thumb-key")

    async def test_storage_failures_keep_existing_preview_usable(self):
        self._stub_storage()
        self.db.add(FileDerivative(
            source_file_id=self.file.id,
            processor_version="derive/1",
            kind="preview_pdf",
            status="ready",
            storage_key="existing-preview",
        ))
        await self.db.commit()
        for method in ("download_to_file", "put_file"):
            with self.subTest(method=method), patch.object(
                storage_module.storage, method,
                AsyncMock(side_effect=DependencyUnavailable("存储暂不可用")),
            ):
                grant = await self._grant(self.owner)
                self.assertEqual(grant.preview_type, "pdf")
                self.assertEqual(grant.preview_url, "https://storage.test/existing-preview")
                self.assertEqual(await self._watermarked_count(), 0)

    # ------------------------------------------------------------------ #
    # 边界与权限
    # ------------------------------------------------------------------ #
    async def test_anonymous_gets_platform_watermark_and_office_is_imaged(self):
        self._stub_storage()
        # 匿名用户没有可绑定身份：使用固定平台水印，不退化为无水印。
        anonymous = await content.ensure_watermarked_preview(
            self.db, source_file=self.file, user=None
        )
        self.assertIsNotNone(anonymous)
        self.assertIn("-anon", anonymous.storage_key)
        self.assertEqual(anonymous.page_no, 1)

        # Office 统一图片化：转换器返回 PDF 后走同一条渲染与水印链路。
        from app.modules.content import derivatives

        self.file.detected_mime = DOCX_MIME
        await self.db.flush()
        with patch.object(
            derivatives, "_convert_office", lambda source, workdir: source
        ):
            office = await content.ensure_watermarked_preview(
                self.db, source_file=self.file, user=self.owner
            )
        self.assertIsNotNone(office)
        self.assertEqual(office.kind, DerivativeKind.WATERMARKED_PREVIEW.value)

    async def test_unauthorized_users_cannot_get_preview(self):
        uploaded = self._stub_storage()
        self.policy.preview_scope = "private"
        await self.db.commit()

        with self.assertRaises(Forbidden):
            await self._grant(self.other)
        # 匿名同样被拒（未获授权不放行，水印不扩大权限）。
        with self.assertRaises(Forbidden):
            await self._grant(None)

        self.assertEqual(await self._watermarked_count(), 0)
        self.assertEqual(uploaded, {})  # 没有生成、没有上传任何预览对象

    async def test_watermark_is_tiled_and_low_visibility(self):
        image = Image.new("RGBA", (600, 400), (255, 255, 255, 255))
        watermarked = _apply_watermark(image, "U1A2B3C4 · 2026-10-16")

        self.assertEqual(watermarked.size, (600, 400))
        self.assertEqual(watermarked.mode, "RGBA")
        pixels = list(watermarked.convert("RGB").getdata())
        darkened = sum(1 for r, _g, _b in pixels if r < 250)
        # 平铺水印覆盖了若干像素，但不是整页变黑（低可见度、可追溯）。
        self.assertGreater(darkened, 0)
        self.assertLess(darkened, 600 * 400)


if __name__ == "__main__":
    unittest.main()
