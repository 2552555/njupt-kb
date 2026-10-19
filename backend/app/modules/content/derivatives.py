"""预览派生（设计文档 §13.1、§16.2、§17.1；prevew.md 四、五、十二）。

- PDF → 首页或少量页（`settings.PREVIEW_MAX_PAGES`）的受限 PDF + 首页缩略图（pypdfium2）。
- PDF / PNG / JPEG / DOCX / PPTX → 前 N 页水印图片预览（WebP）：只渲染前
  `PREVIEW_IMAGE_MAX_PAGES` 页、限定宽度、低可见度斜向重复水印（prevew.md 五、十二）；
  浏览器拿到的是 page1.webp…pageN.webp，不是完整 PDF 或完整 Office 文档。
- PNG/JPEG → 缩略图（Pillow）。
- DOCX/PPTX → 调 `settings.LIBREOFFICE_BIN` 无头转换（临时目录、超时）后再图片化，
  与 PDF 共用同一条渲染与水印链路（统一图片化，不再直接交付转换出的 PDF）。
- 转换失败由调用方记录 `FileDerivative.status=failed`，不改变原件审核事实，不假显示“预览就绪”。
- 隐私遮盖、纠错、水印、格式转换都生成派生文件或新版本，不修改原始文件（§16.2）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

from app.config import settings
from app.core.enums import DerivativeKind
from app.modules.content.scanning import DOCX_MIME, JPEG_MIME, PDF_MIME, PNG_MIME, PPTX_MIME

PROCESSOR_VERSION = "derive/4"

logger = logging.getLogger("app.content.derivatives")


class DerivativeError(Exception):
    """预览转换失败。"""


@dataclass(frozen=True)
class DerivativeArtifact:
    kind: DerivativeKind
    path: Path
    content_type: str
    page_count: int | None
    # 图片预览的页码（从 1 开始）；非分页产物为 None。
    page_no: int | None = None


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
    if total > settings.MAX_DOCUMENT_PAGES:
        raise DerivativeError("PDF 页数超过上限")
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
    try:
        if not 1 <= len(document) <= settings.MAX_DOCUMENT_PAGES:
            raise DerivativeError("PDF 页数不符合限制")
        page = document[0]
        width_pt, height_pt = page.get_size()
        if min(width_pt, height_pt) <= 0:
            raise DerivativeError("页面尺寸无效")
        bitmap = page.render(scale=settings.PREVIEW_THUMBNAIL_WIDTH / max(width_pt, height_pt))
        try:
            image = bitmap.to_pil().convert("RGB")
        finally:
            bitmap.close()
            page.close()
    finally:
        document.close()
    target = workdir / "thumb.png"
    image.save(target, format="PNG", optimize=True)
    return DerivativeArtifact(DerivativeKind.THUMBNAIL, target, "image/png", 1)


def _image_thumbnail(source: Path, workdir: Path) -> DerivativeArtifact:
    from PIL import Image

    with Image.open(source) as image:
        if image.width * image.height > settings.MAX_IMAGE_PIXELS:
            raise DerivativeError("图片像素超过上限")
        converted = image.convert("RGB")
        converted.info.clear()
        side = settings.PREVIEW_THUMBNAIL_WIDTH
        converted.thumbnail((side, side))
        target = workdir / "thumb.png"
        converted.save(target, format="PNG", optimize=True)
    return DerivativeArtifact(DerivativeKind.THUMBNAIL, target, "image/png", 1)


def _convert_office(source: Path, workdir: Path) -> Path:
    """无头转换为 PDF：受限临时目录、独立用户配置、超时、沙箱与网络隔离（§16.1）。

    沙箱计划由 `content.sandbox` 决定：能隔离就用 bubblewrap/unshare，
    不能则按 ``PARSE_SANDBOX_MODE`` 记录或拒绝；转换器始终拿不到继承的密钥与代理。
    """
    from app.modules.content.sandbox import run_sandboxed

    profile = workdir / "lo-profile"
    profile.mkdir(exist_ok=True)
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
        completed, plan = run_sandboxed(
            command, workdir=workdir, timeout=settings.DERIVATIVE_TIMEOUT_SECONDS
        )
    except FileNotFoundError as exc:
        raise DerivativeError(f"未找到转换器 {settings.LIBREOFFICE_BIN}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DerivativeError("转换超时") from exc
    except Exception as exc:  # noqa: BLE001 - 沙箱不可用等统一为派生失败
        from app.modules.content.sandbox import SandboxUnavailable

        if isinstance(exc, SandboxUnavailable):
            raise DerivativeError(f"解析沙箱不可用：{exc}") from exc
        raise DerivativeError(f"转换异常：{type(exc).__name__}") from exc

    logger.info(
        "office_conversion",
        extra={
            "sandbox_backend": plan.backend,
            "network_isolated": plan.network_isolated,
            "filesystem_isolated": plan.filesystem_isolated,
            "sandbox_reason": plan.reason,
        },
    )
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


# --------------------------------------------------------------------------- #
# 第一页水印图片预览（prevew.md 四、五、十二）
# --------------------------------------------------------------------------- #
def _apply_watermark(image: "Image.Image", text: str) -> "Image.Image":
    """叠加低可见度斜向重复水印（prevew.md 十二）。

    平铺而非只放角落：裁剪边缘去不掉水印；透明度受 `PREVIEW_WATERMARK_OPACITY`
    控制，不破坏阅读但可追溯。字体用内置 `load_default()`，不依赖系统字体文件。
    """
    from PIL import Image, ImageDraw, ImageFont

    tile = Image.new("RGBA", (360, 96), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    draw.text(
        (18, 28),
        text,
        font=ImageFont.load_default(),
        fill=(0, 0, 0, settings.PREVIEW_WATERMARK_OPACITY),
    )
    rotated = tile.rotate(30, expand=True, resample=Image.Resampling.BICUBIC)
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    for top in range(0, image.height, rotated.height):
        for left in range(0, image.width, rotated.width):
            layer.alpha_composite(rotated, (left, top))
    return Image.alpha_composite(image, layer)


def _watermarked_webp(
    image: "Image.Image",
    workdir: Path,
    *,
    watermark_text: str,
    file_name: str = "preview.webp",
    page_no: int | None = None,
) -> DerivativeArtifact | None:
    """加水印并保存为 WebP；渲染或写入失败返回 None（调用方回退既有预览）。"""
    try:
        watermarked = _apply_watermark(image.convert("RGBA"), watermark_text)
        target = workdir / file_name
        # 图片由服务端重新渲染/缩放生成，不携带原件元数据（prevew.md 四）。
        output = watermarked.convert("RGB")
        output.info.clear()
        output.save(
            target, format="WEBP", quality=settings.PREVIEW_IMAGE_QUALITY
        )
    except Exception:  # noqa: BLE001 - 生成失败回退既有预览，不阻断预览链路（§13.1）
        return None
    return DerivativeArtifact(
        DerivativeKind.WATERMARKED_PREVIEW,
        target,
        "image/webp",
        1,
        page_no=page_no,
    )


def _pdf_page_images(
    source: Path, workdir: Path, watermark_text: str, max_pages: int
) -> list[DerivativeArtifact]:
    """PDF 前 N 页分别渲染为限定宽度图片（prevew.md 五、六：不原尺寸输出）。

    浏览器拿到的是 page1.webp…pageN.webp，不是完整 PDF——权限隔离由服务端渲染保证，
    不是前端隐藏后续页。单页渲染失败只跳过该页，不整体失败。
    """
    import pypdfium2 as pdfium

    artifacts: list[DerivativeArtifact] = []
    document = None
    try:
        document = pdfium.PdfDocument(str(source))
        if not 1 <= len(document) <= settings.MAX_DOCUMENT_PAGES:
            return []
        limit = min(len(document), max(1, max_pages))
        for index in range(limit):
            page = None
            bitmap = None
            try:
                page = document[index]
                width_pt, height_pt = page.get_size()
                if min(width_pt, height_pt) <= 0:
                    continue
                # 同时限制长边，避免极长页面按宽度缩放后分配巨量位图内存。
                scale = settings.PREVIEW_IMAGE_WIDTH / max(width_pt, height_pt)
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil().copy()
            except Exception:  # noqa: BLE001 - 单页失败跳过，保留其余页
                continue
            finally:
                if bitmap is not None:
                    bitmap.close()
                if page is not None:
                    page.close()
            artifact = _watermarked_webp(
                image,
                workdir,
                watermark_text=watermark_text,
                file_name=f"preview-p{index + 1}.webp",
                page_no=index + 1,
            )
            if artifact is not None:
                artifacts.append(artifact)
    except Exception:  # noqa: BLE001 - 渲染失败回退既有预览
        return []
    finally:
        # pdfium 持有源文件句柄，必须显式关闭，否则临时目录清理会被文件锁阻塞（Windows）。
        if document is not None:
            document.close()
    return artifacts


def _image_first_page(
    source: Path, workdir: Path, watermark_text: str
) -> list[DerivativeArtifact]:
    """PNG/JPEG 原件：缩放后作为图片预览（水印同样叠加）。"""
    from PIL import Image

    try:
        with Image.open(source) as opened:
            if opened.width * opened.height > settings.MAX_IMAGE_PIXELS:
                return []
            image = opened.convert("RGBA")
            image.thumbnail((settings.PREVIEW_IMAGE_WIDTH, settings.PREVIEW_IMAGE_WIDTH))
    except Exception:  # noqa: BLE001 - 解码失败回退既有预览
        return []
    artifact = _watermarked_webp(
        image, workdir, watermark_text=watermark_text, file_name="preview-p1.webp", page_no=1
    )
    return [artifact] if artifact is not None else []


async def build_watermarked_pages(
    source: Path,
    *,
    detected_mime: str,
    workdir: Path,
    watermark_text: str,
    max_pages: int | None = None,
) -> list[DerivativeArtifact]:
    """生成前 N 页水印图片预览（WebP）；无法生成时返回空列表。

    - PDF：按 `PREVIEW_IMAGE_MAX_PAGES` 渲染前 N 页；
    - PNG/JPEG：只有一页；
    - DOCX/PPTX：先用 LibreOffice 转受限 PDF，再走同一图片化链路（统一图片化，
      浏览器不再直接拿到 Office 转出的 PDF）；
    - 渲染是 CPU 阻塞操作：放线程池并受 `DERIVATIVE_TIMEOUT_SECONDS` 约束。
    """
    limit = max(1, int(max_pages or settings.PREVIEW_IMAGE_MAX_PAGES))
    try:
        if detected_mime == PDF_MIME:
            return await _run_blocking(_pdf_page_images, source, workdir, watermark_text, limit)
        if detected_mime in (PNG_MIME, JPEG_MIME):
            return await _run_blocking(_image_first_page, source, workdir, watermark_text)
        if detected_mime in (DOCX_MIME, PPTX_MIME):
            converted = await _run_blocking(_convert_office, source, workdir)
            return await _run_blocking(
                _pdf_page_images, converted, workdir, watermark_text, limit
            )
    except Exception:  # noqa: BLE001 - 超时/异常回退既有预览，不阻断预览链路
        return []
    return []


async def build_watermarked_preview(
    source: Path, *, detected_mime: str, workdir: Path, watermark_text: str
) -> DerivativeArtifact | None:
    """兼容入口：只取第一页水印图片（旧调用方与测试使用）。"""
    pages = await build_watermarked_pages(
        source,
        detected_mime=detected_mime,
        workdir=workdir,
        watermark_text=watermark_text,
        max_pages=1,
    )
    return pages[0] if pages else None
