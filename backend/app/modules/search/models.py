"""统一搜索的查询日志表（设计文档 §22）。

`search_queries` 只用于热词与零结果观测的聚合日志：只记录归一化后的查询词、
搜索范围、结果数与是否零结果。**绝不保存 user_id、IP、UA 或原始未归一化输入**，
因此它不能用于还原某个用户的搜索历史，也不承担审计职责。

跨模块不建外键：日志独立于课程、资源与账号，账号注销或内容下架都不应影响历史
聚合；保留期由 `app.modules.search.retention` 清理。
"""

from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, Timestamped, UUIDPrimaryKey

__all__ = ["SEARCH_SCOPES", "SearchQuery"]

# 搜索范围：与 `schemas.SearchScope` 保持一致；放在模型侧供 CHECK 约束复用。
SEARCH_SCOPES = ("all", "course", "resource", "creator")

_SCOPE_CHECK = "scope IN (" + ", ".join(f"'{value}'" for value in SEARCH_SCOPES) + ")"

_TABLE_COMMENT = "统一搜索聚合日志：仅用于热词与零结果观测，不存用户标识与原始输入"


class SearchQuery(Base, UUIDPrimaryKey, Timestamped):
    """一次搜索的聚合观测记录（§22）。

    `query_norm` 已去首尾空白、压缩连续空白、转小写并按
    `settings.SEARCH_QUERY_MAX_CHARS` 截断；`created_at` 既用于热词时间窗口，
    也用于保留期清理。复合索引服务于「按词取最近记录」与热词分组。
    """

    __tablename__ = "search_queries"
    __table_args__ = (
        CheckConstraint(_SCOPE_CHECK, name="scope"),
        # 热词按 query_norm 分组并取窗口内最近时间；复合索引避免全表扫描。
        Index("ix_search_queries_query_norm_created_at", "query_norm", "created_at"),
        {"comment": _TABLE_COMMENT},
    )

    query_norm: Mapped[str] = mapped_column(String(120), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    result_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    zero_result: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
