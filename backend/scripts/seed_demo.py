"""开发演示数据（**非真实资料**）。

仅用于本地开发与界面演示，全部条目都是显式演示数据：
- 演示账号使用示例邮箱与固定演示口令；为便于本地登录直接置为 active，**未经真实邮箱验证**，
  不代表真实学生身份，不用于生产；
- 演示资源保持**未发布草稿**状态，不伪造审核通过、下载量、评分或贡献记录（§33）。

真实目录数据请用 `python -m scripts.seed_catalog`。本脚本依赖 identity / content 模块，
请在对应模块落地后运行：`python -m scripts.seed_demo`。

使用应用配置的 async_session_factory，不自行建 engine，也不调用 create_all（§31.2）。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import (
    AccountStatus,
    PublicationStatus,
    ResourceType,
    ReviewStatus,
    Role,
)
from app.core.security import hash_password
from app.db import async_session_factory
from app.modules.catalog.models import Course

# 演示账号：邮箱与口令都带 demo 前缀，避免与真实账号混淆。
DEMO_STUDENT_EMAIL = "demo.student@example.com"
DEMO_STUDENT_PASSWORD = "demo-password-123"
DEMO_DISPLAY_NAME = "演示学生（演示数据）"
DEMO_RESOURCE_TITLE = "【演示数据】示例学习笔记"


async def _demo_user(session: AsyncSession):
    from app.modules.identity.models import User, UserProfile

    user = (
        await session.execute(select(User).where(User.email == DEMO_STUDENT_EMAIL))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            email=DEMO_STUDENT_EMAIL,
            password_hash=hash_password(DEMO_STUDENT_PASSWORD),
            role=Role.CONTRIBUTOR.value,
            status=AccountStatus.ACTIVE.value,
        )
        session.add(user)
        await session.flush()
        session.add(UserProfile(user_id=user.id, display_name=DEMO_DISPLAY_NAME))
        await session.flush()
    return user


async def _demo_resource(session: AsyncSession, owner_id, course: Course):
    from app.modules.content.models import Resource, ResourceVersion

    resource = (
        await session.execute(
            select(Resource).where(
                Resource.owner_id == owner_id, Resource.title == DEMO_RESOURCE_TITLE
            )
        )
    ).scalar_one_or_none()
    if resource is not None:
        return resource

    resource = Resource(
        course_id=course.id,
        owner_id=owner_id,
        resource_type=ResourceType.NOTE.value,
        # 演示资源不进入公开状态：保持未发布，不伪造审核通过（§33）。
        publication_status=PublicationStatus.UNPUBLISHED.value,
    )
    session.add(resource)
    await session.flush()
    session.add(
        ResourceVersion(
            resource_id=resource.id,
            version_no=1,
            title=DEMO_RESOURCE_TITLE,
            description="演示数据：仅用于界面展示，不含真实内容。",
            review_status=ReviewStatus.DRAFT.value,
            created_by=owner_id,
        )
    )
    await session.flush()
    return resource


async def seed_demo() -> None:
    async with async_session_factory() as session:
        course = (
            await session.execute(select(Course).order_by(Course.name).limit(1))
        ).scalar_one_or_none()
        if course is None:
            raise RuntimeError("课程目录为空，请先运行 python -m scripts.seed_catalog")

        user = await _demo_user(session)
        await _demo_resource(session, user.id, course)
        await session.commit()

    print(
        "seed_demo 完成（演示数据）："
        f"演示账号 {DEMO_STUDENT_EMAIL} / {DEMO_STUDENT_PASSWORD}，"
        "1 条未发布演示资源。"
    )


if __name__ == "__main__":
    asyncio.run(seed_demo())
