"""预览派生（设计文档 §13.1、§16.2、§17.1）。

- PDF → 首页或少量页（`settings.PREVIEW_MAX_PAGES`）的受限 PDF + 首页缩略图（pypdfium2）。
- PNG/JPEG → 缩略图（Pillow）。
- DOCX/PPTX → 调 `settings.LIBREOFFICE_BIN` 无头转换（临时目录、超时）后再取首页缩略图。
- 转换失败由调用方记录 `FileDerivative.status=failed`，不改变原件审核事实，不假显示“预览就绪”。
- 隐私遮盖、纠错、水印、格式转换都生成派生文件或新版本，不修改原始文件（§16.2）。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.core.enums import DerivativeKind
from app.modules.content.scanning import DOCX_MIME, JPEG_MIME, PDF_MIME, PNG_MIME, PPTX_MIME

PROCESSOR_VERSION = "derive/1"


class DerivativeError(Exception):
    """预览转换失败。"""


@dataclass(frozen=True)
class DerivativeArtifact:
    kind: DerivativeKind
    path: Path
    content_type: str
    page_count: int | None


def primary_kind(detected_mime: str | None) -> DerivativeKind:
    """失败记录使用的主要派生类型。"""
    if detected_mime in (PDF_MIME, DOCX_MIME, PPTX_MIME):
        return DerivativeKind.PREVIEW_PDF
    return DerivativeKind.THUMBNAIL


async def _run_blocking(fn, /, *args):
    return await asyncio.wait_for(
        asyncio.to_thread(fn, *args), timeout=settings.DERIVATIVE_TIMEOUT_SECONDS
    )


def _make_preview_pdf(source: Path, workdir: Path) -> DerivativeArtifact:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(source))
    if reader.is_encrypted:
        raise DerivativeError("PDF 已加密，无法生成预览")
    total = len(reader.pages)
    if total == 0:
        raise DerivativeError("PDF 没有页面")
    limit = min(total, max(1, settings.PREVIEW_MAX_PAGES))
    writer = PdfWriter()
    for index in range(limit):
        writer.add_page(reader.pages[index])
    target = workdir / "preview.pdf"
    with target.open("wb") as handle:
        writer.write(handle)
    return DerivativeArtifact(DerivativeKind.PREVIEW_PDF, target, "application/pdf", limit)


def _render_pdf_thumbnail(source: Path, workdir: Path) -> DerivativeArtifact:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(source))
    if len(document) == 0:
        raise DerivativeError("PDF 没有页面")
    page = document[0]
    width_pt, _ = page.get_size()
    scale = max(0.1, settings.PREVIEW_THUMBNAIL_WIDTH / max(1.0, width_pt))
    image = page.render(scale=scale).to_pil().convert("RGB")
    target = workdir / "thumb.png"
    image.save(target, format="PNG", optimize=True)
    return DerivativeArtifact(DerivativeKind.THUMBNAIL, target, "image/png", 1)


def _image_thumbnail(source: Path, workdir: Path) -> DerivativeArtifact:
    from PIL import Image

    with Image.open(source) as image:
        converted = image.convert("RGB")
        side = settings.PREVIEW_THUMBNAIL_WIDTH
        converted.thumbnail((side, side))
        target = workdir / "thumb.png"
        converted.save(target, format="PNG", optimize=True)
    return DerivativeArtifact(DerivativeKind.THUMBNAIL, target, "image/png", 1)


def _convert_office(source: Path, workdir: Path) -> Path:
    """无头转换为 PDF：受限临时目录、独立用户配置、超时（§16.1）。"""
    profile = workdir / "lo-profile"
    profile.mkdir(exist_ok=True)
    env = {
        "HOME": str(workdir),
        "PATH": os.environ.get("PATH", ""),
        "LANG": "C.UTF-8",
    }
    command = [
        settings.LIBREOFFICE_BIN,
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--nodefault",
        f"-env:UserInstallation=file://{profile}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(workdir),
        str(source),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=settings.DERIVATIVE_TIMEOUT_SECONDS,
            env=env,
            cwd=str(workdir),
            check=False,
        )
    except FileNotFoundError as exc:
        raise DerivativeError(f"未找到转换器 {settings.LIBREOFFICE_BIN}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DerivativeError("转换超时") from exc
    if completed.returncode != 0:
        raise DerivativeError(f"转换失败（返回码 {completed.returncode}）")
    target = workdir / f"{source.stem}.pdf"
    if not target.exists():
        produced = sorted(workdir.glob("*.pdf"))
        if not produced:
            raise DerivativeError("转换未产生 PDF")
        target = produced[0]
    return target


async def _build_pdf(source: Path, workdir: Path) -> list[DerivativeArtifact]:
    preview = await _run_blocking(_make_preview_pdf, source, workdir)
    thumbnail = await _run_blocking(_render_pdf_thumbnail, source, workdir)
    return [preview, thumbnail]


async def build_derivatives(
    source: Path, *, detected_mime: str, workdir: Path
) -> list[DerivativeArtifact]:
    """按真实格式生成受限预览产物；失败抛 `DerivativeError`。"""
    try:
        if detected_mime == PDF_MIME:
            return await _build_pdf(source, workdir)
        if detected_mime in (PNG_MIME, JPEG_MIME):
            return [await _run_blocking(_image_thumbnail, source, workdir)]
        if detected_mime in (DOCX_MIME, PPTX_MIME):
            converted = await _run_blocking(_convert_office, source, workdir)
            return await _build_pdf(converted, workdir)
    except DerivativeError:
        raise
    except asyncio.TimeoutError as exc:
        raise DerivativeError("转换超时") from exc
    except Exception as exc:  # noqa: BLE001 - 统一为派生失败
        raise DerivativeError(f"转换异常：{type(exc).__name__}") from exc
    raise DerivativeError(f"不支持的预览格式：{detected_mime}")
