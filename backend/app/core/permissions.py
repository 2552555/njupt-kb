"""权限映射与依赖（设计文档 §15；用户权责体系 §3）。

首期在代码中集中定义权限映射，不建设任意策略脚本或通用规则引擎。

三级角色与既有枚举的对应（《用户权责、内容治理与授权收益体系设计文档》§3.1）：
- 普通用户 `Role.USER`；
- 资料管理员 `Role.CONTRIBUTOR`（入驻专业贡献者，只管理本人或明确分配的内容）；
- 系统管理员 `Role.REVIEWER`（仅审核类权限集合）与 `Role.ADMIN`（全权限）。
不新增第四层业务角色；`reviewer` 即「只持 content.review 等有限权限的系统管理员」。

`admin_permission_assignments`（assignment 模块）为按职责追加的有限权限，`valid_until`
到期即失效；本版仍保留 `Role.ADMIN` 的全权限语义，未启用对管理员的收窄模式。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import Request

from sqlalchemy import or_, select

from app.core.enums import AccountStatus, Role
from app.core.errors import Forbidden, Unauthenticated

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.identity.models import User

# 有限明确的操作集合（§15）
RESOURCE_SUBMIT = "resource.submit"
RESOURCE_REVISE = "resource.revise"
RESOURCE_WITHDRAW = "resource.withdraw"
RESOURCE_REVIEW = "resource.review"
RESOURCE_PUBLISH = "resource.publish"
REPORT_HANDLE = "report.handle"
CORRECTION_HANDLE = "correction.handle"
COMMENT_MODERATE = "comment.moderate"
REQUEST_HANDLE = "request.handle"
CATALOG_MANAGE = "catalog.manage"
OPS_VIEW = "ops.view"
USER_SUSPEND = "user.suspend"
ENTITLEMENT_GRANT = "entitlement.grant"
AUDIT_VIEW = "audit.view"
JOB_RETRY = "job.retry"
FINANCE_VIEW = "finance.view"
SETTLEMENT_MANAGE = "settlement.manage"
AI_INVOKE = "ai.invoke"
# --- beta15：用户权责体系 §3.2 权限矩阵 ---
CONTRIBUTOR_REVIEW = "contributor.review"
ASSIGNMENT_MANAGE = "assignment.manage"
CASE_HANDLE = "case.handle"
RIGHTS_NOTICE_HANDLE = "rights_notice.handle"
AGREEMENT_MANAGE = "agreement.manage"
PRICE_CONFIRM = "price.confirm"
LEDGER_VIEW = "ledger.view"
PAYOUT_MANAGE = "payout.manage"
METRICS_VIEW = "metrics.view"
# --- 0.0.3 主动防御（AI 对抗 §10）：诱饵、风险状态与策略维护 ---
DEFENSE_MANAGE = "defense.manage"
# --- 0.0.3 推荐与实验（ALLIN §21、§34）---
RECO_MANAGE = "reco.manage"

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        RESOURCE_SUBMIT,
        RESOURCE_REVISE,
        RESOURCE_WITHDRAW,
        RESOURCE_REVIEW,
        RESOURCE_PUBLISH,
        REPORT_HANDLE,
        CORRECTION_HANDLE,
        COMMENT_MODERATE,
        REQUEST_HANDLE,
        CATALOG_MANAGE,
        OPS_VIEW,
        USER_SUSPEND,
        ENTITLEMENT_GRANT,
        AUDIT_VIEW,
        JOB_RETRY,
        FINANCE_VIEW,
        SETTLEMENT_MANAGE,
        AI_INVOKE,
        CONTRIBUTOR_REVIEW,
        ASSIGNMENT_MANAGE,
        CASE_HANDLE,
        RIGHTS_NOTICE_HANDLE,
        AGREEMENT_MANAGE,
        PRICE_CONFIRM,
        LEDGER_VIEW,
        PAYOUT_MANAGE,
        METRICS_VIEW,
        DEFENSE_MANAGE,
        RECO_MANAGE,
    }
)

# §15 权限表：普通审核员可以批准或驳回，但不能调整财务、授予管理员权限或批量删除文件。
# beta15：审核员（系统管理员内部权限集合之一）可处理案件与侵权通知，仍不获得财务权限。
ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.USER: frozenset({RESOURCE_SUBMIT, RESOURCE_REVISE, RESOURCE_WITHDRAW}),
    Role.CONTRIBUTOR: frozenset({RESOURCE_SUBMIT, RESOURCE_REVISE, RESOURCE_WITHDRAW}),
    Role.REVIEWER: frozenset(
        {
            RESOURCE_SUBMIT,
            RESOURCE_REVISE,
            RESOURCE_WITHDRAW,
            RESOURCE_REVIEW,
            REPORT_HANDLE,
            CORRECTION_HANDLE,
            AUDIT_VIEW,
            CONTRIBUTOR_REVIEW,
            CASE_HANDLE,
            RIGHTS_NOTICE_HANDLE,
        }
    ),
    Role.ADMIN: ALL_PERMISSIONS,
}


def permissions_for(role: Role | str) -> frozenset[str]:
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return frozenset()


def has_permission(user: "User", permission: str) -> bool:
    if user.role == Role.ADMIN:
        return True
    return permission in permissions_for(user.role)


async def effective_permissions(db: "AsyncSession", user: "User") -> frozenset[str]:
    """角色权限 ∪ 未过期的按需授权（用户权责体系 §3.1、§15.2）。

    只追加 `ALL_PERMISSIONS` 中存在的权限名，忽略未知值；`valid_until` 到期或
    `revoked_at` 非空即失效。`Role.ADMIN` 仍按既有模型返回全权限。
    """
    if user.role == Role.ADMIN:
        return ALL_PERMISSIONS

    granted = set(permissions_for(user.role))
    from app.modules.assignment.models import AdminPermissionAssignment

    now = datetime.now(timezone.utc)
    rows = (
        (
            await db.execute(
                select(AdminPermissionAssignment.permission).where(
                    AdminPermissionAssignment.user_id == user.id,
                    AdminPermissionAssignment.scope == "global",
                    AdminPermissionAssignment.scope_catalog_id.is_(None),
                    AdminPermissionAssignment.scope_resource_id.is_(None),
                    AdminPermissionAssignment.revoked_at.is_(None),
                    or_(
                        AdminPermissionAssignment.valid_until.is_(None),
                        AdminPermissionAssignment.valid_until > now,
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    granted.update(p for p in rows if p in ALL_PERMISSIONS)
    return frozenset(granted)


def is_active_account(user: "User") -> bool:
    """暂停与封禁有原因与申诉入口，但冻结下载不必自动禁止登录查看申诉（§14.1）。"""
    return user.status in (AccountStatus.ACTIVE, AccountStatus.SUSPENDED)


def require(*permissions: str) -> Callable[[object], object]:
    """FastAPI 依赖工厂：require(RESOURCE_REVIEW)。

    按 `effective_permissions` 判定，即角色权限加上未过期的按需授权。
    """
    from fastapi import Depends

    from app.core.deps import get_current_user, get_current_session, get_db

    async def _dependency(
        request: Request,
        user: "User" = Depends(get_current_user),
        db: "AsyncSession" = Depends(get_db),
    ) -> "User":
        if user.status != AccountStatus.ACTIVE:
            raise Forbidden("当前账号不能执行此操作，请完成邮箱验证或联系管理员")
        granted = await effective_permissions(db, user)
        missing = [p for p in permissions if p not in granted]
        if missing:
            raise Forbidden("没有执行该操作的权限", details={"missing": missing})
        from app.config import settings
        sensitive = {ASSIGNMENT_MANAGE, USER_SUSPEND, ENTITLEMENT_GRANT, RESOURCE_PUBLISH,
                     AGREEMENT_MANAGE, PRICE_CONFIRM, SETTLEMENT_MANAGE, PAYOUT_MANAGE}
        if settings.is_prod and request.method in {"POST", "PUT", "PATCH", "DELETE"} and sensitive.intersection(permissions):
            from app.modules.identity.mfa import require_recent
            session = await get_current_session(request, db=db)
            await require_recent(db, user, session)
        return user

    return _dependency


def ensure(condition: bool, message: str = "没有执行该操作的权限") -> None:
    if not condition:
        raise Forbidden(message)


def ensure_authenticated(user: "User | None") -> "User":
    if user is None:
        raise Unauthenticated("需要登录")
    return user
