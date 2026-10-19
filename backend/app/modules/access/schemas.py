"""access 模块 DTO（设计文档 §25.3、§10：对外响应一律用 DTO）。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import Capability, EntitlementSource, ScopeType


class PreviewGrantRequest(BaseModel):
    version_id: uuid.UUID


class PreviewGrantOut(BaseModel):
    """预览凭证状态响应（撤权后返回），仅暴露状态与元信息，不含签名 URL。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    resource_id: uuid.UUID
    version_id: uuid.UUID
    user_id: uuid.UUID | None = None
    file_id: uuid.UUID | None = None
    derivative_id: uuid.UUID | None = None
    status: str
    valid_until: datetime
    created_at: datetime


class PreviewPageOut(BaseModel):
    """单页预览：页码 + 该页的短期 URL。多页预览逐页签发，不共享一个对象。"""

    page_no: int
    url: str
    url_expires_at: datetime


class PreviewGrantResult(BaseModel):
    """预览凭证响应：仅提供派生产物的短期访问 URL。

    `preview_type` 指明签发的派生形态（prevew.md 五）：
    image=水印 WebP 图片；pdf=受限预览 PDF；thumbnail=缩略图。
    `pages` 仅在图片预览时返回，最多 `PREVIEW_IMAGE_MAX_PAGES` 页；
    `preview_url` 始终等于第一页（或回退产物），旧客户端无需改动。
    """

    grant_id: uuid.UUID
    preview_url: str
    url_expires_at: datetime
    file_name: str
    version_no: int
    preview_type: str
    watermark_token: str | None = None
    pages: list[PreviewPageOut] = []
    # 图片预览是否由服务端叠加了水印（匿名与登录都有水印，此字段用于前端说明）。
    watermarked: bool = False


class DownloadGrantRequest(BaseModel):
    version_id: uuid.UUID
    file_id: uuid.UUID


class DownloadGrantResult(BaseModel):
    """下载授权响应，字段契约与设计文档 §25.3 一致。"""

    grant_id: uuid.UUID
    download_url: str
    url_expires_at: datetime
    file_name: str
    version_no: int


class EntitlementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    scope_type: ScopeType
    scope_id: uuid.UUID
    capability: Capability
    source_type: EntitlementSource
    source_id: uuid.UUID | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    revoked_reason: str | None = None
    created_at: datetime


class EntitlementCreate(BaseModel):
    user_id: uuid.UUID
    scope_type: ScopeType
    scope_id: uuid.UUID
    capability: Capability
    source_type: EntitlementSource
    source_id: uuid.UUID | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None


class EntitlementRevoke(BaseModel):
    reason: str = Field(min_length=1, max_length=255)


class AccessPolicyUpdate(BaseModel):
    metadata_scope: ScopeType
    preview_scope: ScopeType
    original_scope: ScopeType


class AccessPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    resource_id: uuid.UUID
    metadata_scope: ScopeType
    preview_scope: ScopeType
    original_scope: ScopeType
    revision: int
    updated_at: datetime
