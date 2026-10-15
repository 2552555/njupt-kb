"""内容安全检查适配器（设计文档 §13.1、§16.1）。

原则：
- 扫描服务不可用时文件留在隔离区等待，**绝不**默认 `passed`（`failed` 或 `pending`）。
- `rejected` 表示检测拒绝（恶意内容 / 格式不允许）；`failed` 表示检查未能完成，两者语义不同。
- 检查大小、真实格式与扩展名一致性；解析有超时上限。
- ZIP / RAR / 可执行文件 / 宏文档 / 视频默认不作为可发布内容类型（§16.1）。
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.config import settings
from app.core.enums import ScanStatus
from app.core.storage import storage

# 首期允许稳定转换与解析的格式（§16.1、D06）。
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
PDF_MIME = "application/pdf"
PNG_MIME = "image/png"
JPEG_MIME = "image/jpeg"

_MAGIC = (
    (b"%PDF-", PDF_MIME),
    (b"\x89PNG\r\n\x1a\n", PNG_MIME),
    (b"\xff\xd8\xff", JPEG_MIME),
)

_EXTENSIONS: dict[str, set[str]] = {
    PDF_MIME: {".pdf"},
    PNG_MIME: {".png"},
    JPEG_MIME: {".jpg", ".jpeg"},
    DOCX_MIME: {".docx"},
    PPTX_MIME: {".pptx"},
}


class ParseFailure(Exception):
    """文件结构损坏或不可解析。"""


@dataclass(frozen=True)
class ScanVerdict:
    status: ScanStatus
    reason: str | None = None


@dataclass(frozen=True)
class ScanOutcome:
    status: ScanStatus
    detected_mime: str | None = None
    size: int | None = None
    sha256: str | None = None
    page_count: int | None = None
    reason: str | None = None


class ScanProvider(Protocol):
    """恶意内容检测提供者。"""

    name: str

    async def scan(self, path: Path) -> ScanVerdict: ...


def _clamav_scan(path: Path) -> tuple[str, str | None]:
    import clamd

    client = clamd.ClamdNetworkSocket(
        host=settings.CLAMD_HOST, port=settings.CLAMD_PORT, timeout=settings.SCAN_TIMEOUT_SECONDS
    )
    with path.open("rb") as handle:
        result = client.instream(handle)
    status, signature = result.get("stream", ("ERROR", None))
    return status, signature


class ClamAVScanProvider:
    """通过 clamd 扫描（§16.1）。"""

    name = "clamav"

    async def scan(self, path: Path) -> ScanVerdict:
        try:
            verdict, signature = await asyncio.wait_for(
                asyncio.to_thread(_clamav_scan, path), timeout=settings.SCAN_TIMEOUT_SECONDS
            )
        except Exception as exc:  # noqa: BLE001 - 扫描不可用必须显式失败，不默认通过
            return ScanVerdict(ScanStatus.FAILED, f"scanner_unavailable:{type(exc).__name__}")
        if verdict == "FOUND":
            return ScanVerdict(ScanStatus.REJECTED, f"malware:{signature or 'unknown'}")
        if verdict == "OK":
            return ScanVerdict(ScanStatus.PASSED)
        return ScanVerdict(ScanStatus.FAILED, "scanner_error")


class DisabledScanProvider:
    """未配置扫描器：没有检查结果，不能当成通过（§13.1）。"""

    name = "disabled"

    async def scan(self, path: Path) -> ScanVerdict:
        return ScanVerdict(ScanStatus.FAILED, "scanner_disabled")


def get_scan_provider() -> ScanProvider:
    if settings.SCANNER == "clamav":
        return ClamAVScanProvider()
    return DisabledScanProvider()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _detect_ooxml(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return None
    if "[Content_Types].xml" not in names:
        return None
    # 宏文档不作为可发布内容类型（§16.1）。
    if any(name.endswith("vbaProject.bin") for name in names):
        return None
    if any(name.startswith("word/") for name in names):
        return DOCX_MIME
    if any(name.startswith("ppt/") for name in names):
        return PPTX_MIME
    # 普通压缩包默认不直接预览，也不作为可发布内容类型（§16.1）。
    return None


def _detect_format(path: Path) -> str | None:
    with path.open("rb") as handle:
        head = handle.read(8)
    for magic, mime in _MAGIC:
        if head.startswith(magic):
            return mime
    if head.startswith(b"PK\x03\x04"):
        return _detect_ooxml(path)
    return None


def _extension_matches(mime: str, original_name: str) -> bool:
    return Path(original_name).suffix.lower() in _EXTENSIONS.get(mime, set())


def _pdf_page_count(path: Path) -> int:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        raise ParseFailure("PDF 已加密")
    return len(reader.pages)


def _image_page_count(path: Path) -> int:
    from PIL import Image

    with Image.open(path) as image:
        image.verify()
    return 1


async def _parse_pages(path: Path, mime: str) -> int | None:
    """解析页数并设超时上限；DOCX/PPTX 不解析页数（§16.1）。"""
    if mime == PDF_MIME:
        return await asyncio.wait_for(
            asyncio.to_thread(_pdf_page_count, path), timeout=settings.SCAN_TIMEOUT_SECONDS
        )
    if mime in (PNG_MIME, JPEG_MIME):
        return await asyncio.wait_for(
            asyncio.to_thread(_image_page_count, path), timeout=settings.SCAN_TIMEOUT_SECONDS
        )
    return None


async def scan_quarantined_object(
    storage_key: str, *, original_name: str
) -> ScanOutcome:
    """从隔离区取回对象，完成大小、格式、扩展名一致性与恶意内容检查（§16 步骤 5）。"""
    provider = get_scan_provider()
    with tempfile.TemporaryDirectory(prefix="njupt-scan-") as tmp:
        path = Path(tmp) / "object"
        with path.open("wb") as handle:
            written = await storage.download_to_file(storage_key, handle)

        if written <= 0:
            return ScanOutcome(status=ScanStatus.REJECTED, size=0, reason="empty_file")
        if written > settings.MAX_UPLOAD_SIZE_BYTES:
            return ScanOutcome(status=ScanStatus.REJECTED, size=written, reason="size_exceeded")

        sha256 = _sha256(path)
        detected = _detect_format(path)
        base = {"size": written, "sha256": sha256}
        if detected is None:
            return ScanOutcome(status=ScanStatus.REJECTED, detected_mime=None, reason="format_not_allowed", **base)
        if detected not in settings.ALLOWED_UPLOAD_MIME:
            return ScanOutcome(status=ScanStatus.REJECTED, detected_mime=detected, reason="mime_not_allowed", **base)
        if not _extension_matches(detected, original_name):
            return ScanOutcome(status=ScanStatus.REJECTED, detected_mime=detected, reason="extension_mismatch", **base)

        try:
            page_count = await _parse_pages(path, detected)
        except asyncio.TimeoutError:
            return ScanOutcome(status=ScanStatus.FAILED, detected_mime=detected, reason="parse_timeout", **base)
        except ParseFailure as exc:
            return ScanOutcome(status=ScanStatus.REJECTED, detected_mime=detected, reason=f"malformed:{exc}", **base)
        except Exception as exc:  # noqa: BLE001 - 解析异常按未完成处理
            return ScanOutcome(status=ScanStatus.FAILED, detected_mime=detected, reason=f"parse_error:{type(exc).__name__}", **base)

        verdict = await provider.scan(path)
        return ScanOutcome(
            status=verdict.status,
            detected_mime=detected,
            page_count=page_count,
            reason=verdict.reason,
            **base,
        )
