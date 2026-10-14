"""content 对外 DTO（设计文档 §10、§25.1）。

不返回 ORM 对象、`storage_key`、桶名或内部风险参数。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.enums import (
    FilePurpose,
    FreshnessStatus,
    PublicationStatus,
    ResourceType,
    ReviewStatus,
    RightsBasis,
    ScanStatus,
    UploadSessionStatus,
)
from app.core.pagination import CursorPage


# --- 请求 ---
class VersionFileIn(BaseModel):
    file_id: uuid.UUID
    purpose: FilePurpose = FilePurpose.PRIMARY
    display_order: int = 0


class RightsDeclarationIn(BaseModel):
    rights_basis: RightsBasis
    source: str | None = Field(default=None, max_length=2000)
    original_author: str | None = Field(default=None, max_length=300)
    license: str | None = Field(default=None, max_length=300)
    declaration_text_version: str = Field(min_length=1, max_length=32)


class VersionFieldsIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    change_summary: str | None = Field(default=None, max_length=2000)
    preview_disabled: bool = False
    files: list[VersionFileIn] = Field(default_factory=list)
    rights: RightsDeclarationIn | None = None
    tags: list[str] = Field(default_factory=list)


class ResourceCreate(VersionFieldsIn):
    course_id: uuid.UUID
    offering_id: uuid.UUID | None = None
    resource_type: ResourceType


class VersionCreate(VersionFieldsIn):
    pass


class VersionUpdate(BaseModel):
    """PATCH 草稿：revision 用于条件更新，过期提交返回 409（§13.3、§29）。"""

    revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    change_summary: str | None = Field(default=None, max_length=2000)
    preview_disabled: bool | None = None
    files: list[VersionFileIn] | None = None
    rights: RightsDeclarationIn | None = None
    tags: list[str] | None = None


class WithdrawIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    revision: int | None = Field(default=None, ge=1)


class UploadCreateIn(BaseModel):
    original_name: str = Field(min_length=1, max_length=300)
    declared_mime: str = Field(min_length=1, max_length=120)
    expected_size: int = Field(gt=0)
    resource_id: uuid.UUID | None = None


# --- 响应 ---
class VersionOut(BaseModel):
    id: uuid.UUID
    resource_id: uuid.UUID
    version_no: int
    title: str
    description: str | None
    change_summary: str | None
    review_status: ReviewStatus
    preview_disabled: bool
    submitted_at: datetime | None
    revision: int
    created_at: datetime
    updated_at: datetime


class FileOut(BaseModel):
    """版本文件清单条目。不包含 storage_key 或任何永久链接（§16）。"""

    id: uuid.UUID
    original_name: str
    detected_mime: str | None
    size: int
    sha256: str | None
    page_count: int | None
    scan_status: ScanStatus
    purpose: FilePurpose
    display_order: int
    created_at: datetime


class VersionFilesOut(BaseModel):
    resource_id: uuid.UUID
    version_id: uuid.UUID
    items: list[FileOut]


class ResourceCreatedOut(BaseModel):
    resource_id: uuid.UUID
    version_id: uuid.UUID
    version_no: int
    publication_status: PublicationStatus
    review_status: ReviewStatus
    revision: int


class ResourceListItem(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    offering_id: uuid.UUID | None
    resource_type: ResourceType
    freshness_status: FreshnessStatus
    title: str | None
    version_no: int | None
    updated_at: datetime


class ResourceListOut(CursorPage[ResourceListItem]):
    pass


class ResourceDetailOut(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    offering_id: uuid.UUID | None
    resource_type: ResourceType
    publication_status: PublicationStatus
    freshness_status: FreshnessStatus
    published_version_id: uuid.UUID | None
    revision: int
    created_at: datetime
    updated_at: datetime
    published_version: VersionOut | None
    preview_available: bool


class PublicationOut(BaseModel):
    resource_id: uuid.UUID
    publication_status: PublicationStatus
    published_version_id: uuid.UUID | None
    revision: int


class SubmitOut(BaseModel):
    version_id: uuid.UUID
    review_id: uuid.UUID
    review_status: ReviewStatus
    submitted_at: datetime | None


class UploadCreateOut(BaseModel):
    id: uuid.UUID
    status: UploadSessionStatus
    original_name: str
    upload_url: str
    content_type: str
    expires_at: datetime


class UploadStatusOut(BaseModel):
    id: uuid.UUID
    status: UploadSessionStatus
    original_name: str
    declared_mime: str
    expected_size: int
    actual_size: int | None
    file_id: uuid.UUID | None
    scan_status: ScanStatus | None
    created_at: datetime
    completed_at: datetime | None
