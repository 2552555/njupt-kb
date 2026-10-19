"""推荐与实验路由（ALLIN §21、§34；yonghu.md 推荐与学习行为）。

用户侧：行为上报、采集开关、阅读进度、推荐流。
管理侧：模型训练/激活、实验创建/启停/结果（要求 `reco.manage`）。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import get_current_user, get_db, get_optional_user
from app.core.enums import Role
from app.core.errors import NotEnabled
from app.core.permissions import RECO_MANAGE, require
from app.modules.reco import service
from app.modules.reco.models import BehaviorConsent, Experiment, ReadingProgress, RecommendationModel
from app.modules.reco.schemas import (
    BehaviorReportIn,
    ConsentIn,
    ConsentOut,
    ExperimentCreateIn,
    ExperimentOut,
    ModelOut,
    ProgressIn,
    ProgressOut,
    RecoFeedOut,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.identity.models import User

router = APIRouter(tags=["reco"])


@router.post("/behaviors", status_code=status.HTTP_202_ACCEPTED)
async def report_behaviors(
    payload: BehaviorReportIn,
    db: AsyncSession = Depends(get_db),
    user: "User | None" = Depends(get_optional_user),
) -> dict[str, object]:
    """批量上报行为事件；关闭采集时返回 0 且不报错（客户端无需分支）。"""
    if not settings.BEHAVIOR_COLLECT_ENABLED:
        return {"accepted": 0, "collect_enabled": False}
    accepted = await service.record_events(
        db,
        user=user,
        session_key=payload.session_key,
        events=[item.model_dump() for item in payload.events],
    )
    return {"accepted": accepted, "collect_enabled": True}


@router.get("/me/behaviors/consent", response_model=ConsentOut)
async def get_consent(
    db: AsyncSession = Depends(get_db),
    user: "User" = Depends(get_current_user),
) -> ConsentOut:
    row = (
        await db.execute(select(BehaviorConsent).where(BehaviorConsent.user_id == user.id))
    ).scalar_one_or_none()
    if row is None:
        # 没有记录时按默认启用；返回一个虚拟对象让前端能显示开关状态。
        return ConsentOut(enabled=True, version="v1", updated_at=user.created_at)
    return ConsentOut.model_validate(row)


@router.put("/me/behaviors/consent", response_model=ConsentOut)
async def update_consent(
    payload: ConsentIn,
    db: AsyncSession = Depends(get_db),
    user: "User" = Depends(get_current_user),
) -> ConsentOut:
    row = await service.set_consent(
        db, user=user, enabled=payload.enabled, reason=payload.reason
    )
    return ConsentOut.model_validate(row)


@router.get("/me/reading-progress")
async def list_progress(
    db: AsyncSession = Depends(get_db),
    user: "User" = Depends(get_current_user),
) -> dict[str, object]:
    rows = (
        (
            await db.execute(
                select(ReadingProgress)
                .where(ReadingProgress.user_id == user.id)
                .order_by(ReadingProgress.updated_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [ProgressOut.model_validate(row) for row in rows]}


@router.put("/me/reading-progress/{resource_id}", response_model=ProgressOut)
async def update_progress(
    resource_id: uuid.UUID,
    payload: ProgressIn,
    db: AsyncSession = Depends(get_db),
    user: "User" = Depends(get_current_user),
) -> ProgressOut:
    row = await service.upsert_progress(
        db,
        user=user,
        resource_id=resource_id,
        progress_pct=payload.progress_pct,
        last_position=payload.last_position,
        dwell_ms=payload.dwell_ms,
    )
    return ProgressOut.model_validate(row)


@router.get("/reco/feed", response_model=RecoFeedOut)
async def reco_feed(
    surface: str = Query(default="home", max_length=20),
    limit: int | None = Query(default=None, ge=1, le=30),
    course_id: uuid.UUID | None = Query(default=None),
    session_key: str | None = Query(default=None, max_length=64),
    db: AsyncSession = Depends(get_db),
    user: "User | None" = Depends(get_optional_user),
) -> RecoFeedOut:
    if not settings.RECO_ENABLED:
        raise NotEnabled("推荐功能未启用")
    result = await service.recommend(
        db,
        user=user,
        session_key=session_key,
        surface=surface,
        limit=limit or settings.RECO_DEFAULT_LIMIT,
        course_id=course_id,
    )
    return RecoFeedOut(**result)


@router.get("/admin/reco/models")
async def list_models(
    db: AsyncSession = Depends(get_db),
    _: "User" = Depends(require(RECO_MANAGE)),
) -> dict[str, object]:
    rows = (
        (
            await db.execute(
                select(RecommendationModel)
                .order_by(RecommendationModel.version.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [ModelOut.model_validate(row) for row in rows]}


@router.post("/admin/reco/train", response_model=ModelOut, status_code=201)
async def train(
    db: AsyncSession = Depends(get_db),
    actor: "User" = Depends(require(RECO_MANAGE)),
) -> ModelOut:
    model = await service.train_model(db, actor=actor)
    return ModelOut.model_validate(model)


@router.post("/admin/reco/models/{version}/activate", response_model=ModelOut)
async def activate(
    version: int,
    db: AsyncSession = Depends(get_db),
    actor: "User" = Depends(require(RECO_MANAGE)),
) -> ModelOut:
    model = await service.activate_model(db, actor=actor, version=version)
    return ModelOut.model_validate(model)


@router.get("/admin/experiments")
async def list_experiments(
    db: AsyncSession = Depends(get_db),
    _: "User" = Depends(require(RECO_MANAGE)),
) -> dict[str, object]:
    rows = (
        (
            await db.execute(
                select(Experiment).order_by(Experiment.created_at.desc()).limit(50)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [ExperimentOut.model_validate(row) for row in rows]}


@router.post("/admin/experiments", response_model=ExperimentOut, status_code=201)
async def create_experiment(
    payload: ExperimentCreateIn,
    db: AsyncSession = Depends(get_db),
    actor: "User" = Depends(require(RECO_MANAGE)),
) -> ExperimentOut:
    row = await service.create_experiment(
        db,
        actor=actor,
        key=payload.key,
        name=payload.name,
        variants=payload.variants,
        traffic_bps=payload.traffic_bps,
        metric=payload.metric,
        note=payload.note,
    )
    return ExperimentOut.model_validate(row)


@router.post("/admin/experiments/{key}/start", response_model=ExperimentOut)
async def start_experiment(
    key: str,
    db: AsyncSession = Depends(get_db),
    actor: "User" = Depends(require(RECO_MANAGE)),
) -> ExperimentOut:
    row = await service.start_experiment(db, actor=actor, key=key)
    return ExperimentOut.model_validate(row)


@router.post("/admin/experiments/{key}/stop", response_model=ExperimentOut)
async def stop_experiment(
    key: str,
    db: AsyncSession = Depends(get_db),
    actor: "User" = Depends(require(RECO_MANAGE)),
) -> ExperimentOut:
    row = await service.stop_experiment(db, actor=actor, key=key)
    return ExperimentOut.model_validate(row)


@router.get("/admin/experiments/{key}/results")
async def experiment_results(
    key: str,
    db: AsyncSession = Depends(get_db),
    _: "User" = Depends(require(RECO_MANAGE)),
) -> dict[str, object]:
    return await service.experiment_results(db, key=key)
