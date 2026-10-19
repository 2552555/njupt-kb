"""统一搜索 DTO（Pydantic v2，设计文档 §22）。

对外只返回展示所需字段与前端可跳转路径，不返回 ORM 对象、可见性策略或内部计数。
课程/资源/创作者三个分组保持独立，`total` 是本次实际返回条数之和。
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import BaseModel

__all__ = [
    "CourseHit",
    "CreatorHit",
    "HotItem",
    "HotOut",
    "ResourceHit",
    "SearchOut",
    "SearchScope",
    "SuggestItem",
    "SuggestOut",
    "SuggestType",
]


class SearchScope(StrEnum):
    """搜索范围；`all` 表示三组都查。"""

    ALL = "all"
    COURSE = "course"
    RESOURCE = "resource"
    CREATOR = "creator"


class SuggestType(StrEnum):
    """建议项类型，决定前端图标与跳转语义。"""

    COURSE = "course"
    RESOURCE = "resource"
    CREATOR = "creator"


class CourseHit(BaseModel):
    """课程命中项：只暴露公开目录字段。"""

    id: uuid.UUID
    name: str
    code: str | None = None
    college_name: str | None = None


class ResourceHit(BaseModel):
    """资源命中项：标题取当前已发布版本，不暴露草稿版本标题。"""

    id: uuid.UUID
    title: str
    resource_type: str
    course_id: uuid.UUID
    course_name: str


class CreatorHit(BaseModel):
    """创作者命中项：`id` 是用户 ID，对应 `/creators/{id}`。"""

    id: uuid.UUID
    display_name: str
    resource_count: int


class SearchOut(BaseModel):
    """统一搜索响应；分组为空时返回空列表而不是省略字段。"""

    query: str
    scope: SearchScope
    courses: list[CourseHit]
    resources: list[ResourceHit]
    creators: list[CreatorHit]
    total: int


class SuggestItem(BaseModel):
    """搜索建议项；`href` 为前端路径，后端不拼站点域名。"""

    type: SuggestType
    id: uuid.UUID
    label: str
    sublabel: str | None = None
    href: str


class SuggestOut(BaseModel):
    """建议列表；不足最小长度时返回空列表，不返回错误。"""

    items: list[SuggestItem]


class HotItem(BaseModel):
    """热词项：窗口内同一归一化词的搜索次数。"""

    query: str
    count: int


class HotOut(BaseModel):
    """热词响应；`window_days` 让前端能解释统计口径。"""

    items: list[HotItem]
    window_days: int
