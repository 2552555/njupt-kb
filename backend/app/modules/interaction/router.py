"""interaction 路由：收藏、评价、关注、评论、纠错、求资料与搜索。

router 只做解析与调用，业务规则全部在 service.py（docs/ARCHITECTURE.md §1）。
前缀由 main.py 统一添加，模块不得自行决定前缀。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db, get_optional_user, get_request_id
from app.core.enums import CorrectionStatus, ResourceType
from app.core.pagination import CursorPage
from app.core.permissions import CATALOG_MANAGE, COMMENT_MODERATE, CORRECTION_HANDLE, require
from app.modules.interaction import service
from app.modules.interaction.schemas import (
    CommentCreate,
    CommentItem,
    CommentWithdraw,
    CommentModeration,
    CorrectionCreate,
    CorrectionItem,
    CorrectionStatusUpdate,
    ClassificationFix,
    FavoriteItem,
    FollowItem,
    FollowResult,
    RatingSubmit,
    RatingSummary,
    RequestCreate,
    RequestCreateResult,
    RequestItem,
    RequestReopen,
    RequestStatusUpdate,
    RequestResponseCreate,
    RequestResponseItem,
    SearchItem,
    RecommendationsOut,
)
from app.modules.ratelimit.deps import rate_limit

router = APIRouter(tags=["interaction"])


@router.get("/recommendations", response_model=RecommendationsOut)
async def recommend_resources(
    course_id: uuid.UUID | None = None,
    limit: int = Query(default=6, ge=1, le=20),
    user=Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> RecommendationsOut:
    from app.modules.interaction.recommendations import recommend_resources as recommend

    return await recommend(db, user=user, course_id=course_id, limit=limit)


# --- 搜索（§19）---
# beta16：`/search` 让位给 search 模块的统一搜索入口（课程/资源/创作者三组）。
# 本端点保留原有筛选能力（学院、培养方案、教师、学年等），迁至 `/search/resources`，
# 语义不变、参数不变，仅路径变化；前端搜索页在同一批次内改调新入口。


@router.get("/search/resources", response_model=CursorPage[SearchItem])
async def search_resources(
    q: str | None = Query(default=None, description="课程名、代码、简称、资料标题或教师姓名"),
    university_id: uuid.UUID | None = None,
    curriculum_id: uuid.UUID | None = None,
    course_id: uuid.UUID | None = None,
    semester_id: uuid.UUID | None = None,
    teacher_id: uuid.UUID | None = None,
    resource_type: ResourceType | None = None,
    applicable_year: int | None = None,
    cursor: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_optional_user),
    _rl: None = Depends(rate_limit("search")),
) -> CursorPage[SearchItem]:
    """只返回已发布资源；查询失败返回 5xx，不吞成空列表（§39 A21）。"""
    return await service.search(
        db,
        user=user,
        q=q,
        university_id=university_id,
        curriculum_id=curriculum_id,
        course_id=course_id,
        semester_id=semester_id,
        teacher_id=teacher_id,
        resource_type=str(resource_type) if resource_type is not None else None,
        applicable_year=applicable_year,
        cursor=cursor,
        limit=limit,
    )


# --- 收藏（§20.1）---


@router.put("/me/favorites/{resource_id}", status_code=status.HTTP_204_NO_CONTENT)
async def set_favorite(
    resource_id: uuid.UUID,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await service.set_favorite(db, user_id=user.id, resource_id=resource_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/me/favorites/{resource_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(
    resource_id: uuid.UUID,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await service.remove_favorite(db, user_id=user.id, resource_id=resource_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me/favorites", response_model=CursorPage[FavoriteItem])
async def list_favorites(
    cursor: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=100),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[FavoriteItem]:
    return await service.list_favorites(db, user_id=user.id, cursor=cursor, limit=limit)


# --- 评价（§20.1）---


@router.put(
    "/resources/{resource_id}/versions/{version_id}/rating",
    response_model=RatingSummary,
)
async def submit_rating(
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: RatingSubmit,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> RatingSummary:
    return await service.submit_rating(
        db,
        user_id=user.id,
        resource_id=resource_id,
        version_id=version_id,
        score=payload.score,
        request_id=request_id,
    )


@router.get("/resources/{resource_id}/ratings/summary", response_model=RatingSummary)
async def rating_summary(
    resource_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> RatingSummary:
    return await service.resource_rating_summary(db, resource_id)


# --- 评论（§20.2）---


@router.get("/resources/{resource_id}/comments", response_model=CursorPage[CommentItem])
async def list_comments(
    resource_id: uuid.UUID,
    cursor: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=100),
    user=Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[CommentItem]:
    return await service.list_comments(db, resource_id=resource_id, cursor=cursor, limit=limit, user=user)


@router.post(
    "/resources/{resource_id}/comments",
    response_model=CommentItem,
    status_code=status.HTTP_201_CREATED,
)
async def create_comment(
    resource_id: uuid.UUID,
    payload: CommentCreate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CommentItem:
    return await service.create_comment(
        db,
        user_id=user.id,
        resource_id=resource_id,
        body=payload.body,
        parent_id=payload.parent_id,
    )


@router.post("/resources/{resource_id}/comments/{comment_id}/withdraw", response_model=CommentItem)
async def withdraw_comment(
    resource_id: uuid.UUID, comment_id: uuid.UUID, payload: CommentWithdraw,
    user=Depends(get_current_user), db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> CommentItem:
    from app.modules.interaction.comment_management import manage_comment

    return await manage_comment(db, user=user, resource_id=resource_id, comment_id=comment_id,
                                expected_revision=payload.expected_revision, request_id=request_id)


@router.get("/admin/comments", response_model=CursorPage[CommentItem])
async def managed_comments(hidden_only: bool = False, cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100), user=Depends(require(COMMENT_MODERATE)),
    db: AsyncSession = Depends(get_db)):
    from app.modules.interaction.comment_management import list_managed_comments
    return await list_managed_comments(db, hidden_only=hidden_only, cursor=cursor, limit=limit)


@router.post("/resources/{resource_id}/comments/{comment_id}/moderation", response_model=CommentItem)
async def moderate_comment(
    resource_id: uuid.UUID, comment_id: uuid.UUID, payload: CommentModeration,
    user=Depends(require(COMMENT_MODERATE)), db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> CommentItem:
    from app.modules.interaction.comment_management import manage_comment

    return await manage_comment(db, user=user, resource_id=resource_id, comment_id=comment_id,
                                expected_revision=payload.expected_revision, hidden=payload.hidden,
                                reason=payload.reason, request_id=request_id)


# --- 关注（§20.2）---


@router.put("/me/follows/{target_type}/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def set_follow(
    target_type: str,
    target_id: uuid.UUID,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await service.set_follow(
        db, user_id=user.id, target_type=target_type, target_id=target_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/me/follows/{target_type}/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_follow(
    target_type: str,
    target_id: uuid.UUID,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await service.remove_follow(
        db, user_id=user.id, target_type=target_type, target_id=target_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me/follows", response_model=CursorPage[FollowItem])
async def list_follows(
    target_type: str | None = Query(default=None, description="course 或 creator"),
    cursor: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=100),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[FollowItem]:
    return await service.list_follows(
        db, user_id=user.id, target_type=target_type, cursor=cursor, limit=limit
    )


# --- 纠错（§20.2）---


@router.post("/corrections", response_model=CorrectionItem, status_code=status.HTTP_201_CREATED)
async def create_correction(
    payload: CorrectionCreate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CorrectionItem:
    return await service.create_correction(
        db,
        user_id=user.id,
        resource_id=payload.resource_id,
        version_id=payload.version_id,
        description=payload.description,
        suggested_course_id=payload.suggested_course_id,
        suggested_resource_type=payload.suggested_resource_type,
    )


@router.get("/me/corrections", response_model=CursorPage[CorrectionItem])
async def list_my_corrections(
    cursor: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=100),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[CorrectionItem]:
    return await service.list_my_corrections(db, user_id=user.id, cursor=cursor, limit=limit)


@router.get("/admin/corrections", response_model=CursorPage[CorrectionItem])
async def list_corrections(
    status: CorrectionStatus | None = Query(default=None),
    cursor: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=100),
    user=Depends(require(CORRECTION_HANDLE)),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[CorrectionItem]:
    return await service.list_corrections(
        db, status=status, cursor=cursor, limit=limit
    )


@router.post("/admin/corrections/{correction_id}/status", response_model=CorrectionItem)
async def update_correction_status(
    correction_id: uuid.UUID,
    payload: CorrectionStatusUpdate,
    user=Depends(require(CORRECTION_HANDLE)),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> CorrectionItem:
    return await service.update_correction_status(
        db,
        actor_id=user.id,
        correction_id=correction_id,
        status=payload.status,
        resolution_note=payload.resolution_note,
        fixed_version_id=payload.fixed_version_id,
        request_id=request_id,
    )


# --- 求资料（§20.2）---


@router.post("/admin/corrections/{correction_id}/classification", response_model=CorrectionItem)
async def apply_classification_fix(
    correction_id: uuid.UUID, payload: ClassificationFix,
    user=Depends(require(CATALOG_MANAGE, CORRECTION_HANDLE)),
    db: AsyncSession = Depends(get_db), request_id: str = Depends(get_request_id),
):
    return await service.apply_classification_fix(
        db, correction_id=correction_id, actor_id=user.id,
        expected_revision=payload.expected_revision, resolution_note=payload.resolution_note,
        request_id=request_id,
    )


@router.post("/requests", response_model=RequestCreateResult)
async def create_request(
    payload: RequestCreate,
    response: Response,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RequestCreateResult:
    result = await service.create_request(
        db,
        user_id=user.id,
        course_id=payload.course_id,
        scope=payload.scope,
        description=payload.description,
    )
    # 命中同义未解决需求时返回 200 并合并关注；新建返回 201。
    response.status_code = (
        status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    )
    return result


@router.get("/requests", response_model=CursorPage[RequestItem])
async def list_requests(
    course_id: uuid.UUID | None = None,
    request_status: str | None = Query(default=None, alias="status"),
    sort: str = Query(default="latest", description="latest=最新（默认）；hot=近 7 天开放需求榜（按关注数）"),
    cursor: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_optional_user),
) -> CursorPage[RequestItem]:
    return await service.list_requests(
        db,
        course_id=course_id,
        status=request_status,
        cursor=cursor,
        limit=limit,
        viewer=user,
        sort=sort,
    )


@router.post("/requests/{request_id}/follow", response_model=FollowResult)
async def follow_request(
    request_id: uuid.UUID,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FollowResult:
    return await service.follow_request(db, user_id=user.id, request_id=request_id)


@router.get("/me/requests", response_model=CursorPage[RequestItem])
async def list_my_requests(
    cursor: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=100),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[RequestItem]:
    return await service.list_my_requests(
        db, user_id=user.id, cursor=cursor, limit=limit, viewer=user
    )


# 贡献者公开主页（§24.1）由 creator 模块的 GET /creators/{user_id} 提供，
# 本模块不再注册同路径路由，避免同名路由互相遮蔽。


@router.get("/requests/{request_id}", response_model=RequestItem)
async def request_detail(request_id: uuid.UUID, user=Depends(get_optional_user),
    db: AsyncSession = Depends(get_db)):
    from app.modules.interaction.request_management import request_detail
    return await request_detail(db, request_id, viewer=user)


@router.post("/requests/{request_id}/status", response_model=RequestItem)
async def change_request_status(request_id: uuid.UUID, payload: RequestStatusUpdate,
    user=Depends(get_current_user), db: AsyncSession = Depends(get_db), trace=Depends(get_request_id)):
    from app.modules.interaction.request_management import change_status
    return await change_status(db, user=user, request_id=request_id, payload=payload, request_trace_id=trace)


@router.post("/requests/{request_id}/reopen", response_model=RequestItem)
async def reopen_request(request_id: uuid.UUID, payload: RequestReopen,
    user=Depends(get_current_user), db: AsyncSession = Depends(get_db), trace=Depends(get_request_id)):
    """复核重开：解决资源失效后由发起者或管理员带理由重开，回到 open 并清空解决资源。"""
    from app.modules.interaction.request_management import reopen
    return await reopen(db, user=user, request_id=request_id, payload=payload, request_trace_id=trace)


@router.get("/requests/{request_id}/responses", response_model=CursorPage[RequestResponseItem])
async def request_responses(request_id: uuid.UUID, cursor: str | None = None,
    limit: int | None = Query(None, ge=1, le=100), db: AsyncSession = Depends(get_db)):
    from app.modules.interaction.request_management import list_responses
    return await list_responses(db, request_id, cursor=cursor, limit=limit)


@router.get("/requests/{request_id}/resources")
async def request_resource_choices(request_id: uuid.UUID, keyword: str | None = None,
    cursor: str | None = None, db: AsyncSession = Depends(get_db)):
    from app.modules.interaction.request_management import get_request
    from app.modules.content.service import list_resources
    row = await get_request(db, request_id)
    return await list_resources(db, user=None, course_id=row.course_id, keyword=keyword, cursor=cursor, limit=20)


@router.post("/requests/{request_id}/responses", response_model=RequestResponseItem)
async def respond_to_request(request_id: uuid.UUID, payload: RequestResponseCreate,
    user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from app.modules.interaction.request_management import respond
    return await respond(db, user=user, request_id=request_id, resource_id=payload.resource_id)
