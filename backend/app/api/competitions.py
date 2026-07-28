import uuid
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.competition import Competition, CompetitionWinner
from app.schemas.competition import CompetitionOut, CompetitionListOut, CompetitionWinnerOut

router = APIRouter(prefix="/api/competitions", tags=["competitions"])


@router.get("")
async def list_competitions(
    category: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(Competition)
    if category:
        query = query.where(Competition.category == category)
    query = query.order_by(Competition.name)
    result = await db.execute(query)
    competitions = result.scalars().all()
    return [CompetitionListOut.model_validate(c) for c in competitions]


@router.get("/{competition_id}")
async def get_competition(competition_id: str, db: AsyncSession = Depends(get_db)):
    cid = uuid.UUID(competition_id)
    result = await db.execute(
        select(Competition)
        .options(selectinload(Competition.winners))
        .where(Competition.id == cid)
    )
    comp = result.scalar_one_or_none()
    if not comp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Competition not found")

    return CompetitionOut.model_validate(comp)
