"""S3 兼容私有对象存储（设计文档 §16、§17.3）。

原始文件进入私有存储，不返回永久公开原件链接。上传走隔离区，扫描通过后才进入私有区。
预签名 URL 在有效期内可多次使用，不是一次性凭证，调用方不得据此承诺"下载一次"。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import BinaryIO

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings
from app.core.errors import DependencyUnavailable, NotFound


class ObjectSizeExceeded(Exception):
    """对象超过既有上传大小上限，停止读取而非写满临时磁盘。"""


@dataclass(frozen=True)
class PresignedUpload:
    url: str
    storage_key: str
    expires_at: datetime


@dataclass(frozen=True)
class PresignedDownload:
    url: str
    expires_at: datetime


@dataclass(frozen=True)
class ObjectHead:
    size: int
    content_type: str | None
    etag: str | None


def build_storage_key(prefix: str, original_name: str) -> str:
    """分配随机且不可覆盖其他对象的存储键；存储键与业务 ID 独立（§16、§31.2）。"""
    suffix = ""
    if "." in original_name:
        suffix = "." + original_name.rsplit(".", 1)[1].lower()[:16]
    return f"{prefix.strip('/')}/{uuid.uuid4().hex}{suffix}"


class ObjectStorage:
    """薄封装：同步 boto3 调用放入线程池，避免阻塞事件循环。"""

    def __init__(self) -> None:
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL or None,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path" if settings.S3_FORCE_PATH_STYLE else "auto"},
                max_pool_connections=settings.S3_MAX_CONCURRENCY,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
        self._semaphore = asyncio.Semaphore(settings.S3_MAX_CONCURRENCY)
        self._public_client = self._client
        if settings.S3_PUBLIC_ENDPOINT_URL:
            self._public_client = boto3.client(
                "s3", endpoint_url=settings.S3_PUBLIC_ENDPOINT_URL,
                region_name=settings.S3_REGION,
                aws_access_key_id=settings.S3_ACCESS_KEY_ID,
                aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
                config=BotoConfig(signature_version="s3v4", s3={
                    "addressing_style": "path" if settings.S3_FORCE_PATH_STYLE else "auto"}),
            )

    # --- 内部 ---
    async def _run(self, fn, /, *args, **kwargs):
        async with self._semaphore:
            try:
                return await asyncio.to_thread(fn, *args, **kwargs)
            except (ClientError, BotoCoreError) as exc:
                raise DependencyUnavailable(
                    "对象存储暂时不可用，请稍后重试", details={"reason": type(exc).__name__}
                ) from exc

    # --- 上传 ---
    async def presign_upload(
        self, storage_key: str, content_type: str, *, bucket: str | None = None
    ) -> PresignedUpload:
        bucket = bucket or settings.S3_BUCKET_QUARANTINE
        expires_in = settings.S3_PRESIGN_UPLOAD_SECONDS
        url = await self._run(
            self._public_client.generate_presigned_url,
            "put_object",
            Params={"Bucket": bucket, "Key": storage_key, "ContentType": content_type},
            ExpiresIn=expires_in,
        )
        return PresignedUpload(
            url=url,
            storage_key=storage_key,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        )

    async def head(self, storage_key: str, *, bucket: str | None = None) -> ObjectHead | None:
        bucket = bucket or settings.S3_BUCKET_QUARANTINE
        try:
            async with self._semaphore:
                resp = await asyncio.to_thread(
                    self._client.head_object, Bucket=bucket, Key=storage_key
                )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise DependencyUnavailable("对象存储暂时不可用，请稍后重试") from exc
        except BotoCoreError as exc:
            raise DependencyUnavailable("对象存储暂时不可用，请稍后重试") from exc
        return ObjectHead(
            size=int(resp.get("ContentLength", 0)),
            content_type=resp.get("ContentType"),
            etag=(resp.get("ETag") or "").strip('"') or None,
        )

    async def download_to_file(
        self, storage_key: str, target: BinaryIO, *, bucket: str | None = None
    ) -> int:
        bucket = bucket or settings.S3_BUCKET_QUARANTINE
        written = 0
        async with self._semaphore:
            try:
                obj = await asyncio.to_thread(
                    self._client.get_object, Bucket=bucket, Key=storage_key
                )
                body = obj["Body"]
                try:
                    if int(obj.get("ContentLength", 0)) > settings.MAX_UPLOAD_SIZE_BYTES:
                        raise ObjectSizeExceeded()
                    while True:
                        chunk = await asyncio.to_thread(body.read, 1024 * 1024)
                        if not chunk:
                            break
                        if written + len(chunk) > settings.MAX_UPLOAD_SIZE_BYTES:
                            raise ObjectSizeExceeded()
                        target.write(chunk)
                        written += len(chunk)
                finally:
                    body.close()
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in {"404", "NoSuchKey", "NotFound"}:
                    raise NotFound("文件对象不存在") from exc
                raise DependencyUnavailable("对象存储暂时不可用，请稍后重试") from exc
            except BotoCoreError as exc:
                raise DependencyUnavailable("对象存储暂时不可用，请稍后重试") from exc
        return written

    async def put_file(
        self, storage_key: str, source: BinaryIO, content_type: str, *, bucket: str
    ) -> None:
        async with self._semaphore:
            try:
                await asyncio.to_thread(
                    self._client.upload_fileobj,
                    source,
                    bucket,
                    storage_key,
                    ExtraArgs={"ContentType": content_type},
                )
            except (ClientError, BotoCoreError) as exc:
                raise DependencyUnavailable("对象存储暂时不可用，请稍后重试") from exc

    async def put_public_object(self, storage_key: str, data: bytes, content_type: str) -> None:
        """公开桶写入：头像等可匿名读取的静态资源（§43）。内容类型由调用方校验后传入。"""
        async with self._semaphore:
            try:
                await asyncio.to_thread(
                    self._client.put_object,
                    Bucket=settings.S3_BUCKET_PUBLIC,
                    Key=storage_key,
                    Body=data,
                    ContentType=content_type,
                )
            except (ClientError, BotoCoreError) as exc:
                raise DependencyUnavailable("对象存储暂时不可用，请稍后重试") from exc

    def public_url(self, storage_key: str) -> str:
        """公开对象的永久地址；优先使用 CDN/对外前缀，否则回退到 endpoint + 桶名。"""
        base = settings.S3_PUBLIC_BASE_URL or (
            f"{(settings.S3_PUBLIC_ENDPOINT_URL or settings.S3_ENDPOINT_URL).rstrip('/')}"
            f"/{settings.S3_BUCKET_PUBLIC}"
        )
        return f"{base.rstrip('/')}/{storage_key}"

    async def promote(self, storage_key: str, *, target_bucket: str) -> None:
        """隔离区对象转入私有区：扫描通过后才允许（§16）。"""
        async with self._semaphore:
            try:
                await asyncio.to_thread(
                    self._client.copy_object,
                    Bucket=target_bucket,
                    Key=storage_key,
                    CopySource={"Bucket": settings.S3_BUCKET_QUARANTINE, "Key": storage_key},
                )
                await asyncio.to_thread(
                    self._client.delete_object,
                    Bucket=settings.S3_BUCKET_QUARANTINE,
                    Key=storage_key,
                )
            except (ClientError, BotoCoreError) as exc:
                raise DependencyUnavailable("对象存储暂时不可用，请稍后重试") from exc

    async def delete(self, storage_key: str, *, bucket: str) -> None:
        async with self._semaphore:
            try:
                await asyncio.to_thread(
                    self._client.delete_object, Bucket=bucket, Key=storage_key
                )
            except (ClientError, BotoCoreError) as exc:
                raise DependencyUnavailable("对象存储暂时不可用，请稍后重试") from exc

    # --- 下载 ---
    async def presign_download(
        self,
        storage_key: str,
        *,
        bucket: str,
        file_name: str | None = None,
        expires_in: int | None = None,
    ) -> PresignedDownload:
        expires_in = expires_in or settings.S3_PRESIGN_DOWNLOAD_SECONDS
        params: dict[str, object] = {"Bucket": bucket, "Key": storage_key}
        if file_name:
            from urllib.parse import quote

            params["ResponseContentDisposition"] = (
                f"attachment; filename*=UTF-8''{quote(file_name)}"
            )
        url = await self._run(
            self._public_client.generate_presigned_url,
            "get_object",
            Params=params,
            ExpiresIn=expires_in,
        )
        return PresignedDownload(
            url=url, expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        )

    async def presign_preview_download(
        self,
        storage_key: str,
        *,
        bucket: str,
        file_name: str | None = None,
    ) -> PresignedDownload:
        """预览专用短期预签名下载：使用 PREVIEW_URL_TTL_SECONDS 而非默认下载有效期。

        预览只提供派生产物（第一页水印 WebP / 受限预览 PDF），缩短"持有即用的临时凭据"
        暴露窗口（prevew.md §十二、S05）。不改变既有 presign_download 的语义。
        """
        return await self.presign_download(
            storage_key,
            bucket=bucket,
            file_name=file_name,
            expires_in=settings.PREVIEW_URL_TTL_SECONDS,
        )

    async def health(self) -> bool:
        try:
            async with self._semaphore:
                await asyncio.to_thread(self._client.list_buckets)
            return True
        except Exception:  # noqa: BLE001 - 健康检查不抛出，返回不可用
            return False

    async def ensure_buckets(self) -> list[str]:
        """确保配置的桶存在，返回已创建/已存在的桶名。开发与自建部署使用。"""
        created: list[str] = []
        for bucket in (
            settings.S3_BUCKET_QUARANTINE,
            settings.S3_BUCKET_PRIVATE,
            settings.S3_BUCKET_PUBLIC,
        ):
            try:
                async with self._semaphore:
                    await asyncio.to_thread(self._client.head_bucket, Bucket=bucket)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in {"404", "NoSuchBucket", "NotFound"}:
                    async with self._semaphore:
                        await asyncio.to_thread(self._client.create_bucket, Bucket=bucket)
                else:
                    raise DependencyUnavailable("无法初始化对象存储桶") from exc
            created.append(bucket)
        return created


@lru_cache
def get_storage() -> ObjectStorage:
    return ObjectStorage()


storage = get_storage()
