"""content 应用服务：业务规则的唯一落点（设计文档 §12、§12.1、§12.2、§13、§16、§17.1、§18）。

跨模块调用一律在函数体内局部导入（§2.3），不导入对方模型类。

对外契约函数（docs/INTERFACES.md `## content`）：
    get_resource / get_version / get_file / published_version / version_files /
    resource_course_id / assert_visible / mark_published / mark_withdrawn / set_freshness /
    set_version_review_status / reassign_resource_course

任务处理入口（供 jobs 模块的 FILE_SCAN / FILE_DERIVE 处理器调用）：
    process_file_scan(db, file_id=...) / process_file_derive(db, file_id=...)

按用户水印预览（prevew.md 五、十二，供 access 预览授予调用）：
    ensure_watermarked_preview(db, source_file=..., user=...)
"""

from __future__ import annotations

import re
import hashlib
import tempfile
import uuid
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import (
    Integer,
    String,
    Uuid,
    and_,
    case,
    column,
    delete,
    func,
    or_,
    select,
    table,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import record_audit
from app.core.enums import (
    DerivativeKind,
    DerivativeStatus,
    DomainEventType,
    FilePurpose,
    FreshnessStatus,
    GrantStatus,
    JobType,
    PublicationStatus,
    ResourceType,
    ReviewStatus,
    RightsBasis,
    ScanStatus,
    UploadSessionStatus,
)
from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.core.events import emit_event
from app.core.pagination import decode_cursor, encode_cursor, normalize_page_size
from app.core.permissions import RESOURCE_PUBLISH, RESOURCE_REVIEW, effective_permissions
from app.core.storage import ObjectSizeExceeded, build_storage_key, storage
from app.modules.content.derivatives import (
    PROCESSOR_VERSION,
    DerivativeError,
    build_derivatives,
    build_watermarked_pages,
    build_watermarked_preview,
    primary_kind,
)
from app.modules.content.models import (
    FileDerivative,
    Resource,
    ResourceFile,
    ResourceTag,
    ResourceVersion,
    RightsDeclaration,
    Tag,
    UploadSession,
    VersionFile,
)
from app.modules.content.scanning import (
    DOCX_MIME,
    JPEG_MIME,
    PDF_MIME,
    PNG_MIME,
    PPTX_MIME,
    ScanOutcome,
    scan_quarantined_object,
)
from app.modules.content.schemas import (
    FileOut,
    PublicationOut,
    ResourceCreatedOut,
    ResourceDetailOut,
    ResourceListItem,
    ResourceListOut,
    RightsDeclarationIn,
    SubmitOut,
    UploadCreateIn,
    UploadCreateOut,
    UploadStatusOut,
    VersionFileIn,
    VersionFilesOut,
    VersionOut,
)

# 只用于按目录筛选的轻量表引用；不导入 catalog 模型类（§2.2）。
_COURSES = table("courses", column("id", Uuid), column("owning_college_id", Uuid))
_OFFERINGS = table(
    "course_offerings", column("id", Uuid), column("course_id", Uuid), column("semester_id", Uuid)
)
_SEMESTERS = table("semesters", column("id", Uuid), column("academic_year", Integer))
# 计数展示用的跨模块只读引用：不导入 access/interaction 模型类（§2.2）。
_DOWNLOAD_GRANTS = table(
    "download_grants", column("resource_id", Uuid), column("status", String)
)
_FAVORITES = table("favorites", column("resource_id", Uuid))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _slugify(name: str) -> str:
    return re.sub(r"\s+", "-", name.strip().lower())[:64]


# --------------------------------------------------------------------------- #
# 对外契约函数
# --------------------------------------------------------------------------- #
async def get_resource(db: AsyncSession, resource_id: uuid.UUID) -> Resource:
    resource = await db.get(Resource, resource_id)
    if resource is None:
        raise NotFound("资源不存在")
    return resource


async def get_version(db: AsyncSession, version_id: uuid.UUID) -> ResourceVersion:
    version = await db.get(ResourceVersion, version_id)
    if version is None:
        raise NotFound("资源版本不存在")
    return version


async def get_file(db: AsyncSession, file_id: uuid.UUID) -> ResourceFile:
    file = await db.get(ResourceFile, file_id)
    if file is None:
        raise NotFound("文件不存在")
    return file


async def published_version(db: AsyncSession, resource: Resource) -> ResourceVersion | None:
    if resource.published_version_id is None:
        return None
    return await db.get(ResourceVersion, resource.published_version_id)


async def version_files(
    db: AsyncSession, version_id: uuid.UUID
) -> list[tuple[VersionFile, ResourceFile]]:
    stmt = (
        select(VersionFile, ResourceFile)
        .join(ResourceFile, VersionFile.file_id == ResourceFile.id)
        .where(VersionFile.version_id == version_id)
        .order_by(VersionFile.display_order, VersionFile.created_at)
    )
    return list((await db.execute(stmt)).all())


async def resource_course_id(db: AsyncSession, resource_id: uuid.UUID) -> uuid.UUID:
    resource = await get_resource(db, resource_id)
    return resource.course_id


async def assert_visible(db: AsyncSession, resource: Resource) -> None:
    """可见性唯一入口：未发布（unpublished/withdrawn/archived）一律抛 NotFound（§12.1、§25.1）。"""
    if (
        resource.publication_status != PublicationStatus.PUBLISHED.value
        or resource.published_version_id is None
    ):
        raise NotFound("资源不存在")


async def mark_published(
    db: AsyncSession,
    *,
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    expected_revision: int,
    actor_id: uuid.UUID | None,
    request_id: str | None,
) -> None:
    """切换公开版本（发布或回滚）。公开指针必须指向本资源已通过审核的版本（§12.1）。"""
    resource = await get_resource(db, resource_id)
    version = await get_version(db, version_id)
    if version.resource_id != resource.id:
        raise NotFound("版本不属于该资源")
    if version.review_status != ReviewStatus.APPROVED.value:
        raise Conflict("公开版本必须指向已通过审核的版本", code="VERSION_NOT_APPROVED")

    pairs = await version_files(db, version.id)
    if not pairs or len([link for link, _ in pairs if link.purpose == FilePurpose.PRIMARY.value]) != 1:
        raise Conflict("发布版本缺少唯一主文件", code="FILE_NOT_READY")
    if any(file.scan_status != ScanStatus.PASSED.value for _, file in pairs):
        raise Conflict("文件尚未通过安全检查，不能发布", code="FILE_NOT_READY")
    rights = await db.scalar(select(RightsDeclaration).where(RightsDeclaration.version_id == version.id))
    if rights is None or rights.rights_basis not in ("original", "authorized", "open_license"):
        raise Conflict("缺少有效展示与传播许可，不能发布", code="DISTRIBUTION_NOT_ALLOWED")

    result = await db.execute(
        update(Resource)
        .where(Resource.id == resource_id, Resource.revision == expected_revision)
        .values(
            published_version_id=version_id,
            publication_status=PublicationStatus.PUBLISHED.value,
            revision=Resource.revision + 1,
        )
    )
    if result.rowcount == 0:
        raise Conflict("资源已被其他操作更新，请刷新后重试", code="STALE_REVISION")

    await record_audit(
        db,
        action="resource.published",
        object_type="resource",
        object_id=resource_id,
        object_version=expected_revision + 1,
        actor_id=actor_id,
        after={"published_version_id": str(version_id), "version_no": version.version_no},
        request_id=request_id,
    )
    await emit_event(
        db,
        event_type=DomainEventType.RESOURCE_PUBLISHED,
        subject_type="resource",
        subject_id=resource_id,
        subject_version=version.version_no,
        payload={"resource_id": str(resource_id), "version_id": str(version_id)},
    )


async def mark_withdrawn(
    db: AsyncSession,
    *,
    resource_id: uuid.UUID,
    expected_revision: int,
    actor_id: uuid.UUID | None,
    reason: str,
    request_id: str | None,
) -> None:
    """撤回：不物理删除文件（§13.3）。"""
    resource = await get_resource(db, resource_id)
    before = resource.publication_status
    result = await db.execute(
        update(Resource)
        .where(Resource.id == resource_id, Resource.revision == expected_revision)
        .values(
            publication_status=PublicationStatus.WITHDRAWN.value,
            revision=Resource.revision + 1,
        )
    )
    if result.rowcount == 0:
        raise Conflict("资源已被其他操作更新，请刷新后重试", code="STALE_REVISION")

    await record_audit(
        db,
        action="resource.withdrawn",
        object_type="resource",
        object_id=resource_id,
        object_version=expected_revision + 1,
        actor_id=actor_id,
        before={"publication_status": before},
        after={"publication_status": PublicationStatus.WITHDRAWN.value},
        reason=reason,
        request_id=request_id,
    )
    await emit_event(
        db,
        event_type=DomainEventType.RESOURCE_WITHDRAWN,
        subject_type="resource",
        subject_id=resource_id,
        payload={"resource_id": str(resource_id), "reason": reason},
    )


async def set_freshness(
    db: AsyncSession,
    *,
    resource_id: uuid.UUID,
    status: FreshnessStatus | str,
    actor_id: uuid.UUID | None,
) -> None:
    """时效标签，不自动导致删除（§13.2）。"""
    resource = await get_resource(db, resource_id)
    value = FreshnessStatus(status).value
    before = resource.freshness_status
    resource.freshness_status = value
    resource.revision += 1
    await record_audit(
        db,
        action="resource.freshness_changed",
        object_type="resource",
        object_id=resource_id,
        object_version=resource.revision,
        actor_id=actor_id,
        before={"freshness_status": before},
        after={"freshness_status": value},
    )


async def set_version_review_status(
    db: AsyncSession,
    *,
    version_id: uuid.UUID,
    status: ReviewStatus | str,
    actor_id: uuid.UUID | None,
    request_id: str | None = None,
) -> None:
    """审核结论投影到版本审核状态（§13.2）。

    `resource_versions.review_status` 属于 content，moderation 只能经本函数改写（§2.5）。
    以 `pending` 为条件更新：两名审核员并发时只有一个决定生效，过期的那个抛冲突（§13.3、§29）。
    """
    value = ReviewStatus(status).value
    result = await db.execute(
        update(ResourceVersion)
        .where(
            ResourceVersion.id == version_id,
            ResourceVersion.review_status == ReviewStatus.PENDING.value,
        )
        .values(review_status=value, revision=ResourceVersion.revision + 1)
    )
    if result.rowcount == 0:
        raise Conflict("版本审核状态已变化，无法写入审核结论", code="STALE_VERSION")

    await record_audit(
        db,
        action="version.review_status_changed",
        object_type="resource_version",
        object_id=version_id,
        actor_id=actor_id,
        before={"review_status": ReviewStatus.PENDING.value},
        after={"review_status": value},
        request_id=request_id,
    )


async def reassign_resource_course(
    db: AsyncSession,
    *,
    resource_id: uuid.UUID,
    course_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    reason: str,
    request_id: str | None = None,
) -> None:
    """更正资源课程归属（§11.2、§12.1）。

    课程归属是资源级关键字段，修改需要管理员授权并留痕，不能借编辑绕过审核。
    资源必须始终有明确课程归属，因此这里不接受清空。
    """
    from app.modules.catalog.service import get_course

    await get_course(db, course_id)
    resource = await get_resource(db, resource_id)
    before_course = resource.course_id
    if before_course == course_id:
        raise Conflict("资源已归属该课程", code="NO_CHANGE")

    # 开课实例必须与资源课程一致（§11.2）：改课程后原 offering 不再成立，一并清空。
    expected_revision = resource.revision
    result = await db.execute(
        update(Resource)
        .where(Resource.id == resource_id, Resource.revision == expected_revision)
        .values(course_id=course_id, offering_id=None, revision=Resource.revision + 1)
    )
    if result.rowcount == 0:
        raise Conflict("资源已被其他操作更新，请刷新后重试", code="STALE_REVISION")

    await record_audit(
        db,
        action="resource.course_reassigned",
        object_type="resource",
        object_id=resource_id,
        object_version=expected_revision + 1,
        actor_id=actor_id,
        before={"course_id": str(before_course), "offering_id": str(resource.offering_id)},
        after={"course_id": str(course_id), "offering_id": None},
        reason=reason,
        request_id=request_id,
    )


# --------------------------------------------------------------------------- #
# 内部辅助
# --------------------------------------------------------------------------- #
async def correct_classification(
    db, *, resource_id, version_id, course_id, resource_type,
    expected_revision, actor_id, reason, request_id,
):
    resource = await get_resource(db, resource_id)
    if resource.published_version_id != version_id:
        raise Conflict("公开版本已变化，请重新核对纠错", code="STALE_VERSION")
    values = {"revision": Resource.revision + 1}
    before = {"course_id": str(resource.course_id), "resource_type": resource.resource_type,
              "offering_id": str(resource.offering_id) if resource.offering_id else None}
    if course_id is not None:
        from app.modules.catalog.service import get_course
        course = await get_course(db, course_id)
        if course.status != "active":
            raise InvalidInput("请选择有效课程")
        values["course_id"] = course_id
        if course_id != resource.course_id:
            values["offering_id"] = None
    if resource_type is not None:
        values["resource_type"] = ResourceType(resource_type).value
    result = await db.execute(update(Resource).where(
        Resource.id == resource_id, Resource.revision == expected_revision,
        Resource.published_version_id == version_id,
    ).values(**values))
    if result.rowcount != 1:
        raise Conflict("资料已更新，请刷新后重试", code="STALE_REVISION")
    await record_audit(db, actor_id=actor_id, action="resource.classification_corrected",
                       object_type="resource", object_id=resource_id, before=before,
                       after={k: str(v) for k, v in values.items() if k != "revision"},
                       reason=reason, request_id=request_id)


async def _owner_display_name(db: AsyncSession, owner_id: uuid.UUID) -> str:
    """贡献者公开显示名；账号已注销时用匿名占位，不泄露账号信息（§24.1、§29）。"""
    from app.modules.identity.service import display_name_of, get_users

    users = await get_users(db, [owner_id])
    owner = users.get(owner_id)
    return display_name_of(owner) if owner is not None else "用户"


def _version_out(version: ResourceVersion) -> VersionOut:
    return VersionOut(
        id=version.id,
        resource_id=version.resource_id,
        version_no=version.version_no,
        title=version.title,
        description=version.description,
        change_summary=version.change_summary,
        review_status=ReviewStatus(version.review_status),
        preview_disabled=version.preview_disabled,
        submitted_at=version.submitted_at,
        revision=version.revision,
        created_at=version.created_at,
        updated_at=version.updated_at,
    )


def _file_out(link: VersionFile, file: ResourceFile) -> FileOut:
    return FileOut(
        id=file.id,
        original_name=file.original_name,
        detected_mime=file.detected_mime,
        size=file.size,
        sha256=file.sha256,
        page_count=file.page_count,
        scan_status=ScanStatus(file.scan_status),
        purpose=FilePurpose(link.purpose),
        display_order=link.display_order,
        created_at=file.created_at,
    )


async def _is_manager(db: AsyncSession, user) -> bool:
    if user is None:
        return False
    granted = await effective_permissions(db, user)
    return RESOURCE_REVIEW in granted or RESOURCE_PUBLISH in granted


async def _preview_ready(db: AsyncSession, version_id: uuid.UUID) -> bool:
    primary_ids = select(VersionFile.file_id).where(
        VersionFile.version_id == version_id, VersionFile.purpose == FilePurpose.PRIMARY.value
    )
    count = await db.scalar(
        select(func.count())
        .select_from(FileDerivative)
        .where(
            FileDerivative.source_file_id.in_(primary_ids),
            FileDerivative.status == DerivativeStatus.READY.value,
            FileDerivative.kind.in_(
                [DerivativeKind.PREVIEW_PDF.value, DerivativeKind.THUMBNAIL.value]
            ),
        )
    )
    return bool(count)


async def _resource_download_count(db: AsyncSession, resource_id: uuid.UUID) -> int:
    """下载计数：只统计 consumed 状态的 download_grants（§17.2）。"""
    count = await db.scalar(
        select(func.count())
        .select_from(_DOWNLOAD_GRANTS)
        .where(
            _DOWNLOAD_GRANTS.c.resource_id == resource_id,
            _DOWNLOAD_GRANTS.c.status == GrantStatus.CONSUMED.value,
        )
    )
    return int(count or 0)


async def _resource_favorite_count(db: AsyncSession, resource_id: uuid.UUID) -> int:
    """收藏计数：favorites 行数（§20.1）。"""
    count = await db.scalar(
        select(func.count())
        .select_from(_FAVORITES)
        .where(_FAVORITES.c.resource_id == resource_id)
    )
    return int(count or 0)


def _hot_score_expression(db: AsyncSession, as_of: datetime):
    """资源热门分（beta14 sort=hot）：互动量按创建时间衰减，全程在 SQL 内计算。

    score = 互动量 / (age_days + HOT_SCORE_TIME_DECAY_DAYS) ^ HOT_SCORE_DECAY_POWER，
    互动量 = favorites 计数 + 已消费 download_grants 计数；age 从 Resource.created_at 起算。
    系数取自主控统一配置（app/config.py）；SQLite 用 julianday、PG 用 extract(epoch)，
    两者方言均内置 pow，避免把整表拉回内存排序。
    """
    decay_days = settings.HOT_SCORE_TIME_DECAY_DAYS
    decay_power = settings.HOT_SCORE_DECAY_POWER
    engagement = func.coalesce(
        select(func.count()).select_from(_FAVORITES)
        .where(_FAVORITES.c.resource_id == Resource.id).scalar_subquery(),
        0,
    ) + func.coalesce(
        select(func.count()).select_from(_DOWNLOAD_GRANTS)
        .where(
            _DOWNLOAD_GRANTS.c.resource_id == Resource.id,
            _DOWNLOAD_GRANTS.c.status == GrantStatus.CONSUMED.value,
        ).scalar_subquery(),
        0,
    )
    if db.get_bind().dialect.name == "sqlite":
        age_days = func.julianday(as_of) - func.julianday(Resource.created_at)
    else:
        age_days = (as_of.timestamp() - func.extract("epoch", Resource.created_at)) / 86400.0
    age_days = case((age_days < 0, 0.0), else_=age_days)
    return engagement / func.pow(age_days + decay_days, decay_power)


async def _load_owned_files(
    db: AsyncSession, *, user, files: list[VersionFileIn]
) -> None:
    ids = [item.file_id for item in files]
    if len(set(ids)) != len(ids):
        raise InvalidInput("同一文件不能重复关联")
    if len([item for item in files if item.purpose == FilePurpose.PRIMARY]) > 1:
        raise InvalidInput("一个版本最多只能有一个主文件")
    if not ids:
        return
    rows = (await db.execute(select(ResourceFile).where(ResourceFile.id.in_(ids)))).scalars().all()
    found = {row.id: row for row in rows}
    for item in files:
        row = found.get(item.file_id)
        # 不得通过“文件已存在”泄露他人上传（§12.2）。
        if row is None or row.uploader_id != user.id:
            raise NotFound("上传文件不存在或不属于你")


async def _sync_version_files(
    db: AsyncSession, *, version: ResourceVersion, user, files: list[VersionFileIn]
) -> None:
    await _load_owned_files(db, user=user, files=files)
    await db.execute(delete(VersionFile).where(VersionFile.version_id == version.id))
    for index, item in enumerate(files):
        db.add(
            VersionFile(
                version_id=version.id,
                file_id=item.file_id,
                purpose=item.purpose.value,
                display_order=item.display_order or index,
            )
        )


async def _sync_rights(
    db: AsyncSession, *, version: ResourceVersion, user, rights: RightsDeclarationIn | None
) -> None:
    if rights is None:
        return
    if rights.rights_basis != RightsBasis.ORIGINAL and not (rights.source or "").strip():
        raise InvalidInput("该来源类型必须填写来源说明")
    await db.execute(delete(RightsDeclaration).where(RightsDeclaration.version_id == version.id))
    db.add(
        RightsDeclaration(
            version_id=version.id,
            declarant_id=user.id,
            rights_basis=rights.rights_basis.value,
            source=rights.source,
            original_author=rights.original_author,
            license=rights.license,
            declaration_text_version=rights.declaration_text_version,
            declared_at=_now(),
        )
    )


async def _sync_tags(db: AsyncSession, *, resource: Resource, tags: list[str] | None) -> None:
    if tags is None:
        return
    await db.execute(delete(ResourceTag).where(ResourceTag.resource_id == resource.id))
    seen: set[str] = set()
    for raw in tags:
        name = raw.strip()
        if not name:
            continue
        slug = _slugify(name)
        if slug in seen:
            continue
        seen.add(slug)
        tag = (await db.execute(select(Tag).where(Tag.slug == slug))).scalar_one_or_none()
        if tag is None:
            tag = Tag(name=name[:64], slug=slug)
            db.add(tag)
            await db.flush()
        db.add(ResourceTag(resource_id=resource.id, tag_id=tag.id))


async def _next_version_no(db: AsyncSession, resource_id: uuid.UUID) -> int:
    current = await db.scalar(
        select(func.max(ResourceVersion.version_no)).where(ResourceVersion.resource_id == resource_id)
    )
    return (current or 0) + 1


async def _ensure_owner(db: AsyncSession, resource: Resource, user) -> None:
    if resource.owner_id != user.id and user.role != "admin":
        # owner 判定之外只追加分配判定；审核权限语义不变（§3.3）。
        from app.core.enums import AssignmentCapability
        from app.modules.assignment.service import can_manage_resource

        if not await can_manage_resource(db, user, resource.id, AssignmentCapability.MAINTAIN):
            # 私有对象统一 404，避免泄露存在性（§25.1）。
            raise NotFound("资源不存在")


async def _validate_offering(db: AsyncSession, *, course_id: uuid.UUID, offering_id: uuid.UUID | None) -> None:
    from app.modules.catalog.service import get_course, get_offering

    await get_course(db, course_id)
    if offering_id is not None:
        offering = await get_offering(db, offering_id)
        # 若指定开课实例，其 course_id 必须与资源课程一致（§11.2）。
        if offering.course_id != course_id:
            raise InvalidInput("开课实例与资源课程不一致", details={"reason": "offering_course_mismatch"})


# --------------------------------------------------------------------------- #
# 资源与版本
# --------------------------------------------------------------------------- #
async def create_resource(
    db: AsyncSession, *, user, payload, request_id: str | None
) -> ResourceCreatedOut:
    """创建资源及首个草稿版本（§16 步骤 6）。"""
    await _validate_offering(db, course_id=payload.course_id, offering_id=payload.offering_id)
    resource = Resource(
        course_id=payload.course_id,
        offering_id=payload.offering_id,
        owner_id=user.id,
        resource_type=payload.resource_type.value,
        publication_status=PublicationStatus.UNPUBLISHED.value,
        freshness_status=FreshnessStatus.CURRENT.value,
    )
    db.add(resource)
    await db.flush()

    from app.modules.access.service import initialize_policy
    resource.access_policy_id = await initialize_policy(db, resource.id)

    version = ResourceVersion(
        resource_id=resource.id,
        version_no=1,
        title=payload.title,
        description=payload.description,
        change_summary=payload.change_summary,
        review_status=ReviewStatus.DRAFT.value,
        preview_disabled=payload.preview_disabled,
        created_by=user.id,
    )
    db.add(version)
    await db.flush()
    await _sync_version_files(db, version=version, user=user, files=payload.files)
    await _sync_rights(db, version=version, user=user, rights=payload.rights)
    await _sync_tags(db, resource=resource, tags=payload.tags)
    await record_audit(
        db,
        action="resource.created",
        object_type="resource",
        object_id=resource.id,
        object_version=1,
        actor_id=user.id,
        after={"course_id": str(resource.course_id), "version_no": 1},
        request_id=request_id,
    )
    return ResourceCreatedOut(
        resource_id=resource.id,
        version_id=version.id,
        version_no=version.version_no,
        publication_status=PublicationStatus(resource.publication_status),
        review_status=ReviewStatus(version.review_status),
        revision=resource.revision,
    )


async def create_version(
    db: AsyncSession, *, user, resource_id: uuid.UUID, payload, request_id: str | None
) -> VersionOut:
    """创建修订草稿；被驳回后修订形成新的可追溯提交（§12.1）。"""
    resource = await get_resource(db, resource_id)
    await _ensure_owner(db, resource, user)
    version = ResourceVersion(
        resource_id=resource.id,
        version_no=await _next_version_no(db, resource.id),
        title=payload.title,
        description=payload.description,
        change_summary=payload.change_summary,
        review_status=ReviewStatus.DRAFT.value,
        preview_disabled=payload.preview_disabled,
        created_by=user.id,
    )
    db.add(version)
    await db.flush()
    await _sync_version_files(db, version=version, user=user, files=payload.files)
    await _sync_rights(db, version=version, user=user, rights=payload.rights)
    if payload.tags:
        await _sync_tags(db, resource=resource, tags=payload.tags)
    await db.refresh(version)
    await record_audit(
        db,
        action="resource.version_created",
        object_type="resource_version",
        object_id=version.id,
        object_version=version.version_no,
        actor_id=user.id,
        after={"resource_id": str(resource.id), "version_no": version.version_no},
        request_id=request_id,
    )
    return _version_out(version)


async def update_version(
    db: AsyncSession,
    *,
    user,
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    payload,
    request_id: str | None,
) -> VersionOut:
    """修改本人可编辑草稿；用 revision 做条件更新，过期提交返回 409（§13.3）。"""
    resource = await get_resource(db, resource_id)
    await _ensure_owner(db, resource, user)
    version = await get_version(db, version_id)
    if version.resource_id != resource.id:
        raise NotFound("版本不属于该资源")
    if version.review_status != ReviewStatus.DRAFT.value:
        raise Conflict("仅草稿可以编辑；已提交版本请创建新修订", code="VERSION_FROZEN")

    data = payload.model_dump(exclude_unset=True)
    values: dict[str, object] = {"revision": ResourceVersion.revision + 1}
    if data.get("title") is not None:
        values["title"] = data["title"]
    if "description" in data:
        values["description"] = data["description"]
    if "change_summary" in data:
        values["change_summary"] = data["change_summary"]
    if data.get("preview_disabled") is not None:
        values["preview_disabled"] = data["preview_disabled"]

    result = await db.execute(
        update(ResourceVersion)
        .where(ResourceVersion.id == version.id, ResourceVersion.revision == payload.revision)
        .values(**values)
    )
    if result.rowcount == 0:
        raise Conflict("版本已被修改，请刷新后重试", code="STALE_REVISION")

    if payload.files is not None:
        await _sync_version_files(db, version=version, user=user, files=payload.files)
    if payload.rights is not None:
        await _sync_rights(db, version=version, user=user, rights=payload.rights)
    if payload.tags is not None:
        await _sync_tags(db, resource=resource, tags=payload.tags)

    await db.refresh(version)
    await record_audit(
        db,
        action="resource.version_updated",
        object_type="resource_version",
        object_id=version.id,
        object_version=version.version_no,
        actor_id=user.id,
        request_id=request_id,
    )
    return _version_out(version)


async def submit_version(
    db: AsyncSession,
    *,
    user,
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    request_id: str | None,
) -> SubmitOut:
    """提交明确版本审核（§16 步骤 8）。"""
    from app.modules.moderation.service import open_review

    resource = await get_resource(db, resource_id)
    await _ensure_owner(db, resource, user)
    version = await get_version(db, version_id)
    if version.resource_id != resource.id:
        raise NotFound("版本不属于该资源")
    if version.review_status != ReviewStatus.DRAFT.value:
        raise Conflict("该版本已提交或已审核，请创建新修订", code="VERSION_FROZEN")
    if not version.title.strip():
        raise InvalidInput("提交前必须填写标题")

    pairs = await version_files(db, version.id)
    if not pairs:
        raise InvalidInput("版本至少需要一个文件")
    primaries = [link for link, _ in pairs if link.purpose == FilePurpose.PRIMARY.value]
    if len(primaries) != 1:
        raise InvalidInput("版本必须且只能有一个主文件")
    for _, file in pairs:
        if file.scan_status != ScanStatus.PASSED.value:
            raise Conflict("存在未通过安全检查的文件，暂时无法提交", code="FILE_NOT_READY")
        if file.uploader_id != user.id:
            raise Forbidden("文件不属于提交者")

    rights = await db.scalar(
        select(RightsDeclaration).where(RightsDeclaration.version_id == version.id)
    )
    if rights is None:
        raise InvalidInput("提交前必须填写来源声明")

    # D07：P1 要求有效预览；作者显式关闭预览时除外（§17.1）。
    if not version.preview_disabled and not await _preview_ready(db, version.id):
        raise Conflict("预览尚未就绪，请稍后再提交", code="PREVIEW_NOT_READY")

    submitted_at = _now()
    result = await db.execute(
        update(ResourceVersion)
        .where(
            ResourceVersion.id == version.id,
            ResourceVersion.revision == version.revision,
            ResourceVersion.review_status == ReviewStatus.DRAFT.value,
        )
        .values(
            review_status=ReviewStatus.PENDING.value,
            submitted_at=submitted_at,
            revision=ResourceVersion.revision + 1,
        )
    )
    if result.rowcount == 0:
        raise Conflict("版本状态已变化，请刷新后重试", code="STALE_VERSION")
    await db.refresh(version)

    review = await open_review(db, version_id=version.id, submitter_id=user.id)
    await record_audit(
        db,
        action="resource.version_submitted",
        object_type="resource_version",
        object_id=version.id,
        object_version=version.version_no,
        actor_id=user.id,
        after={"review_status": ReviewStatus.PENDING.value},
        request_id=request_id,
    )
    await emit_event(
        db,
        event_type=DomainEventType.RESOURCE_VERSION_SUBMITTED,
        subject_type="resource_version",
        subject_id=version.id,
        subject_version=version.version_no,
        payload={"resource_id": str(resource.id), "version_id": str(version.id)},
    )
    return SubmitOut(
        version_id=version.id,
        review_id=review.id,
        review_status=ReviewStatus(version.review_status),
        submitted_at=version.submitted_at,
    )


async def withdraw_resource(
    db: AsyncSession, *, user, resource_id: uuid.UUID, payload, request_id: str | None
) -> PublicationOut:
    """作者、管理员或受管资料管理员撤回；撤回不物理删除文件（§13.3）。

    权限判定 = owner ∨ 管理员 ∨ 该资源上有效的 `MANAGE` 分配。分配是 owner 之外的
    **追加**判定，不放宽审核权限：审核仍由 `RESOURCE_REVIEW` 决定（§3.3）。
    无权限时统一 404，不泄露资源是否存在（§25.1）；审计 actor 记实际操作人。
    """
    resource = await get_resource(db, resource_id)
    if resource.owner_id != user.id and user.role != "admin":
        from app.core.enums import AssignmentCapability
        from app.modules.assignment.service import can_manage_resource

        if not await can_manage_resource(db, user, resource.id, AssignmentCapability.MANAGE):
            raise NotFound("资源不存在")
    if resource.publication_status != PublicationStatus.PUBLISHED.value:
        raise Conflict("资源当前不处于已发布状态，无法撤回", code="NOT_PUBLISHED")
    await mark_withdrawn(
        db,
        resource_id=resource.id,
        expected_revision=payload.revision or resource.revision,
        actor_id=user.id,
        reason=payload.reason,
        request_id=request_id,
    )
    resource = await get_resource(db, resource_id)
    return PublicationOut(
        resource_id=resource.id,
        publication_status=PublicationStatus(resource.publication_status),
        published_version_id=resource.published_version_id,
        revision=resource.revision,
    )


async def get_resource_detail(db: AsyncSession, *, user, resource_id: uuid.UUID) -> ResourceDetailOut:
    from app.modules.access.service import authorize
    from app.core.enums import Capability
    from app.modules.interaction.service import resource_rating_summary

    resource = await get_resource(db, resource_id)
    from app.core.enums import AssignmentCapability
    from app.modules.assignment.service import can_manage_resource

    # owner 判定之外只追加分配判定，与列表/搜索使用同一作用域（§3.3）。
    manages = bool(user) and await can_manage_resource(
        db, user, resource.id, AssignmentCapability.MAINTAIN
    )
    is_public = (
        resource.publication_status == PublicationStatus.PUBLISHED.value
        and resource.published_version_id is not None
    )
    if not (is_public or manages or await _is_manager(db, user)):
        raise NotFound("资源不存在")

    version = await published_version(db, resource)
    if version is not None:
        await authorize(db, user=user, resource_id=resource.id, version_id=version.id,
                        capability=Capability.READ_METADATA)
    preview_available = False
    if version is not None and not version.preview_disabled:
        preview_available = await _preview_ready(db, version.id)
    download_count = await _resource_download_count(db, resource.id)
    favorite_count = await _resource_favorite_count(db, resource.id)
    return ResourceDetailOut(
        id=resource.id,
        course_id=resource.course_id,
        offering_id=resource.offering_id,
        owner_id=resource.owner_id,
        owner_display_name=await _owner_display_name(db, resource.owner_id),
        resource_type=resource.resource_type,
        publication_status=PublicationStatus(resource.publication_status),
        freshness_status=FreshnessStatus(resource.freshness_status),
        published_version_id=resource.published_version_id,
        revision=resource.revision,
        created_at=resource.created_at,
        updated_at=resource.updated_at,
        published_version=_version_out(version) if version else None,
        preview_available=preview_available,
        download_count=download_count,
        favorite_count=favorite_count,
        rating_summary=await resource_rating_summary(db, resource.id),
    )


async def list_resources(
    db: AsyncSession,
    *,
    user=None,
    course_id: uuid.UUID | None = None,
    college_id: uuid.UUID | None = None,
    offering_id: uuid.UUID | None = None,
    semester_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
    year: int | None = None,
    resource_type=None,
    keyword: str | None = None,
    sort: str = "latest",
    limit: int | None = None,
    cursor: str | None = None,
) -> ResourceListOut:
    """已发布资源列表；默认只返回已发布版本（§19.1）。

    ``sort=hot``（beta14）按热门分降序 + id 降序：互动量按创建时间衰减
    （``_hot_score_expression``），游标键为 score + id，与 latest/oldest 的
    时间游标不可混用；默认排序行为不变。
    """
    if sort not in ("latest", "oldest", "hot"):
        raise InvalidInput("不支持的排序方式", details={"allowed": ["latest", "oldest", "hot"]})
    size = normalize_page_size(limit)

    from app.modules.access.visibility import visible_resource_ids

    stmt = select(Resource).where(
        Resource.publication_status == PublicationStatus.PUBLISHED.value,
        Resource.id.in_(visible_resource_ids(user)),
    )
    if course_id is not None:
        stmt = stmt.where(Resource.course_id == course_id)
    if owner_id is not None:
        # 贡献者主页按作者过滤；可见范围仍由 visible_resource_ids 决定，不扩大权限。
        stmt = stmt.where(Resource.owner_id == owner_id)
    if offering_id is not None:
        stmt = stmt.where(Resource.offering_id == offering_id)
    if resource_type is not None:
        stmt = stmt.where(Resource.resource_type == getattr(resource_type, "value", resource_type))
    if college_id is not None:
        stmt = stmt.where(
            Resource.course_id.in_(
                select(_COURSES.c.id).where(_COURSES.c.owning_college_id == college_id)
            )
        )
    if semester_id is not None:
        stmt = stmt.where(
            Resource.offering_id.in_(
                select(_OFFERINGS.c.id).where(_OFFERINGS.c.semester_id == semester_id)
            )
        )
    if year is not None:
        stmt = stmt.where(
            Resource.offering_id.in_(
                select(_OFFERINGS.c.id)
                .join(_SEMESTERS, _OFFERINGS.c.semester_id == _SEMESTERS.c.id)
                .where(_SEMESTERS.c.academic_year == year)
            )
        )
    if keyword:
        pattern = f"%{keyword.strip()}%"
        stmt = stmt.where(
            Resource.id.in_(
                select(ResourceVersion.resource_id).where(
                    ResourceVersion.id == Resource.published_version_id,
                    ResourceVersion.title.ilike(pattern),
                )
            )
        )

    as_of = _now()
    if cursor:
        payload = decode_cursor(cursor)
        try:
            last_id = uuid.UUID(payload["id"])
            if sort == "hot":
                last_score = float(payload["s"])
                as_of = datetime.fromisoformat(payload["at"])
                if not math.isfinite(last_score) or last_score < 0 or as_of.tzinfo is None:
                    raise ValueError("invalid hot cursor")
            else:
                ts = datetime.fromisoformat(payload["ts"])
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidInput("cursor 无效") from exc
        if sort == "hot":
            # 固定首屏计分时间，避免时间衰减令已展示行落入后续页。
            hot_score = _hot_score_expression(db, as_of)
            stmt = stmt.where(
                or_(
                    hot_score < last_score,
                    and_(hot_score == last_score, Resource.id < last_id),
                ),
            )
        else:
            ts = _aware(ts)
            if sort == "latest":
                stmt = stmt.where(
                    or_(
                        Resource.updated_at < ts,
                        and_(Resource.updated_at == ts, Resource.id < last_id),
                    )
                )
            else:
                stmt = stmt.where(
                    or_(
                        Resource.updated_at > ts,
                        and_(Resource.updated_at == ts, Resource.id > last_id),
                    )
                )

    if sort == "hot":
        hot_score = _hot_score_expression(db, as_of)
        stmt = stmt.add_columns(hot_score.label("hot_score")).order_by(hot_score.desc(), Resource.id.desc())
    elif sort == "latest":
        stmt = stmt.order_by(Resource.updated_at.desc(), Resource.id.desc())
    else:
        stmt = stmt.order_by(Resource.updated_at.asc(), Resource.id.asc())

    result = await db.execute(stmt.limit(size + 1))
    scores = {}
    if sort == "hot":
        ranked = result.all()
        rows = [row[0] for row in ranked]
        scores = {row[0].id: float(row.hot_score) for row in ranked}
    else:
        rows = list(result.scalars().all())
    has_more = len(rows) > size
    rows = rows[:size]

    version_ids = [row.published_version_id for row in rows if row.published_version_id]
    versions: dict[uuid.UUID, ResourceVersion] = {}
    if version_ids:
        found = (
            await db.execute(select(ResourceVersion).where(ResourceVersion.id.in_(version_ids)))
        ).scalars().all()
        versions = {item.id: item for item in found}

    items = []
    for row in rows:
        version = versions.get(row.published_version_id) if row.published_version_id else None
        items.append(
            ResourceListItem(
                id=row.id,
                course_id=row.course_id,
                offering_id=row.offering_id,
                resource_type=row.resource_type,
                freshness_status=row.freshness_status,
                title=version.title if version else None,
                version_no=version.version_no if version else None,
                updated_at=row.updated_at,
            )
        )
    if sort == "hot" and has_more and rows:
        next_cursor = encode_cursor({"s": scores[rows[-1].id], "id": str(rows[-1].id), "at": as_of.isoformat()})
    else:
        next_cursor = (
            encode_cursor({"ts": rows[-1].updated_at.isoformat(), "id": str(rows[-1].id)})
            if has_more and rows
            else None
        )
    return ResourceListOut(items=items, next_cursor=next_cursor, has_more=has_more)


async def list_version_files(
    db: AsyncSession, *, user, resource_id: uuid.UUID, version_id: uuid.UUID
) -> VersionFilesOut:
    """版本文件清单；不返回 storage_key 或任何永久链接（§16）。"""
    resource = await get_resource(db, resource_id)
    version = await get_version(db, version_id)
    if version.resource_id != resource.id:
        raise NotFound("版本不属于该资源")
    from app.core.enums import AssignmentCapability
    from app.modules.assignment.service import can_manage_resource

    # owner 判定之外只追加分配判定，与列表/详情使用同一作用域（§3.3）。
    manages = bool(user) and await can_manage_resource(
        db, user, resource.id, AssignmentCapability.MAINTAIN
    )
    is_public = (
        resource.publication_status == PublicationStatus.PUBLISHED.value
        and resource.published_version_id == version.id
    )
    # 历史版本默认仅作者与有权限管理人员可见（§12.1、D08）。
    if not (manages or is_public or await _is_manager(db, user)):
        raise NotFound("版本不存在")

    from app.modules.access.service import authorize
    from app.core.enums import Capability
    await authorize(db, user=user, resource_id=resource.id, version_id=version.id,
                    capability=Capability.READ_METADATA)

    pairs = await version_files(db, version.id)
    return VersionFilesOut(
        resource_id=resource.id,
        version_id=version.id,
        items=[_file_out(link, file) for link, file in pairs],
    )


# --------------------------------------------------------------------------- #
# 上传会话（§16 步骤 1-4）
# --------------------------------------------------------------------------- #
async def create_upload_session(db: AsyncSession, *, user, payload: UploadCreateIn) -> UploadCreateOut:
    """创建上传会话（§16 步骤 1-4）。

    `resource_id` 非空时，owner 之外**追加**该资源上的有效 `MAINTAIN` 分配判定，
    使受管资料管理员能为受管资源上传文件。此处只放行「创建上传会话」，
    不放宽后续「提交版本」链路的既有权限规则（`_ensure_owner` 保持原样）。
    会话的 `uploader_id` 始终记实际创建人，不改变资源归属。
    """
    mime = payload.declared_mime.strip().lower()
    if mime not in settings.ALLOWED_UPLOAD_MIME:
        raise InvalidInput("不支持的文件类型", details={"allowed": settings.ALLOWED_UPLOAD_MIME})
    if payload.expected_size > settings.MAX_UPLOAD_SIZE_BYTES:
        limit_mib = settings.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)
        raise InvalidInput(f"单文件不能超过 {limit_mib} MiB")
    if payload.resource_id is not None:
        resource = await get_resource(db, payload.resource_id)
        if resource.owner_id != user.id:
            from app.core.enums import AssignmentCapability
            from app.modules.assignment.service import can_manage_resource

            if not await can_manage_resource(
                db, user, resource.id, AssignmentCapability.MAINTAIN
            ):
                raise NotFound("资源不存在")

    storage_key = build_storage_key("uploads", payload.original_name)
    session = UploadSession(
        uploader_id=user.id,
        resource_id=payload.resource_id,
        storage_key=storage_key,
        original_name=payload.original_name,
        declared_mime=mime,
        expected_size=payload.expected_size,
        status=UploadSessionStatus.INITIATED.value,
        expires_at=_now() + timedelta(seconds=settings.S3_PRESIGN_UPLOAD_SECONDS),
    )
    db.add(session)
    await db.flush()

    presigned = await storage.presign_upload(storage_key, mime)
    return UploadCreateOut(
        id=session.id,
        status=UploadSessionStatus(session.status),
        original_name=session.original_name,
        upload_url=presigned.url,
        content_type=mime,
        expires_at=presigned.expires_at,
    )


