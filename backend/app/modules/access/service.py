"""access 应用服务：权限判定、预览与下载授权、权益管理。

设计文档 §15（判定顺序与权益）、§17.2/§17.3（下载授权与凭证撤销）、
§29（一致性、并发与幂等）、§30（依赖故障不得默认放行）。

业务规则只在本文件；router 只做解析与调用。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import idempotency as idem
from app.core.audit import record_audit
from app.core.enums import (
    Capability,
    FilePurpose,
    GrantStatus,
    Role,
    ScanStatus,
    ScopeType,
)
from app.core.errors import Conflict, Forbidden, NotFound, RateLimited
from app.core.pagination import (
    CursorPage,
    decode_cursor,
    encode_cursor,
    normalize_page_size,
)
from app.core.permissions import RESOURCE_REVIEW, has_permission, is_active_account
from app.core.storage import storage
from app.modules.access.models import (
    AccessPolicy,
    DownloadGrant,
    DownloadQuotaUsage,
    Entitlement,
    PreviewGrant,
)
from app.modules.access.schemas import (
    AccessPolicyUpdate,
    DownloadGrantResult,
    EntitlementCreate,
    EntitlementOut,
    PreviewGrantResult,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.content.models import Resource, ResourceFile, ResourceVersion, VersionFile
    from app.modules.identity.models import User

# --- 固定策略参数（首期反滥用粗粒度限制，非收费额度；D05 不接现金/积分）---
DOWNLOAD_GRANT_SCOPE = "access.download-grant"
DOWNLOAD_GRANT_WINDOW_SECONDS = 3600  # 授权许可窗口；链接过期后可在此窗口内重新签发
DOWNLOAD_QUOTA_PERIOD_HOURS = 24
DOWNLOAD_QUOTA_MAX_PER_PERIOD = 100

# §18：来源允许的分发范围。原件下载只允许明确可分发的来源。
DISTRIBUTION_ALLOWED_BASES = frozenset({"original", "authorized", "open_license"})

_CAPABILITY_RANK: dict[str, int] = {
    Capability.READ_METADATA.value: 0,
    Capability.PREVIEW.value: 1,
    Capability.READ_FULL.value: 2,
    Capability.DOWNLOAD_ORIGINAL.value: 3,
}


# --------------------------------------------------------------------------
# 通用小工具
# --------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _rank(capability: object) -> int:
    return _CAPABILITY_RANK.get(str(capability), -1)


def _as_str(value: object) -> str:
    return str(value) if value is not None else ""


# --------------------------------------------------------------------------
# 判定顺序（§15.1）：账号状态 → 对象存在与发布状态 → 文件安全状态 →
# 内容来源允许的分发范围 → 访问策略 → 用户有效权益 →（配额与风险在 issue_download_grant）
# --------------------------------------------------------------------------
async def authorize(
    db: AsyncSession,
    *,
    user: "User | None",
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    file_id: uuid.UUID | None = None,
    capability: Capability,
) -> None:
    """唯一允许判定"能否读/预览/下载"的函数（docs/INTERFACES.md）。

    不通过时抛 Forbidden 或 NotFound，不返回布尔值。
    作者或管理员权限不能绕过恶意文件隔离、未授权商业传播等基础边界（§15.1）。
    """
    # 步骤 1：账号状态
    if user is not None and not is_active_account(user):
        raise Forbidden("账号当前状态不允许访问该内容", details={"status": _as_str(user.status)})

    # 步骤 2：对象存在与发布状态
    from app.modules.content.service import assert_visible, get_resource, get_version

    resource = await get_resource(db, resource_id)
    version = await get_version(db, version_id)
    if version.resource_id != resource_id:
        # 版本不属于该资源：不泄露存在性
        raise NotFound("资源版本不存在")

    bypass = await _management_bypass(db, user=user, resource=resource, version=version)
    if not bypass:
        # 可见性唯一入口：未发布一律 NotFound（§19.2、§25.1）
        await assert_visible(db, resource)
        if resource.published_version_id != version.id:
            # 默认仅公开当前版本（D08）；历史版本仅作者与有权限管理人员可见
            raise NotFound("资源版本不存在")

    # 步骤 3：文件安全状态（作者/管理员同样不得绕过）
    _, resource_file = await _resolve_file(db, version_id=version_id, file_id=file_id)
    _assert_file_safe(resource_file)

    # 步骤 4：内容来源允许的分发范围
    await _assert_distribution_allowed(db, version_id=version_id, capability=capability)

    if bypass:
        # 作者/管理人员在工作范围内：已完成 3、4 的基础边界校验
        return

    # 步骤 5：访问策略
    policy = await _load_policy(db, resource_id)
    if policy is None:
        if capability is Capability.READ_METADATA:
            # 已发布资源的公开元数据对所有角色开放（§15）；草稿已在上一步被 assert_visible 拦截
            return
        # 未知策略默认拒绝（§15.1）
        raise Forbidden("该内容未对当前账号开放")

    scope = _scope_for_capability(policy, capability)

    if scope is ScopeType.PUBLIC:
        return
    if user is None:
        raise Forbidden("需要登录后才能访问该内容")
    if scope is ScopeType.AUTHENTICATED:
        return
    if scope is ScopeType.VERIFIED_SCHOOL:
        # D03：身份校验方案确定前 verified_school 默认拒绝
        if await _is_school_verified(db, user):
            return
        raise Forbidden("该内容仅对已通过校园身份校验的用户开放")
    if scope is ScopeType.EXPLICIT_GRANT:
        # 步骤 6：用户有效权益
        if await _best_entitlement(db, user=user, resource=resource, version=version, capability=capability):
            return
        raise Forbidden("尚未获得该内容的访问权益")
    if scope is ScopeType.PRIVATE:
        raise Forbidden("该内容未对当前账号开放")

    # 未知策略默认拒绝
    raise Forbidden("该内容未对当前账号开放")


# --------------------------------------------------------------------------
# 预览授权（§17.1）
# --------------------------------------------------------------------------
async def issue_preview_grant(
    db: AsyncSession,
    *,
    user: "User | None",
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    request_id: str,
) -> PreviewGrantResult:
    """获得允许的预览访问凭证；只签发派生产物的短期 URL。"""
    await authorize(
        db, user=user, resource_id=resource_id, version_id=version_id, capability=Capability.PREVIEW
    )

    from app.modules.content.service import get_version

    version = await get_version(db, version_id)
    _, resource_file = await _resolve_file(db, version_id=version_id, file_id=None)

    derivative = await _find_preview_derivative(db, source_file_id=resource_file.id)
    if derivative is None:
        # 不假显示"预览就绪"（§13.1）
        raise NotFound("该内容的预览尚未就绪", code="PREVIEW_NOT_READY")
    derivative_id, storage_key = derivative

    signed = await storage.presign_download(storage_key, bucket=settings.S3_BUCKET_PRIVATE)
    grant = PreviewGrant(
        user_id=user.id if user is not None else None,
        resource_id=resource_id,
        version_id=version_id,
        file_id=resource_file.id,
        derivative_id=derivative_id,
        status=GrantStatus.ISSUED.value,
        valid_until=signed.expires_at,
    )
    db.add(grant)
    await db.flush()

    return PreviewGrantResult(
        grant_id=grant.id,
        preview_url=signed.url,
        url_expires_at=signed.expires_at,
        file_name=resource_file.original_name,
        version_no=version.version_no,
    )


# --------------------------------------------------------------------------
# 下载授权（§17.2 六步、§29 幂等）
# --------------------------------------------------------------------------
async def issue_download_grant(
    db: AsyncSession,
    *,
    user: "User",
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    file_id: uuid.UUID,
    idempotency_key: str,
    request_id: str,
) -> DownloadGrantResult:
    """为指定资源版本创建下载授权，支持 Idempotency-Key（§25.1、A10）。"""
    key = idem.normalize_key(idempotency_key)
    scope = f"{DOWNLOAD_GRANT_SCOPE}:{user.id}"
    payload = {
        "resource_id": str(resource_id),
        "version_id": str(version_id),
        "file_id": str(file_id),
    }

    # 重试：命中同一幂等键且请求摘要一致时返回同一业务授权结果
    cached = await idem.lookup(db, scope=scope, key=key, payload=payload, actor_id=user.id)
    if cached is not None:
        return DownloadGrantResult(**cached)

    # 已存在授权记录：在授权许可窗口内重新签发，不自动再次扣额度（§17.2）
    existing = await _grant_by_key(db, user_id=user.id, key=key)
    if existing is not None:
        if existing.request_hash != idem.request_hash(payload):
            # 相同幂等键用于不同请求 → 冲突（§25.1）
            raise Conflict("相同的 Idempotency-Key 已用于不同的请求", code="IDEMPOTENCY_KEY_REUSED")
        return await _reissue(
            db,
            grant=existing,
            user=user,
            resource_id=resource_id,
            version_id=version_id,
            file_id=file_id,
            scope=scope,
            key=key,
            payload=payload,
        )

    # 步骤 1–6：账号、资源、文件、来源、策略、权益
    await authorize(
        db,
        user=user,
        resource_id=resource_id,
        version_id=version_id,
        file_id=file_id,
        capability=Capability.DOWNLOAD_ORIGINAL,
    )
    # 步骤 7：配额与风险限制（依赖故障不得默认放行，§30）
    period_start = await _check_quota(db, user=user)

    from app.modules.content.service import get_resource, get_version

    resource = await get_resource(db, resource_id)
    version = await get_version(db, version_id)
    _, resource_file = await _resolve_file(db, version_id=version_id, file_id=file_id)
    policy = await _load_policy(db, resource_id)
    entitlement = await _best_entitlement(
        db, user=user, resource=resource, version=version, capability=Capability.DOWNLOAD_ORIGINAL
    )

    grant = DownloadGrant(
        user_id=user.id,
        resource_id=resource_id,
        version_id=version_id,
        file_id=resource_file.id,
        idempotency_key=key,
        request_hash=idem.request_hash(payload),
        entitlement_ref=entitlement.id if entitlement is not None else None,
        status=GrantStatus.ISSUED.value,
        valid_until=_now() + timedelta(seconds=DOWNLOAD_GRANT_WINDOW_SECONDS),
    )
    db.add(grant)
    try:
        # 唯一约束 (user_id, idempotency_key) 避免同一幂等键重复计次（§15.2）
        await db.flush()
    except IntegrityError:
        # 并发同键：回滚本次尝试（含额度占用），按既有授权重新签发
        await db.rollback()
        existing = await _grant_by_key(db, user_id=user.id, key=key)
        if existing is None:
            raise Conflict("下载授权并发冲突，请重试", code="DOWNLOAD_GRANT_CONFLICT")
        if existing.request_hash != idem.request_hash(payload):
            raise Conflict("相同的 Idempotency-Key 已用于不同的请求", code="IDEMPOTENCY_KEY_REUSED")
        return await _reissue(
            db,
            grant=existing,
            user=user,
            resource_id=resource_id,
            version_id=version_id,
            file_id=file_id,
            scope=scope,
            key=key,
            payload=payload,
        )

    # 同一授权最多占用一次额度（grant_id 唯一）
    db.add(
        DownloadQuotaUsage(
            user_id=user.id,
            period_start=period_start,
            policy_version=policy.revision if policy is not None else None,
            grant_id=grant.id,
            amount=1,
        )
    )

    result = await _sign_download(
        db, grant_id=grant.id, resource_file=resource_file, version_no=version.version_no
    )
    # 幂等结果与授权在同一事务内提交（§25.1）
    await idem.remember(
        db,
        scope=scope,
        key=key,
        payload=payload,
        actor_id=user.id,
        response_status=201,
        response=result.model_dump(mode="json"),
    )
    return result


async def _reissue(
    db: AsyncSession,
    *,
    grant: DownloadGrant,
    user: "User",
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    file_id: uuid.UUID,
    scope: str,
    key: str,
    payload: dict[str, str],
) -> DownloadGrantResult:
    """在授权许可窗口内重新签发链接；重新核验账号、资源与权益状态，不再次扣额度。"""
    if _as_str(grant.status) == GrantStatus.REVOKED.value:
        # 已撤回或失去权益时不能凭旧幂等键复活授权（§17.2）
        raise Forbidden("该下载授权已被撤销", code="GRANT_REVOKED")

    await authorize(
        db,
        user=user,
        resource_id=resource_id,
        version_id=version_id,
        file_id=file_id,
        capability=Capability.DOWNLOAD_ORIGINAL,
    )

    if _aware(grant.valid_until) <= _now():
        raise Conflict("下载授权许可窗口已过，请重新发起请求", code="GRANT_WINDOW_EXPIRED")

    from app.modules.content.service import get_version

    version = await get_version(db, version_id)
    _, resource_file = await _resolve_file(db, version_id=version_id, file_id=file_id)
    result = await _sign_download(
        db, grant_id=grant.id, resource_file=resource_file, version_no=version.version_no
    )
    await idem.remember(
        db,
        scope=scope,
        key=key,
        payload=payload,
        actor_id=user.id,
        response_status=200,
        response=result.model_dump(mode="json"),
    )
    return result


async def _sign_download(
    db: AsyncSession,
    *,
    grant_id: uuid.UUID,
    resource_file: "ResourceFile",
    version_no: int,
) -> DownloadGrantResult:
    """签发短期访问 URL。签名 URL 本身不写入普通日志（§17.2）。"""
    signed = await storage.presign_download(
        resource_file.storage_key,
        bucket=settings.S3_BUCKET_PRIVATE,
        file_name=resource_file.original_name,
    )
    return DownloadGrantResult(
        grant_id=grant_id,
        download_url=signed.url,
        url_expires_at=signed.expires_at,
        file_name=resource_file.original_name,
        version_no=version_no,
    )


# --------------------------------------------------------------------------
# 权益管理（§15）
# --------------------------------------------------------------------------
async def list_entitlements(
    db: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    limit: int | None,
    cursor: str | None,
) -> CursorPage[EntitlementOut]:
    size = normalize_page_size(limit)
    stmt = select(Entitlement).order_by(Entitlement.created_at.desc(), Entitlement.id.desc())
    if user_id is not None:
        stmt = stmt.where(Entitlement.user_id == user_id)
    if cursor:
        data = decode_cursor(cursor)
        ts = datetime.fromisoformat(data["ts"])
        cid = uuid.UUID(data["id"])
        stmt = stmt.where(
            or_(
                Entitlement.created_at < ts,
                and_(Entitlement.created_at == ts, Entitlement.id < cid),
            )
        )
    rows = list((await db.execute(stmt.limit(size + 1))).scalars().all())
    has_more = len(rows) > size
    items = rows[:size]
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor({"ts": last.created_at.isoformat(), "id": str(last.id)})
    return CursorPage[EntitlementOut](
        items=[EntitlementOut.model_validate(item) for item in items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def create_entitlement(
    db: AsyncSession,
    *,
    actor: "User",
    payload: EntitlementCreate,
    request_id: str,
) -> Entitlement:
    from app.modules.identity.service import get_user

    target = await get_user(db, payload.user_id)  # 找不到抛 NotFound

    entitlement = Entitlement(
        user_id=target.id,
        scope_type=_as_str(payload.scope_type),
        scope_id=payload.scope_id,
        capability=_as_str(payload.capability),
        source_type=_as_str(payload.source_type),
        source_id=payload.source_id,
        starts_at=payload.starts_at,
        expires_at=payload.expires_at,
    )
    db.add(entitlement)
    await db.flush()

    await record_audit(
        db,
        action="entitlement.grant",
        object_type="entitlement",
        object_id=entitlement.id,
        actor_id=actor.id,
        after={
            "user_id": str(target.id),
            "scope_type": entitlement.scope_type,
            "scope_id": str(entitlement.scope_id),
            "capability": entitlement.capability,
            "source_type": entitlement.source_type,
            "expires_at": entitlement.expires_at,
        },
        request_id=request_id,
    )
    return entitlement


async def revoke_entitlement(
    db: AsyncSession,
    *,
    actor: "User",
    entitlement_id: uuid.UUID,
    reason: str,
    request_id: str,
) -> Entitlement:
    entitlement = (
        await db.execute(select(Entitlement).where(Entitlement.id == entitlement_id))
    ).scalar_one_or_none()
    if entitlement is None:
        raise NotFound("权益不存在")
    if entitlement.revoked_at is not None:
        raise Conflict("该权益已撤销", code="ENTITLEMENT_ALREADY_REVOKED")

    before = {"revoked_at": None}
    entitlement.revoked_at = _now()
    entitlement.revoked_reason = reason
    await db.flush()

    await record_audit(
        db,
        action="entitlement.revoke",
        object_type="entitlement",
        object_id=entitlement.id,
        actor_id=actor.id,
        before=before,
        after={"revoked_at": entitlement.revoked_at, "reason": reason},
        reason=reason,
        request_id=request_id,
    )
    return entitlement


async def set_access_policy(
    db: AsyncSession,
    *,
    actor: "User",
    resource_id: uuid.UUID,
    payload: AccessPolicyUpdate,
    request_id: str,
) -> AccessPolicy:
    """设置资源访问范围；变更需审计（§15.1、§27）。"""
    from app.modules.content.service import get_resource

    await get_resource(db, resource_id)  # 找不到抛 NotFound

    policy = await _load_policy(db, resource_id)
    if policy is None:
        policy = AccessPolicy(
            resource_id=resource_id,
            metadata_scope=_as_str(payload.metadata_scope),
            preview_scope=_as_str(payload.preview_scope),
            original_scope=_as_str(payload.original_scope),
        )
        db.add(policy)
        await db.flush()
        action = "access_policy.create"
        before = None
    else:
        before = {
            "metadata_scope": policy.metadata_scope,
            "preview_scope": policy.preview_scope,
            "original_scope": policy.original_scope,
            "revision": policy.revision,
        }
        policy.metadata_scope = _as_str(payload.metadata_scope)
        policy.preview_scope = _as_str(payload.preview_scope)
        policy.original_scope = _as_str(payload.original_scope)
        policy.revision = (policy.revision or 1) + 1
        await db.flush()
        action = "access_policy.update"

    await record_audit(
        db,
        action=action,
        object_type="access_policy",
        object_id=policy.id,
        object_version=policy.revision,
        actor_id=actor.id,
        before=before,
        after={
            "resource_id": str(resource_id),
            "metadata_scope": policy.metadata_scope,
            "preview_scope": policy.preview_scope,
            "original_scope": policy.original_scope,
            "revision": policy.revision,
        },
        request_id=request_id,
    )
    return policy


async def get_access_policy(db: AsyncSession, resource_id: uuid.UUID) -> AccessPolicy | None:
    return await _load_policy(db, resource_id)


# --------------------------------------------------------------------------
# 内部辅助
# --------------------------------------------------------------------------
async def _load_policy(db: AsyncSession, resource_id: uuid.UUID) -> AccessPolicy | None:
    stmt = select(AccessPolicy).where(AccessPolicy.resource_id == resource_id)
    return (await db.execute(stmt)).scalar_one_or_none()


def _scope_for_capability(policy: AccessPolicy | None, capability: Capability) -> ScopeType:
    if policy is None:
        # 未知策略默认拒绝（§15.1）
        return ScopeType.PRIVATE
    if capability is Capability.READ_METADATA:
        raw = policy.metadata_scope
    elif capability is Capability.PREVIEW:
        raw = policy.preview_scope
    else:
        raw = policy.original_scope
    try:
        return ScopeType(raw)
    except ValueError:
        return ScopeType.PRIVATE


async def _management_bypass(
    db: AsyncSession,
    *,
    user: "User | None",
    resource: "Resource",
    version: "ResourceVersion",
) -> bool:
    """作者或管理人员的例外通道；不绕过文件隔离与来源分发边界（调用方仍执行步骤 3、4）。"""
    if user is None:
        return False
    if user.role == Role.ADMIN:
        return True
    if getattr(resource, "owner_id", None) == user.id:
        return True
    if has_permission(user, RESOURCE_REVIEW):
        # 审核员仅在工作范围内可见原件（§15）
        try:
            from app.modules.moderation.service import can_review
        except ImportError:
            return True
        return bool(await can_review(db, user, version.id))
    return False


async def _resolve_file(
    db: AsyncSession, *, version_id: uuid.UUID, file_id: uuid.UUID | None
) -> tuple["VersionFile", "ResourceFile"]:
    from app.modules.content.service import version_files

    entries = list(await version_files(db, version_id))
    if not entries:
        raise NotFound("该版本没有可用文件")
    if file_id is not None:
        for version_file, resource_file in entries:
            if resource_file.id == file_id:
                return version_file, resource_file
        raise NotFound("指定文件不属于该版本")
    entries.sort(
        key=lambda entry: (
            0 if _as_str(entry[0].purpose) == FilePurpose.PRIMARY.value else 1,
            entry[0].display_order or 0,
        )
    )
    return entries[0]


def _assert_file_safe(resource_file: "ResourceFile") -> None:
    status = _as_str(resource_file.scan_status)
    if status == ScanStatus.PASSED.value:
        return
    code = "FILE_REJECTED" if status == ScanStatus.REJECTED.value else "FILE_UNAVAILABLE"
    raise Forbidden("该文件当前不可下载", code=code, details={"scan_status": status})


async def _assert_distribution_allowed(
    db: AsyncSession, *, version_id: uuid.UUID, capability: Capability
) -> None:
    if capability is not Capability.DOWNLOAD_ORIGINAL:
        return
    basis = await _rights_basis(db, version_id)
    if basis is None:
        raise Forbidden("该内容缺少来源声明，暂不开放原件下载", code="DISTRIBUTION_NOT_ALLOWED")
    if basis not in DISTRIBUTION_ALLOWED_BASES:
        raise Forbidden(
            "内容来源不允许原件分发", code="DISTRIBUTION_NOT_ALLOWED", details={"rights_basis": basis}
        )


async def _rights_basis(db: AsyncSession, version_id: uuid.UUID) -> str | None:
    """读取内容来源允许的分发范围（§18）。

    跨模块只读：不导入 content 的模型类，按冻结表名与列名查询（ARCHITECTURE §3、设计 §12）。
    缺少声明时返回 None，由调用方按"未知默认拒绝"处理。
    """
    row = (
        await db.execute(
            text(
                "SELECT rights_basis FROM rights_declarations "
                "WHERE version_id = :version_id ORDER BY declared_at DESC LIMIT 1"
            ),
            {"version_id": version_id},
        )
    ).first()
    return str(row[0]) if row is not None and row[0] is not None else None


async def _find_preview_derivative(
    db: AsyncSession, *, source_file_id: uuid.UUID
) -> tuple[uuid.UUID, str] | None:
    """查找已就绪的预览派生产物（§17.1：预览是 FileDerivative，不是另一个内容版本）。

    跨模块只读：按冻结表名与列名查询，不导入 content 的模型类。
    """
    row = (
        await db.execute(
            text(
                "SELECT id, storage_key FROM file_derivatives "
                "WHERE source_file_id = :source_file_id AND status = 'ready' "
                "AND kind IN ('watermarked_preview', 'preview_pdf') "
                "ORDER BY CASE kind WHEN 'watermarked_preview' THEN 0 ELSE 1 END, created_at DESC "
                "LIMIT 1"
            ),
            {"source_file_id": source_file_id},
        )
    ).first()
    if row is None:
        return None
    return row[0], str(row[1])


async def _is_school_verified(db: AsyncSession, user: "User") -> bool:
    try:
        from app.modules.identity.service import is_school_verified
    except ImportError:
        # 身份模块不可用时不放行（D03、§30）
        return False
    return bool(await is_school_verified(db, user.id))


def _entitlement_active(entitlement: Entitlement, now: datetime) -> bool:
    if entitlement.revoked_at is not None:
        return False
    if entitlement.starts_at is not None and _aware(entitlement.starts_at) > now:
        return False
    if entitlement.expires_at is not None and _aware(entitlement.expires_at) <= now:
        return False
    return True


async def _best_entitlement(
    db: AsyncSession,
    *,
    user: "User | None",
    resource: "Resource",
    version: "ResourceVersion",
    capability: Capability,
) -> Entitlement | None:
    """用户有效权益（§15.1 步骤 6）：资源级、课程级或版本级，能力不低于所需。"""
    if user is None:
        return None

    scope_ids = {resource.id, version.id}
    course_id = getattr(resource, "course_id", None)
    if course_id is not None:
        scope_ids.add(course_id)

    stmt = select(Entitlement).where(
        Entitlement.user_id == user.id,
        Entitlement.scope_id.in_(scope_ids),
        Entitlement.revoked_at.is_(None),
    )
    candidates = list((await db.execute(stmt)).scalars().all())

    if settings.COMMERCE_ENABLED:
        # P3：订单/订阅产生的权益；未启用时不伪造（docs/INTERFACES.md）
        from app.modules.commerce.service import entitlement_grants

        candidates.extend(
            await entitlement_grants(
                db, user_id=user.id, scope_type=ScopeType.EXPLICIT_GRANT, scope_id=resource.id
            )
        )

    now = _now()
    required = _rank(capability)
    best: Entitlement | None = None
    for entitlement in candidates:
        if not _entitlement_active(entitlement, now):
            continue
        if _rank(entitlement.capability) < required:
            continue
        if best is None or _rank(entitlement.capability) > _rank(best.capability):
            best = entitlement
    return best


async def _check_quota(db: AsyncSession, *, user: "User") -> datetime:
    """步骤 7：配额与风险限制。依赖故障时不得默认通过（§30）。"""
    now = _now()
    period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    used = (
        await db.execute(
            select(func.coalesce(func.sum(DownloadQuotaUsage.amount), 0)).where(
                DownloadQuotaUsage.user_id == user.id,
                DownloadQuotaUsage.period_start == period_start,
            )
        )
    ).scalar_one()
    if int(used) >= DOWNLOAD_QUOTA_MAX_PER_PERIOD:
        raise RateLimited(
            "今日下载授权次数已达上限，请稍后再试",
            details={"limit": DOWNLOAD_QUOTA_MAX_PER_PERIOD},
        )
    return period_start


async def _grant_by_key(db: AsyncSession, *, user_id: uuid.UUID, key: str) -> DownloadGrant | None:
    stmt = select(DownloadGrant).where(
        DownloadGrant.user_id == user_id, DownloadGrant.idempotency_key == key
    )
    return (await db.execute(stmt)).scalar_one_or_none()
