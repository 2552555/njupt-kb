"""权限映射与依赖（设计文档 §15）。

首期在代码中集中定义权限映射，不建设任意策略脚本或通用规则引擎。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from app.core.enums import AccountStatus, Role
from app.core.errors import Forbidden, Unauthenticated

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.identity.models import User

# 有限明确的操作集合（§15）
RESOURCE_SUBMIT = "resource.submit"
RESOURCE_REVISE = "resource.revise"
RESOURCE_WITHDRAW = "resource.withdraw"
RESOURCE_REVIEW = "resource.review"
RESOURCE_PUBLISH = "resource.publish"
REPORT_HANDLE = "report.handle"
CORRECTION_HANDLE = "correction.handle"
CATALOG_MANAGE = "catalog.manage"
USER_SUSPEND = "user.suspend"
ENTITLEMENT_GRANT = "entitlement.grant"
AUDIT_VIEW = "audit.view"
JOB_RETRY = "job.retry"
FINANCE_VIEW = "finance.view"
SETTLEMENT_MANAGE = "settlement.manage"
AI_INVOKE = "ai.invoke"

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        RESOURCE_SUBMIT,
        RESOURCE_REVISE,
        RESOURCE_WITHDRAW,
        RESOURCE_REVIEW,
        RESOURCE_PUBLISH,
        REPORT_HANDLE,
        CORRECTION_HANDLE,
        CATALOG_MANAGE,
        USER_SUSPEND,
        ENTITLEMENT_GRANT,
        AUDIT_VIEW,
        JOB_RETRY,
        FINANCE_VIEW,
        SETTLEMENT_MANAGE,
        AI_INVOKE,
    }
)

# §15 权限表：普通审核员可以批准或驳回，但不能调整财务、授予管理员权限或批量删除文件。
ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.USER: frozenset(),
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


def is_active_account(user: "User") -> bool:
    """暂停与封禁有原因与申诉入口，但冻结下载不必自动禁止登录查看申诉（§14.1）。"""
    return user.status in (AccountStatus.ACTIVE, AccountStatus.SUSPENDED)


def require(*permissions: str) -> Callable[[object], object]:
    """FastAPI 依赖工厂：require(RESOURCE_REVIEW)。"""
    from fastapi import Depends

    from app.core.deps import get_current_user

    async def _dependency(user: "User" = Depends(get_current_user)) -> "User":
        if user.status == AccountStatus.BANNED:
            raise Forbidden("账号已被封禁")
        missing = [p for p in permissions if not has_permission(user, p)]
        if missing:
            raise Forbidden("没有执行该操作的权限", details={"missing": missing})
        return user

    return _dependency


def ensure(condition: bool, message: str = "没有执行该操作的权限") -> None:
    if not condition:
        raise Forbidden(message)


def ensure_authenticated(user: "User | None") -> "User":
    if user is None:
        raise Unauthenticated("需要登录")
    return user
