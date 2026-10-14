"""资源、版本与文件模型（设计文档 §12、§12.1、§12.2、§13.1）。

资源 / 资源版本 / 文件分别建模：已提交审核的文件内容不原地覆盖，
预览是 `FileDerivative`，不是另一个人工上传的内容版本。

跨模块外键用字符串表名（§2.2），不导入对方模型类。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import (
    DerivativeKind,
    DerivativeStatus,
    FilePurpose,
    FreshnessStatus,
    PublicationStatus,
    RelationType,
    ResourceType,
    ReviewStatus,
    RightsBasis,
    ScanStatus,
    UploadSessionStatus,
)
from app.db import Base, Revisioned, Timestamped, UUIDPrimaryKey

__all__ = [
    "Resource",
    "ResourceVersion",
    "ResourceFile",
    "VersionFile",
    "FileDerivative",
    "RightsDeclaration",
    "Tag",
    "ResourceTag",
    "ResourceRelation",
    "UploadSession",
]


def _in(column: str, enum_cls: type[StrEnum]) -> str:
    """生成状态列 CheckConstraint 的 SQL（§4：String 存枚举值 + CheckConstraint）。"""
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"


class Resource(Base, UUIDPrimaryKey, Timestamped, Revisioned):
    """资源：内容身份与公开指针。资源必须归属课程（§11.2、§12）。"""

    __tablename__ = "resources"
    __table_args__ = (
        CheckConstraint(_in("resource_type", ResourceType), name="resource_type"),
        CheckConstraint(_in("publication_status", PublicationStatus), name="publication_status"),
        CheckConstraint(_in("freshness_status", FreshnessStatus), name="freshness_status"),
        Index("ix_resources_publication_updated", "publication_status", "updated_at"),
        Index("ix_resources_course_publication", "course_id", "publication_status"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("courses.id"), nullable=False, index=True
    )
    # offering_id 可空表示跨学期通用内容；若指定开课实例，其 course_id 必须与资源课程一致（§11.2）。
    offering_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("course_offerings.id"), nullable=True, index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False, index=True
    )
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    publication_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PublicationStatus.UNPUBLISHED.value
    )
    freshness_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=FreshnessStatus.CURRENT.value
    )
    access_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("access_policies.id"), nullable=True
    )
    # 公开指针必须指向本资源已通过审核的版本（§12.1）；由发布事务核验。
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("resource_versions.id", use_alter=True, name="fk_resources_published_version_id"),
        nullable=True,
    )


class ResourceVersion(Base, UUIDPrimaryKey, Timestamped, Revisioned):
    """资源版本快照：标题、简介、文件与来源声明共同构成审批对象（§12.1）。"""

    __tablename__ = "resource_versions"
    __table_args__ = (
        UniqueConstraint("resource_id", "version_no", name="uq_resource_versions_resource_version_no"),
        CheckConstraint(_in("review_status", ReviewStatus), name="review_status"),
        Index("ix_resource_versions_resource_created", "resource_id", "created_at"),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resources.id"), nullable=False, index=True
    )
    # 同一资源的版本号唯一，提交顺序单调增加；不以文件名中的 v2 作为系统版本（§12.1）。
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ReviewStatus.DRAFT.value
    )
    # 一页文档的“第一页预览”可能等同全部内容，允许作者关闭预览（§17.1）。
    preview_disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResourceFile(Base, UUIDPrimaryKey, Timestamped):
    """上传文件实体。SHA-256 只用于相同字节识别与完整性核验（§12.2）。"""

    __tablename__ = "resource_files"
    __table_args__ = (
        CheckConstraint(_in("scan_status", ScanStatus), name="scan_status"),
        Index("ix_resource_files_uploader_scan", "uploader_id", "scan_status"),
    )

    uploader_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False, index=True
    )
    upload_session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("upload_sessions.id"), nullable=True, index=True
    )
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    original_name: Mapped[str] = mapped_column(String(300), nullable=False)
    # detected_mime 由检查任务识别，不采用客户端声明（§16 步骤 4、5）。
    detected_mime: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scan_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ScanStatus.PENDING.value
    )
    scan_failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class VersionFile(Base, UUIDPrimaryKey, Timestamped):
    """版本与文件的关联；purpose 区分主文件、附件和示例（§12）。"""

    __tablename__ = "version_files"
    __table_args__ = (
        UniqueConstraint("version_id", "file_id", "purpose", name="uq_version_files_version_file_purpose"),
        CheckConstraint(_in("purpose", FilePurpose), name="purpose"),
        Index("ix_version_files_version_order", "version_id", "display_order"),
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resource_versions.id"), nullable=False, index=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resource_files.id"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(
        String(16), nullable=False, default=FilePurpose.PRIMARY.value
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class FileDerivative(Base, UUIDPrimaryKey, Timestamped):
    """预览派生产物。转换失败不改变原件审核事实（§13.1、§16.2）。"""

    __tablename__ = "file_derivatives"
    __table_args__ = (
        CheckConstraint(_in("kind", DerivativeKind), name="kind"),
        CheckConstraint(_in("status", DerivativeStatus), name="status"),
        Index("ix_file_derivatives_source_kind", "source_file_id", "kind"),
    )

    source_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resource_files.id"), nullable=False, index=True
    )
    processor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DerivativeStatus.PENDING.value
    )
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class RightsDeclaration(Base, UUIDPrimaryKey, Timestamped):
    """来源声明：提交时记录声明条款版本、声明人、时间与来源（§18）。"""

    __tablename__ = "rights_declarations"
    __table_args__ = (
        UniqueConstraint("version_id", name="uq_rights_declarations_version"),
        CheckConstraint(_in("rights_basis", RightsBasis), name="rights_basis"),
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resource_versions.id"), nullable=False, index=True
    )
    declarant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    rights_basis: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_author: Mapped[str | None] = mapped_column(String(300), nullable=True)
    license: Mapped[str | None] = mapped_column(String(300), nullable=True)
    declaration_text_version: Mapped[str] = mapped_column(String(32), nullable=False)
    declared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Tag(Base, UUIDPrimaryKey, Timestamped):
    """标签。"""

    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("slug", name="uq_tags_slug"),)

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)


class ResourceTag(Base, UUIDPrimaryKey, Timestamped):
    """资源与标签的关联。"""

    __tablename__ = "resource_tags"
    __table_args__ = (
        UniqueConstraint("resource_id", "tag_id", name="uq_resource_tags_resource_tag"),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resources.id"), nullable=False, index=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tags.id"), nullable=False, index=True
    )


class ResourceRelation(Base, UUIDPrimaryKey, Timestamped):
    """资源之间的关系。"""

    __tablename__ = "resource_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_resource_id", "target_resource_id", "relation_type",
            name="uq_resource_relations_triple",
        ),
        CheckConstraint(_in("relation_type", RelationType), name="relation_type"),
    )

    source_resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resources.id"), nullable=False, index=True
    )
    target_resource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("resources.id"), nullable=False, index=True
    )
    relation_type: Mapped[str] = mapped_column(String(24), nullable=False)


class UploadSession(Base, UUIDPrimaryKey, Timestamped):
    """上传会话：客户端向私有隔离区直传（§16 步骤 1-4、§13.1）。"""

    __tablename__ = "upload_sessions"
    __table_args__ = (
        CheckConstraint(_in("status", UploadSessionStatus), name="status"),
        Index("ix_upload_sessions_uploader_status", "uploader_id", "status"),
    )

    uploader_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False, index=True
    )
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("resources.id"), nullable=True, index=True
    )
    # 随机且不可覆盖其他对象的存储键；存储键与业务 ID 独立（§16、§31.2）。
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    original_name: Mapped[str] = mapped_column(String(300), nullable=False)
    declared_mime: Mapped[str] = mapped_column(String(120), nullable=False)
    expected_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=UploadSessionStatus.INITIATED.value
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