async def _session_file(db: AsyncSession, session_id: uuid.UUID) -> ResourceFile | None:
    return (
        await db.execute(select(ResourceFile).where(ResourceFile.upload_session_id == session_id))
    ).scalar_one_or_none()


def _upload_out(session: UploadSession, file: ResourceFile | None) -> UploadStatusOut:
    return UploadStatusOut(
        id=session.id,
        status=UploadSessionStatus(session.status),
        original_name=session.original_name,
        declared_mime=session.declared_mime,
        expected_size=session.expected_size,
        actual_size=session.actual_size,
        file_id=file.id if file else None,
        scan_status=ScanStatus(file.scan_status) if file else None,
        created_at=session.created_at,
        completed_at=session.completed_at,
    )


async def get_upload_session(db: AsyncSession, *, user, upload_id: uuid.UUID) -> UploadStatusOut:
    session = await db.get(UploadSession, upload_id)
    if session is None or session.uploader_id != user.id:
        raise NotFound("上传会话不存在")
    return _upload_out(session, await _session_file(db, session.id))


async def complete_upload(
    db: AsyncSession, *, user, upload_id: uuid.UUID, request_id: str | None
) -> UploadStatusOut:
    """核验对象存在、实际大小与会话归属，然后进入处理（§16 步骤 4-5）。"""
    from app.modules.jobs.service import enqueue

    session = await db.get(UploadSession, upload_id)
    if session is None or session.uploader_id != user.id:
        raise NotFound("上传会话不存在")
    if session.status in (UploadSessionStatus.EXPIRED.value, UploadSessionStatus.ABORTED.value):
        raise Conflict("上传会话已失效，请重新创建", code="UPLOAD_SESSION_INVALID")
    existing = await _session_file(db, session.id)
    if session.status in (UploadSessionStatus.RECEIVED.value, UploadSessionStatus.VERIFIED.value):
        if existing is not None:
            return _upload_out(session, existing)

    if _aware(session.expires_at) <= _now():
        session.status = UploadSessionStatus.EXPIRED.value
        raise Conflict("上传会话已过期，请重新创建", code="UPLOAD_SESSION_EXPIRED")

    head = await storage.head(session.storage_key)
    if head is None:
        raise Conflict("未检测到已上传的文件", code="UPLOAD_OBJECT_MISSING")
    if head.size <= 0:
        raise Conflict("上传内容为空", code="UPLOAD_EMPTY")
    if head.size > settings.MAX_UPLOAD_SIZE_BYTES:
        raise InvalidInput("文件超过大小上限")
    if head.size != session.expected_size:
        raise Conflict(
            "上传大小与声明不一致",
            code="UPLOAD_SIZE_MISMATCH",
            details={"expected": session.expected_size, "actual": head.size},
        )

    session.actual_size = head.size
    session.status = UploadSessionStatus.RECEIVED.value
    session.completed_at = _now()

    file = ResourceFile(
        uploader_id=user.id,
        upload_session_id=session.id,
        storage_key=session.storage_key,
        original_name=session.original_name,
        size=head.size,
        scan_status=ScanStatus.PENDING.value,
    )
    db.add(file)
    await db.flush()

    await enqueue(
        db,
        job_type=JobType.FILE_SCAN,
        target_id=file.id,
        payload={"file_id": str(file.id)},
        dedupe_key=f"file.scan:{file.id}",
    )
    await record_audit(
        db,
        action="upload.received",
        object_type="resource_file",
        object_id=file.id,
        actor_id=user.id,
        after={"size": file.size, "upload_session_id": str(session.id)},
        request_id=request_id,
    )
    return _upload_out(session, file)


