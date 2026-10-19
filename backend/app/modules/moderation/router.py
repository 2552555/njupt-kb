"""moderation 路由（设计文档 §25.2、§27）。

只做参数解析与调用 `service`；业务规则不在这里。
前缀由 `app/main.py` 统一加 `/api/v1`，本模块不自行决定前缀。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, get_request_id
from app.core.enums import FreshnessStatus, ReportStatus, ReviewStatus
from app.core.pagination import CursorPage
from app.core.permissions import (
    AUDIT_VIEW,
    CATALOG_MANAGE,
    REPORT_HANDLE,
    RESOURCE_PUBLISH,
    RESOURCE_REVIEW,
    USER_SUSPEND,
    require,
)
from app.modules.moderation import comment_reports, personal, service
from app.modules.moderation.models import AppealStatus
from app.modules.moderation.schemas import (
    AuditLogOut,
    CatalogCorrectionIn,
    CommentAppealCreateIn,
    CommentAppealDecisionIn,
    CommentAppealOut,
    CommentReportCreateIn,
    CommentReportDecisionIn,
    CommentReportOut,
    FreshnessIn,
    ModerationDecisionOut,
    ModerationSummaryOut,
    PublicationIn,
    PublicationOut,
    PublicationCandidateOut,
    PersonalReportOut,
    PersonalReportEventOut,
    PersonalCommentAppealOut,
    ReportCreateIn,
    ReportEventIn,
    ReportEventOut,
    ReportOut,
    ReviewDecisionIn,
    ReviewOut,
    UserReinstateIn,
    UserSuspendIn,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.identity.models import User

router = APIRouter()


@router.get("/me/reports", response_model=CursorPage[PersonalReportOut])
async def list_personal_reports(
    kind: Literal["resource", "comment"] | None = Query(default=None),
    report_status: list[ReportStatus] | None = Query(default=None, alias="status"),
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: "User" = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[PersonalReportOut]:
    return await personal.list_reports(
        db, user=user, kind=kind, statuses=report_status, limit=limit, cursor=cursor,
    )


@router.get("/me/reports/{report_id}", response_model=PersonalReportOut)
async def get_personal_report(
    report_id: uuid.UUID,
    user: "User" = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PersonalReportOut:
    return await personal.get_report(db, user=user, report_id=report_id)


@router.get("/me/reports/{report_id}/events", response_model=CursorPage[PersonalReportEventOut])
async def list_personal_report_events(
    report_id: uuid.UUID,
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: "User" = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[PersonalReportEventOut]:
    return await personal.list_report_events(
        db, user=user, report_id=report_id, limit=limit, cursor=cursor,
    )


@router.get("/me/comment-appeals", response_model=CursorPage[PersonalCommentAppealOut])
async def list_personal_comment_appeals(
    appeal_status: list[AppealStatus] | None = Query(default=None, alias="status"),
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: "User" = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[PersonalCommentAppealOut]:
    return await personal.list_comment_appeals(
        db, user=user, statuses=appeal_status, limit=limit, cursor=cursor,
    )


# --------------------------------------------------------------------------- #
# 举报（普通用户）
# --------------------------------------------------------------------------- #


@router.post("/reports", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
async def create_report(
    payload: ReportCreateIn,
    user: "User" = Depends(get_current_user),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> ReportOut:
    report = await service.create_report(db, reporter=user, payload=payload, request_id=request_id)
    return ReportOut.model_validate(report)


# --------------------------------------------------------------------------- #
# 审核队列
# --------------------------------------------------------------------------- #


@router.get("/admin/reviews", response_model=CursorPage[ReviewOut])
async def list_reviews(
    review_status: list[ReviewStatus] | None = Query(default=None, alias="status"),
    submitted_from: datetime | None = Query(default=None),
    submitted_to: datetime | None = Query(default=None),
    resource_id: uuid.UUID | None = Query(default=None),
    version_id: uuid.UUID | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: "User" = Depends(require(RESOURCE_REVIEW)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[ReviewOut]:
    return await service.list_reviews(
        db,
        statuses=review_status,
        submitted_from=submitted_from,
        submitted_to=submitted_to,
        resource_id=resource_id,
        version_id=version_id,
        limit=limit,
        cursor=cursor,
        viewer=user,
    )


@router.post(
    "/admin/reviews/{review_id}/decision",
    response_model=ModerationDecisionOut,
    status_code=status.HTTP_201_CREATED,
)
async def decide_review(
    review_id: uuid.UUID,
    payload: ReviewDecisionIn,
    user: "User" = Depends(require(RESOURCE_REVIEW)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> ModerationDecisionOut:
    """给出审核决定：通过（approve）、补证（needs_info）或拒绝（reject），需 content.review。"""
    decision = await service.decide(
        db,
        review_id=review_id,
        reviewer=user,
        decision=payload.decision,
        reason=payload.reason,
        snapshot_revision=payload.snapshot_revision,
        request_id=request_id,
    )
    return ModerationDecisionOut.model_validate(decision)


# --------------------------------------------------------------------------- #
# 发布与时效
# --------------------------------------------------------------------------- #


@router.get("/admin/publication-candidates", response_model=CursorPage[PublicationCandidateOut])
async def publication_candidates(
    limit: int | None = Query(default=None, ge=1, le=100), cursor: str | None = None,
    user: "User" = Depends(require(RESOURCE_PUBLISH)), db: AsyncSession = Depends(get_db),
) -> CursorPage[PublicationCandidateOut]:
    return await service.list_publication_candidates(db, limit=limit, cursor=cursor)


@router.post("/admin/resources/{resource_id}/publication", response_model=PublicationOut)
async def set_publication(
    resource_id: uuid.UUID,
    payload: PublicationIn,
    user: "User" = Depends(require(RESOURCE_PUBLISH)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> PublicationOut:
    version_id = await service.set_publication(
        db,
        resource_id=resource_id,
        action=payload.action,
        version_id=payload.version_id,
        expected_revision=payload.expected_revision,
        actor=user,
        reason=payload.reason,
        request_id=request_id,
    )
    return PublicationOut(resource_id=resource_id, version_id=version_id, action=payload.action)


@router.post(
    "/admin/resources/{resource_id}/freshness", status_code=status.HTTP_204_NO_CONTENT
)
async def set_freshness(
    resource_id: uuid.UUID,
    payload: FreshnessIn,
    user: "User" = Depends(require(RESOURCE_PUBLISH)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await service.set_freshness(
        db,
        resource_id=resource_id,
        status=FreshnessStatus(payload.status),
        actor=user,
        reason=payload.reason,
        request_id=request_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# 投诉工单
# --------------------------------------------------------------------------- #


@router.get("/admin/reports", response_model=CursorPage[ReportOut])
async def list_reports(
    report_status: list[ReportStatus] | None = Query(default=None, alias="status"),
    resource_id: uuid.UUID | None = Query(default=None),
    reporter_id: uuid.UUID | None = Query(default=None),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: "User" = Depends(require(REPORT_HANDLE)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[ReportOut]:
    return await service.list_reports(
        db,
        statuses=report_status,
        resource_id=resource_id,
        reporter_id=reporter_id,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        cursor=cursor,
    )


@router.get("/admin/reports/{report_id}/events", response_model=CursorPage[ReportEventOut])
async def list_report_events(
    report_id: uuid.UUID,
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: "User" = Depends(require(REPORT_HANDLE)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[ReportEventOut]:
    return await service.list_report_events(db, report_id=report_id, limit=limit, cursor=cursor)


@router.post(
    "/admin/reports/{report_id}/events",
    response_model=ReportEventOut,
    status_code=status.HTTP_201_CREATED,
)
async def append_report_event(
    report_id: uuid.UUID,
    payload: ReportEventIn,
    user: "User" = Depends(require(REPORT_HANDLE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> ReportEventOut:
    event = await service.append_report_event(
        db, report_id=report_id, actor=user, payload=payload, request_id=request_id
    )
    return ReportEventOut.model_validate(event)


# --------------------------------------------------------------------------- #
# 评论举报与申诉（§18、§20.2）
# --------------------------------------------------------------------------- #


@router.post(
    "/reports/comments", response_model=CommentReportOut, status_code=status.HTTP_201_CREATED
)
async def create_comment_report(
    payload: CommentReportCreateIn,
    user: "User" = Depends(get_current_user),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> CommentReportOut:
    """资料讨论区举报：普通用户能力（登录 + active 账号即可，不需新权限）。"""
    return await comment_reports.create_comment_report(
        db,
        reporter=user,
        resource_id=payload.resource_id,
        comment_id=payload.comment_id,
        reason=payload.reason,
        request_id=request_id,
    )


@router.get("/admin/comment-reports", response_model=CursorPage[CommentReportOut])
async def list_comment_reports(
    report_status: list[ReportStatus] | None = Query(default=None, alias="status"),
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: "User" = Depends(require(REPORT_HANDLE)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[CommentReportOut]:
    return await comment_reports.list_comment_reports(
        db, statuses=report_status, limit=limit, cursor=cursor
    )


@router.post("/admin/comment-reports/{report_id}/decision", response_model=CommentReportOut)
async def decide_comment_report(
    report_id: uuid.UUID,
    payload: CommentReportDecisionIn,
    user: "User" = Depends(require(REPORT_HANDLE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> CommentReportOut:
    """受理 / 驳回 / 处置（处置复用评论隐藏能力）。"""
    return await comment_reports.decide_comment_report(
        db,
        report_id=report_id,
        actor=user,
        action=payload.action,
        reason=payload.reason,
        expected_revision=payload.expected_revision,
        comment_revision=payload.comment_revision,
        request_id=request_id,
    )


@router.post(
    "/comment-appeals", response_model=CommentAppealOut, status_code=status.HTTP_201_CREATED
)
async def create_comment_appeal(
    payload: CommentAppealCreateIn,
    user: "User" = Depends(get_current_user),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> CommentAppealOut:
    """被隐藏评论的作者提交一次申诉。"""
    return await comment_reports.create_comment_appeal(
        db,
        appellant=user,
        resource_id=payload.resource_id,
        comment_id=payload.comment_id,
        reason=payload.reason,
        request_id=request_id,
    )


@router.get("/admin/comment-appeals", response_model=CursorPage[CommentAppealOut])
async def list_comment_appeals(
    appeal_status: list[AppealStatus] | None = Query(default=None, alias="status"),
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: "User" = Depends(require(REPORT_HANDLE)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[CommentAppealOut]:
    return await comment_reports.list_comment_appeals(
        db, statuses=appeal_status, limit=limit, cursor=cursor
    )


@router.post("/admin/comment-appeals/{appeal_id}/decision", response_model=CommentAppealOut)
async def decide_comment_appeal(
    appeal_id: uuid.UUID,
    payload: CommentAppealDecisionIn,
    user: "User" = Depends(require(REPORT_HANDLE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> CommentAppealOut:
    """采纳（恢复评论）或驳回申诉。"""
    return await comment_reports.decide_comment_appeal(
        db,
        appeal_id=appeal_id,
        actor=user,
        decision=payload.decision,
        reason=payload.reason,
        expected_revision=payload.expected_revision,
        comment_revision=payload.comment_revision,
        request_id=request_id,
    )


# --------------------------------------------------------------------------- #
# 审计与概览
# --------------------------------------------------------------------------- #


@router.get("/admin/audit", response_model=CursorPage[AuditLogOut])
async def list_audit_logs(
    object_type: str | None = Query(default=None),
    object_id: str | None = Query(default=None),
    actor_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: "User" = Depends(require(AUDIT_VIEW)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[AuditLogOut]:
    return await service.list_audit_logs(
        db,
        object_type=object_type,
        object_id=object_id,
        actor_id=actor_id,
        action=action,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        cursor=cursor,
    )


@router.get("/admin/moderation/summary", response_model=ModerationSummaryOut)
async def moderation_summary(
    user: "User" = Depends(require(RESOURCE_REVIEW)),
    db: AsyncSession = Depends(get_db),
) -> ModerationSummaryOut:
    return await service.moderation_summary(db)


# --------------------------------------------------------------------------- #
# 目录维护与账号限制
# --------------------------------------------------------------------------- #


@router.post(
    "/admin/catalog/resources/{resource_id}/course", status_code=status.HTTP_204_NO_CONTENT
)
async def correct_resource_course(
    resource_id: uuid.UUID,
    payload: CatalogCorrectionIn,
    user: "User" = Depends(require(CATALOG_MANAGE)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await service.correct_resource_course(
        db,
        resource_id=resource_id,
        course_id=payload.course_id,
        actor=user,
        reason=payload.reason,
        request_id=request_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/admin/users/{user_id}/suspend", status_code=status.HTTP_204_NO_CONTENT)
async def suspend_user(
    user_id: uuid.UUID,
    payload: UserSuspendIn,
    user: "User" = Depends(require(USER_SUSPEND)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await service.set_account_restriction(
        db,
        user_id=user_id,
        suspend=True,
        reason=payload.reason,
        until=payload.until,
        actor=user,
        request_id=request_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/admin/users/{user_id}/reinstate", status_code=status.HTTP_204_NO_CONTENT)
async def reinstate_user(
    user_id: uuid.UUID,
    payload: UserReinstateIn,
    user: "User" = Depends(require(USER_SUSPEND)),
    request_id: str = Depends(get_request_id),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await service.set_account_restriction(
        db,
        user_id=user_id,
        suspend=False,
        reason=payload.reason,
        until=None,
        actor=user,
        request_id=request_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
