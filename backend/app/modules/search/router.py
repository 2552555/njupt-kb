"""统一搜索路由（设计文档 §22）。

只解析参数并调用 service；业务规则、可见性与日志全部在 service.py。
前缀由 `app/main.py` 统一加 `/api/v1`，本模块不自行决定前缀。
三个端点均可匿名访问；`/search` 复用搜索分级限流，建议与热词不额外限流。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.deps import get_db, get_optional_user
from app.modules.ratelimit.deps import rate_limit
from app.modules.search import service
from app.modules.search.schemas import HotOut, SearchOut, SearchScope, SuggestOut

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchOut)
async def unified_search(
    q: str = Query(
        ...,
        min_length=1,
        max_length=settings.SEARCH_QUERY_MAX_CHARS,
        description="课程名、课程代码、资料标题或创作者显示名",
    ),
    scope: SearchScope = Query(
        default=SearchScope.ALL,
        description="搜索范围：all / course / resource / creator",
    ),
    limit: int = Query(default=10, ge=1, le=50, description="每个分组的最大条数"),
    user: Any = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(rate_limit("search")),
) -> SearchOut:
    """统一搜索：一次请求返回课程、资源与创作者三个分组。"""
    return await service.search(db, user=user, q=q, scope=scope, limit=limit)


@router.get("/search/suggest", response_model=SuggestOut)
async def search_suggest(
    q: str = Query(default="", description="输入片段；不足最小长度时返回空列表"),
    user: Any = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> SuggestOut:
    """输入联想：前缀优先；不写查询日志，避免按键级写入放大。"""
    return await service.suggest(db, user=user, q=q)


@router.get("/search/hot", response_model=HotOut)
async def search_hot(
    db: AsyncSession = Depends(get_db),
) -> HotOut:
    """窗口内热词：只含非零结果且长度不少于 2 的归一化词。"""
    return await service.hot_queries(db)