# --------------------------------------------------------------------------- #
# 任务处理入口（FILE_SCAN / FILE_DERIVE）
# --------------------------------------------------------------------------- #
async def process_file_scan(db: AsyncSession, *, file_id: uuid.UUID) -> ScanStatus:
    """检查任务：自行计算哈希、识别真实格式、执行扫描（§16 步骤 5）。"""
    from app.modules.jobs.service import enqueue

    file = await db.scalar(select(ResourceFile).where(ResourceFile.id == file_id).with_for_update().execution_options(populate_existing=True))
    if file is None:
        raise NotFound("文件不存在")
    if file.scan_status == ScanStatus.PASSED.value:
        return ScanStatus.PASSED

    file.scan_status = ScanStatus.RUNNING.value
    await db.flush()

    verified_key = build_storage_key("originals", file.original_name)
    outcome: ScanOutcome = await scan_quarantined_object(
        file.storage_key, original_name=file.original_name, verified_storage_key=verified_key
    )
    if outcome.sha256:
        file.sha256 = outcome.sha256
    if outcome.size:
        file.size = outcome.size
    if outcome.detected_mime:
        file.detected_mime = outcome.detected_mime
    if outcome.page_count is not None:
        file.page_count = outcome.page_count
    file.scan_status = outcome.status.value
    file.scan_failure_reason = outcome.reason

    if outcome.status == ScanStatus.PASSED:
        # 扫描通过后才允许从隔离区转入私有区（§16）。
        file.storage_key = verified_key
        if file.upload_session_id:
            session = await db.get(UploadSession, file.upload_session_id)
            if session is not None:
                session.status = UploadSessionStatus.VERIFIED.value
        await enqueue(
            db,
            job_type=JobType.FILE_DERIVE,
            target_id=file.id,
            payload={"file_id": str(file.id)},
            dedupe_key=f"file.derive:{file.id}",
        )

    await emit_event(
        db,
        event_type=DomainEventType.FILE_SCAN_FINISHED,
        subject_type="resource_file",
        subject_id=file.id,
        payload={"file_id": str(file.id), "status": outcome.status.value, "reason": outcome.reason},
    )
    return outcome.status


