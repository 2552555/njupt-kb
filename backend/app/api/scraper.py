from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.scraper import ScrapedPage

router = APIRouter(prefix="/api/scraper", tags=["scraper"])


@router.get("/latest")
async def latest_pages(
    limit: int = Query(20, ge=1, le=100),
    category: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(ScrapedPage)
    if category:
        query = query.where(ScrapedPage.category == category)
    query = query.order_by(ScrapedPage.scraped_at.desc()).limit(limit)
    result = await db.execute(query)
    pages = result.scalars().all()

    return [
        {
            "id": p.id,
            "source_id": p.source_id,
            "url": p.url,
            "title": p.title,
            "content_text": p.content_text[:500] if p.content_text else None,
            "published_at": p.published_at,
            "scraped_at": p.scraped_at,
            "category": p.category,
        }
        for p in pages
    ]
