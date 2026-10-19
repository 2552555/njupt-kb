"""个人工作台、历史与关注动态；从现有业务事实聚合，不新增跟踪数据。"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import CursorPage, encode_cursor, normalize_page_size, seek_descending


class PersonalOverview(BaseModel):
    resources: int
    published: int
    drafts: int
    pending_reviews: int
    favorites: int
    follows: int
    unread_notifications: int
    open_requests: int
    open_corrections: int
    downloads: int
    received_download_grants: int
    received_ratings: int
    average_rating: float | None


class DownloadHistoryItem(BaseModel):
    id: uuid.UUID
    created_at: datetime
    status: str
    available: bool
    resource_id: uuid.UUID | None
    title: str | None
    file_name: str | None
    version_no: int | None


class FeedItem(BaseModel):
    id: uuid.UUID
    title: str
    resource_type: str
    course_id: uuid.UUID
    course_name: str
    owner_id: uuid.UUID
    version_no: int
    updated_at: datetime


def result_page(items, rows, size, *, timestamp=lambda row: row.created_at):
    more = len(rows) > size
    last = rows[size - 1] if more else None
    cursor = encode_cursor({"ts": timestamp(last).isoformat(), "id": str(last.id)}) if last else None
    return CursorPage(items=items, has_more=more, next_cursor=cursor)


async def overview(db: AsyncSession, user) -> PersonalOverview:
    from app.modules.access.models import DownloadGrant
    from app.modules.content.models import Resource, ResourceVersion
    from app.modules.interaction.models import Correction, Favorite, Follow, Rating, ResourceRequest
    from app.modules.notification.models import Notification

    async def count(model, *conditions):
        return int(await db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0)

    owned = select(Resource.id).where(Resource.owner_id == user.id)
    ratings = (await db.execute(select(func.count(Rating.id), func.avg(Rating.score)).where(
        Rating.resource_id.in_(owned)
    ))).one()
    return PersonalOverview(
        resources=await count(Resource, Resource.owner_id == user.id),
        published=await count(Resource, Resource.owner_id == user.id, Resource.publication_status == "published"),
        drafts=await count(ResourceVersion, ResourceVersion.resource_id.in_(owned), ResourceVersion.review_status == "draft"),
        pending_reviews=await count(ResourceVersion, ResourceVersion.resource_id.in_(owned), ResourceVersion.review_status == "pending"),
        favorites=await count(Favorite, Favorite.user_id == user.id),
        follows=await count(Follow, Follow.user_id == user.id),
        unread_notifications=await count(Notification, Notification.user_id == user.id, Notification.is_read.is_(False)),
        open_requests=await count(ResourceRequest, ResourceRequest.requester_id == user.id, ResourceRequest.status == "open"),
        open_corrections=await count(Correction, Correction.submitter_id == user.id, Correction.status == "open"),
        downloads=await count(DownloadGrant, DownloadGrant.user_id == user.id),
        received_download_grants=await count(DownloadGrant, DownloadGrant.resource_id.in_(owned), DownloadGrant.status.in_(["issued", "consumed", "expired"])),
        received_ratings=int(ratings[0]),
        average_rating=float(ratings[1]) if ratings[1] is not None else None,
    )


async def downloads(db: AsyncSession, user, *, cursor=None, limit=None):
    from app.modules.access.models import DownloadGrant
    from app.modules.access.visibility import visible_resource_ids
    from app.modules.content.models import Resource, ResourceFile, ResourceVersion

    size = normalize_page_size(limit)
    stmt = seek_descending(db, select(DownloadGrant).where(DownloadGrant.user_id == user.id),
                DownloadGrant.created_at, DownloadGrant.id, cursor)
    rows = list((await db.scalars(stmt.limit(size + 1))).all())
    current = rows[:size]
    # 只对当前仍可见且发布的资源返回标识/标题；历史授权不等于当前读取权。
    visible = select(Resource.published_version_id).where(Resource.id.in_(visible_resource_ids(user)), Resource.publication_status == "published")
    details = (await db.execute(select(ResourceVersion.id, ResourceVersion.title, ResourceVersion.version_no)
        .where(ResourceVersion.id.in_([row.version_id for row in current]), ResourceVersion.id.in_(visible), ResourceVersion.review_status == "approved"))).all()
    versions = {row.id: row for row in details}
    file_rows = (await db.execute(select(ResourceFile.id, ResourceFile.original_name).where(
        ResourceFile.id.in_([row.file_id for row in current if row.version_id in versions])
    ))).all()
    files = {row.id: row.original_name for row in file_rows}
    items = [DownloadHistoryItem(
        id=row.id, created_at=row.created_at, status=row.status,
        available=row.version_id in versions,
        resource_id=row.resource_id if row.version_id in versions else None,
        title=versions[row.version_id].title if row.version_id in versions else None,
        file_name=files.get(row.file_id) if row.version_id in versions else None,
        version_no=versions[row.version_id].version_no if row.version_id in versions else None,
    ) for row in current]
    return result_page(items, rows, size)


async def feed(db: AsyncSession, user, *, cursor=None, limit=None):
    from app.modules.access.visibility import visible_resource_ids
    from app.modules.catalog.models import Course
    from app.modules.content.models import Resource, ResourceVersion
    from app.modules.interaction.models import Follow

    size = normalize_page_size(limit)
    following = exists(select(Follow.id).where(Follow.user_id == user.id, or_(
        and_(Follow.target_type == "course", Follow.target_id == Resource.course_id),
        and_(Follow.target_type == "creator", Follow.target_id == Resource.owner_id),
    )))
    # 按已发布版本的提交时间排序，不把作者后续编辑草稿当成新发布。
    stamp = func.coalesce(ResourceVersion.submitted_at, ResourceVersion.created_at)
    stmt = select(Resource.id, Resource.resource_type, Resource.course_id, Resource.owner_id,
        ResourceVersion.title, ResourceVersion.version_no, stamp.label("updated_at"), Course.name.label("course_name"))
    stmt = stmt.select_from(Resource).join(ResourceVersion, Resource.published_version_id == ResourceVersion.id).join(Course, Course.id == Resource.course_id).where(
        following, Resource.id.in_(visible_resource_ids(user)), Resource.publication_status == "published",
        ResourceVersion.resource_id == Resource.id, ResourceVersion.review_status == "approved", Course.status != "inactive",
    )
    stmt = seek_descending(db, stmt, stamp, Resource.id, cursor)
    rows = (await db.execute(stmt.limit(size + 1))).all()
    items = [FeedItem(**row._mapping) for row in rows[:size]]
    return result_page(items, rows, size, timestamp=lambda row: row.updated_at)