async def process_file_derive(db: AsyncSession, *, file_id: uuid.UUID) -> DerivativeStatus:
    """预览派生任务；失败不改变原件审核事实，不假显示“预览就绪”（§13.1）。"""
    file = await db.scalar(select(ResourceFile).where(ResourceFile.id == file_id).with_for_update().execution_options(populate_existing=True))
    if file is None:
        raise NotFound("文件不存在")
    if file.scan_status != ScanStatus.PASSED.value:
        raise Conflict("文件尚未通过安全检查，不能生成预览", code="FILE_NOT_READY")

    kind = primary_kind(file.detected_mime)

    ready = await db.scalar(select(FileDerivative.id).where(
        FileDerivative.source_file_id == file.id, FileDerivative.kind == kind.value,
        FileDerivative.processor_version == PROCESSOR_VERSION, FileDerivative.status == DerivativeStatus.READY.value,
    ).limit(1))
    if ready is not None:
        return DerivativeStatus.READY

    with tempfile.TemporaryDirectory(prefix="njupt-derive-") as tmp:
        workdir = Path(tmp)
        source = workdir / "source"
        try:
            await _download_verified_source(file, source)
            artifacts = await build_derivatives(
                source, detected_mime=file.detected_mime or "", workdir=workdir
            )
        except DerivativeError as exc:
            db.add(
                FileDerivative(
                    source_file_id=file.id,
                    processor_version=PROCESSOR_VERSION,
                    kind=kind.value,
                    status=DerivativeStatus.FAILED.value,
                    failure_reason=str(exc)[:1000],
                )
            )
            await db.flush()
            return DerivativeStatus.FAILED

        # 成功得到完整产物后才替换旧的普通派生记录，失败不清空已有预览。
        await db.execute(delete(FileDerivative).where(
            FileDerivative.source_file_id == file.id,
            FileDerivative.kind.in_([DerivativeKind.PREVIEW_PDF.value, DerivativeKind.THUMBNAIL.value]),
        ))
        for artifact in artifacts:
            derivative_key = build_storage_key("derivatives", artifact.path.name)
            with artifact.path.open("rb") as handle:
                await storage.put_file(
                    derivative_key,
                    handle,
                    artifact.content_type,
                    bucket=settings.S3_BUCKET_PRIVATE,
                )
            db.add(
                FileDerivative(
                    source_file_id=file.id,
                    processor_version=PROCESSOR_VERSION,
                    kind=artifact.kind.value,
                    storage_key=derivative_key,
                    status=DerivativeStatus.READY.value,
                    page_count=artifact.page_count,
                )
            )
        await db.flush()
        await emit_event(
            db,
            event_type=DomainEventType.FILE_DERIVATIVE_READY,
            subject_type="resource_file",
            subject_id=file.id,
            payload={"file_id": str(file.id), "kinds": [item.kind.value for item in artifacts]},
        )
        return DerivativeStatus.READY


