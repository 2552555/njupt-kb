"""access 模块 DTO（设计文档 §25.3、§10：对外响应一律用 DTO）。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import Capability, EntitlementSource, ScopeType


class PreviewGrantRequest(BaseModel):
    version_id: uuid.UUID


class PreviewGrantResult(BaseModel):
    """预览凭证响应：仅提供派生产物的短期访问 URL。"""

    grant_id: uuid.UUID
    preview_url: str
    url_expires_at: datetime
    file_name: str
    version_no: int


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
