"""0.0.3 预览与解析隔离测试。

覆盖本地可确定的部分：

- 多页图片预览：页数上限、页码、独立文件、Office 走同一条图片化链路；
- 匿名预览使用固定平台水印，不退化为无水印；
- 解析沙箱计划：``off`` 不包装、``best-effort`` 如实标注未隔离、``required`` 明确拒绝；
- 子进程环境不继承代理与密钥。

真实 bubblewrap/unshare 隔离、恶意文档样本与内网探测属于 Linux 预发布环境验收，
本文件不宣称已完成（本机为 Windows，无网络命名空间）。
"""

import io
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app.models_registry  # noqa: F401
from app.config import settings
from app.core.enums import DerivativeKind
from app.modules.content import derivatives, sandbox


def _make_pdf(pages: int = 3) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class MultiPagePreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory(prefix="njupt-v003-preview-")
        self.workdir = Path(self.tmp.name)
        self.pdf = self.workdir / "source.pdf"
        self.pdf.write_bytes(_make_pdf(3))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_renders_multiple_pages_with_page_numbers(self):
        pages = await derivatives.build_watermarked_pages(
            self.pdf,
            detected_mime="application/pdf",
            workdir=self.workdir,
            watermark_text="U test",
        )
        self.assertEqual([artifact.page_no for artifact in pages], [1, 2, 3])
        self.assertEqual(len({artifact.path for artifact in pages}), 3)
        for artifact in pages:
            self.assertEqual(artifact.kind, DerivativeKind.WATERMARKED_PREVIEW)
            self.assertTrue(artifact.path.exists())
            self.assertGreater(artifact.path.stat().st_size, 0)

    async def test_page_limit_is_enforced(self):
        with patch.object(settings, "PREVIEW_IMAGE_MAX_PAGES", 2):
            pages = await derivatives.build_watermarked_pages(
                self.pdf,
                detected_mime="application/pdf",
                workdir=self.workdir,
                watermark_text="U test",
            )
        self.assertEqual([artifact.page_no for artifact in pages], [1, 2])

    async def test_office_uses_same_image_pipeline(self):
        docx = self.workdir / "source.docx"
        docx.write_bytes(b"fake-office-bytes")
        with patch.object(
            derivatives, "_convert_office", lambda source, workdir: self.pdf
        ):
            pages = await derivatives.build_watermarked_pages(
                docx,
                detected_mime=(
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                workdir=self.workdir,
                watermark_text="U test",
            )
        self.assertEqual([artifact.page_no for artifact in pages], [1, 2, 3])

    async def test_unsupported_format_returns_empty(self):
        pages = await derivatives.build_watermarked_pages(
            self.pdf,
            detected_mime="application/zip",
            workdir=self.workdir,
            watermark_text="U test",
        )
        self.assertEqual(pages, [])


class SandboxPlanTests(unittest.TestCase):
    def test_off_mode_does_not_wrap(self):
        with patch.object(settings, "PARSE_SANDBOX_MODE", "off"):
            plan = sandbox.plan_command(["true"], workdir=Path("/tmp"))
        self.assertEqual(plan.argv, ["true"])
        self.assertEqual(plan.backend, "none")
        self.assertFalse(plan.network_isolated)
        self.assertIn("关闭", plan.reason or "")

    def test_best_effort_reports_missing_isolation_honestly(self):
        with patch.object(settings, "PARSE_SANDBOX_MODE", "best-effort"):
            plan = sandbox.plan_command(["true"], workdir=Path("/tmp"))
        self.assertFalse(plan.network_isolated)
        self.assertIn(plan.backend, {"rlimit", "none"})
        self.assertTrue(plan.reason)
        # 资源上限始终给出，即使没有命名空间隔离。
        self.assertGreater(plan.cpu_seconds, 0)
        self.assertGreater(plan.memory_bytes, 0)
        self.assertGreater(plan.max_file_bytes, 0)

    def test_required_mode_refuses_without_isolation(self):
        with patch.object(settings, "PARSE_SANDBOX_MODE", "required"):
            with self.assertRaises(sandbox.SandboxUnavailable):
                sandbox.plan_command(["true"], workdir=Path("/tmp"))

    def test_environment_does_not_inherit_secrets_or_proxies(self):
        with TemporaryDirectory() as tmp, patch.object(
            settings, "PARSE_SANDBOX_MODE", "best-effort"
        ):
            plan = sandbox.plan_command([sys.executable, "-c", "print(1)"], workdir=Path(tmp))
        for key in ("SECRET_KEY", "DATABASE_URL", "AI_API_KEY", "SMTP_PASSWORD"):
            self.assertNotIn(key, plan.env)
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            self.assertEqual(plan.env.get(key), "")

    def test_run_sandboxed_executes_and_reports_plan(self):
        with TemporaryDirectory() as tmp, patch.object(
            settings, "PARSE_SANDBOX_MODE", "best-effort"
        ):
            completed, plan = sandbox.run_sandboxed(
                [sys.executable, "-c", "print('sandbox-ok')"], workdir=Path(tmp)
            )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("sandbox-ok", completed.stdout.decode("utf-8", errors="replace"))
        self.assertIn(plan.backend, {"bwrap", "unshare", "rlimit", "none"})


if __name__ == "__main__":
    unittest.main()