# --------------------------------------------------------------------------- #
# 按用户水印预览（prevew.md 五、十二）
# --------------------------------------------------------------------------- #
def user_short_code(user_id: uuid.UUID, *, length: int = 8) -> str:
    """用户水印短码：HMAC-SHA256(SECRET_KEY, user_id) 前 8 位十六进制。

    不用 UUID 前缀：任何人拿到短码都无法对着用户表枚举反查本人（prevew.md 十二）；
    复用现有 SECRET_KEY，不引入新的密钥配置。
    """
    import hashlib
    import hmac

    digest = hmac.new(settings.SECRET_KEY.encode("utf-8"), user_id.bytes, hashlib.sha256)
    return digest.hexdigest()[:length].upper()


async def _ensure_watermarked_pages(
    db: AsyncSession,
    *,
    source_file: ResourceFile,
    user,
    max_pages: int,
    build,
) -> list[FileDerivative]:
    """生成/复用前 N 页水印图片预览；失败返回空列表，由调用方回退既有预览。

    - 浏览器拿到的是 page1.webp…pageN.webp，权限隔离由服务端渲染保证。
    - 登录用户的水印含本人短码：同一文件不同用户是不同 FileDerivative 行，
      不在任何公开桶放无水印原图。
    - 匿名用户没有可绑定身份，使用固定平台水印并共享同一批派生对象；
      水印同样平铺，不能靠裁剪边缘去掉。
    - 开关关闭、格式不支持或生成失败时返回空列表，不阻断预览链路。
    """
    if not (settings.PREVIEW_IMAGE_ENABLED and settings.PREVIEW_WATERMARK_ENABLED):
        return []
    source_file = await db.scalar(
        select(ResourceFile)
        .where(ResourceFile.id == source_file.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if source_file is None or source_file.scan_status != ScanStatus.PASSED.value:
        return []
    mime = source_file.detected_mime
    if mime not in (PDF_MIME, PNG_MIME, JPEG_MIME, DOCX_MIME, PPTX_MIME):
        return []

    if user is None:
        # 匿名：固定平台水印 + 共享对象键；不写入任何个人标识。
        owner_key = "anon"
        watermark_text = settings.PREVIEW_ANONYMOUS_WATERMARK
    else:
        short_code = user_short_code(user.id)
        # 展示短码不作为所有权键，避免截断摘要碰撞后复用其他用户的水印。
        owner_key = user_short_code(user.id, length=64)
        watermark_text = f"U{short_code} · {_now().date().isoformat()}"

    existing = (
        (
            await db.execute(
                select(FileDerivative)
                .where(
                    FileDerivative.source_file_id == source_file.id,
                    FileDerivative.kind == DerivativeKind.WATERMARKED_PREVIEW.value,
                    FileDerivative.processor_version == PROCESSOR_VERSION,
                    FileDerivative.status == DerivativeStatus.READY.value,
                    FileDerivative.page_no.is_not(None),
                    FileDerivative.storage_key.like(f"%-{owner_key}.webp"),
                )
                .order_by(FileDerivative.page_no)
            )
        )
        .scalars()
        .all()
    )
    if existing:
        # 已就绪：直接复用，不重复渲染。
        return list(existing)

    with tempfile.TemporaryDirectory(prefix="njupt-wm-preview-") as tmp:
        workdir = Path(tmp)
        source = workdir / "source"
        try:
            await _download_verified_source(source_file, source)
        except DerivativeError:
            return []
        artifacts = await build(
            source,
            detected_mime=mime,
            workdir=workdir,
            watermark_text=watermark_text,
            max_pages=max_pages,
        )
        if not artifacts:
            return []
        rows: list[FileDerivative] = []
        for artifact in artifacts:
            page_no = artifact.page_no or 1
            # 键含用户短码与页码：同文件不同用户、不同页都是不同对象（prevew.md 十二）。
            derivative_key = f"derivatives/{uuid.uuid4().hex}-p{page_no}-{owner_key}.webp"
            with artifact.path.open("rb") as handle:
                await storage.put_file(
                    derivative_key,
                    handle,
                    artifact.content_type,
                    bucket=settings.S3_BUCKET_PRIVATE,
                )
            row = FileDerivative(
                source_file_id=source_file.id,
                processor_version=PROCESSOR_VERSION,
                kind=DerivativeKind.WATERMARKED_PREVIEW.value,
                storage_key=derivative_key,
                status=DerivativeStatus.READY.value,
                page_count=artifact.page_count,
                page_no=page_no,
            )
            db.add(row)
            rows.append(row)
        await db.flush()
        return rows


async def ensure_watermarked_preview_pages(
    db: AsyncSession, *, source_file: ResourceFile, user
) -> list[FileDerivative]:
    """多页水印图片预览（PDF / 图片 / Office 统一图片化）。"""
    return await _ensure_watermarked_pages(
        db,
        source_file=source_file,
        user=user,
        max_pages=settings.PREVIEW_IMAGE_MAX_PAGES,
        build=build_watermarked_pages,
    )


async def ensure_watermarked_preview(
    db: AsyncSession, *, source_file: ResourceFile, user
) -> FileDerivative | None:
    """兼容入口：只保证第一页水印图片（旧调用方与测试使用）。

    匿名用户现在也会得到带平台水印的第一页图片；如需回退既有受限预览，
    调用方仍按「无行则回退」处理。
    """
    pages = await _ensure_watermarked_pages(
        db,
        source_file=source_file,
        user=user,
        max_pages=1,
        build=_build_single_page,
    )
    return pages[0] if pages else None


async def _build_single_page(
    source: Path,
    *,
    detected_mime: str,
    workdir: Path,
    watermark_text: str,
    max_pages: int = 1,
) -> list:
    """单页构建适配：复用旧的 `build_watermarked_preview`，保持测试与回退语义。"""
    artifact = await build_watermarked_preview(
        source, detected_mime=detected_mime, workdir=workdir, watermark_text=watermark_text
    )
    return [artifact] if artifact is not None else []


async def _download_verified_source(file: ResourceFile, destination: Path) -> None:
    if not file.sha256:
        raise DerivativeError("缺少安全检查摘要，不能生成新的预览")
    try:
        with destination.open("wb") as handle:
            written = await storage.download_to_file(file.storage_key, handle, bucket=settings.S3_BUCKET_PRIVATE)
    except ObjectSizeExceeded as exc:
        raise DerivativeError("文件超过大小上限") from exc
    digest = hashlib.sha256()
    with destination.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if written != file.size or digest.hexdigest() != file.sha256:
        raise DerivativeError("文件字节与安全检查记录不一致")
