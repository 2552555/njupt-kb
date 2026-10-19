"""Metadata visibility as a database predicate, applied before pagination."""

from datetime import datetime, timezone

from sqlalchemy import Uuid, and_, column, DateTime, exists, or_, select, table, true, false

from app.core.enums import Role, ScopeType
from app.core.permissions import RESOURCE_REVIEW, has_permission, is_active_account
from app.modules.access.models import AccessPolicy, Entitlement


def visible_resource_ids(user):
    resources = table(
        "resources",
        column("id", Uuid),
        column("owner_id", Uuid),
        column("course_id", Uuid),
        column("published_version_id", Uuid),
    )
    policy = AccessPolicy
    # 资料管理员的明确分配范围（§3.3）：与列表/搜索/详情/导出共用同一作用域过滤。
    assignments = table(
        "resource_assignments",
        column("id", Uuid),
        column("user_id", Uuid),
        column("resource_id", Uuid),
        column("course_id", Uuid),
        column("effective_at", DateTime),
        column("revoked_at", DateTime),
    )
    allowed = or_(policy.id.is_(None), policy.metadata_scope == ScopeType.PUBLIC.value)
    if user is not None:
        if not is_active_account(user):
            return select(resources.c.id).where(false())
        if user.role == Role.ADMIN:
            allowed = true()
        else:
            now = datetime.now(timezone.utc)
            grant = exists(
                select(Entitlement.id).where(
                    Entitlement.user_id == user.id,
                    Entitlement.scope_id.in_(
                        [
                            resources.c.id,
                            resources.c.course_id,
                            resources.c.published_version_id,
                        ]
                    ),
                    Entitlement.revoked_at.is_(None),
                    or_(Entitlement.starts_at.is_(None), Entitlement.starts_at <= now),
                    or_(Entitlement.expires_at.is_(None), Entitlement.expires_at > now),
                )
            ).correlate(resources)
            assigned = exists(
                select(assignments.c.id).where(
                    assignments.c.user_id == user.id,
                    assignments.c.revoked_at.is_(None),
                    or_(
                        assignments.c.effective_at.is_(None),
                        assignments.c.effective_at <= now,
                    ),
                    or_(
                        assignments.c.resource_id == resources.c.id,
                        assignments.c.course_id == resources.c.course_id,
                    ),
                )
            ).correlate(resources)
            # verified_school remains closed until an actual verification scheme is configured.
            allowed = or_(
                allowed,
                resources.c.owner_id == user.id,
                assigned,
                policy.metadata_scope == ScopeType.AUTHENTICATED.value,
                and_(policy.metadata_scope == ScopeType.EXPLICIT_GRANT.value, grant),
            )
            permissions = table(
                "admin_permission_assignments", column("user_id", Uuid),
                column("permission"), column("scope"), column("revoked_at", DateTime),
                column("valid_until", DateTime),
                column("scope_catalog_id", Uuid), column("scope_resource_id", Uuid),
            )
            review_permission = or_(
                true() if has_permission(user, RESOURCE_REVIEW) else false(),
                exists(select(permissions.c.user_id).where(
                    permissions.c.user_id == user.id,
                    permissions.c.permission == RESOURCE_REVIEW,
                    permissions.c.scope == "global",
                    permissions.c.scope_catalog_id.is_(None),
                    permissions.c.scope_resource_id.is_(None),
                    permissions.c.revoked_at.is_(None),
                    or_(permissions.c.valid_until.is_(None), permissions.c.valid_until > now),
                )),
            )
            if user is not None:
                reviews = table(
                    "reviews",
                    column("version_id", Uuid),
                    column("submitter_id", Uuid),
                    column("assigned_reviewer_id", Uuid),
                )
                work = exists(
                    select(reviews.c.version_id).where(
                        reviews.c.version_id == resources.c.published_version_id,
                        reviews.c.submitter_id != user.id,
                        or_(
                            reviews.c.assigned_reviewer_id.is_(None),
                            reviews.c.assigned_reviewer_id == user.id,
                        ),
                    )
                ).correlate(resources)
                allowed = or_(allowed, and_(review_permission, work))
    return (
        select(resources.c.id)
        .select_from(resources.outerjoin(policy, policy.resource_id == resources.c.id))
        .where(allowed)
    )
