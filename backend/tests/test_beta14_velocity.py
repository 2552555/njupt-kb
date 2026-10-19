"""Beta14 下载速率风控（access 域：只记录、不阻断；隔离内存 SQLite）。

覆盖：
- 窗口内下载授权次数达到阈值：产生 security.download_velocity_flagged 审计，
  且不阻断当次下载（仍返回签发结果）；
- 未达阈值：不产生审计；
- 审计写入独立会话：业务事务回滚后速率标记仍然生效（避免回滚丢标记，
  modules/ratelimit 独立计数会话先例）。

计数走 core.ratelimit 固定窗口（Redis 不可用时进程内计数）；存储全部打桩，
不连接真实 S3/Redis。生产 PostgreSQL 并发不在本机验证范围内。
"""

import unittest
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models_registry  # noqa: F401  # 独立内存库建表需要全部模型已注册
import test_content_workflow as workflow
from app.config import settings
from app.core import storage as storage_module
from app.core.ratelimit import reset_rate_limit
from app.core.storage import PresignedDownload
from app.db import Base
from app.modules.access import service as access_service
from app.modules.access.models import AccessPolicy, DownloadGrant
from app.modules.content.models import ResourceFile, RightsDeclaration, VersionFile
from app.modules.moderation.models import AuditLog

VELOCITY_ACTION = "security.download_velocity_flagged"


class Beta14VelocityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        # 可下载的最小事实链：已发布、审核通过、公开原件策略、来源声明、文件已扫描。
        self.file = ResourceFile(
            uploader_id=self.owner.id,
            storage_key="beta14-source",
            original_name="beta14.pdf",
            detected_mime="application/pdf",
            scan_status="passed",
        )
        self.db.add(self.file)
        await self.db.flush()
        self.db.add(
            VersionFile(
                version_id=self.version.id,
                file_id=self.file.id,
                purpose="primary",
                display_order=0,
            )
        )
        self.db.add(
            RightsDeclaration(
                version_id=self.version.id,
                declarant_id=self.owner.id,
                rights_basis="original",
                declaration_text_version="v1",
            )
        )
        self.version.review_status = "approved"
        self.resource.published_version_id = self.version.id
        self.resource.publication_status = "published"
        self.db.add(
            AccessPolicy(
                resource_id=self.resource.id,
                metadata_scope="public",
                preview_scope="authenticated",
                original_scope="public",
            )
        )
        await self.db.commit()
        # 进程内计数跨用例残留会互相污染：每个用例开跑前重置该用户的速率桶。
        await reset_rate_limit(f"download-velocity:{self.other.id}")

        stack = ExitStack()
        self.addCleanup(stack.close)

        async def fake_presign(storage_key, *, bucket, file_name=None, expires_in=None):
            return PresignedDownload(
                url=f"https://storage.test/{storage_key}",
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
            )

        stack.enter_context(
            patch.object(storage_module.storage, "presign_download", fake_presign)
        )

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def _download(self, key: str):
        return await access_service.issue_download_grant(
            self.db,
            user=self.other,
            resource_id=self.resource.id,
            version_id=self.version.id,
            file_id=self.file.id,
            idempotency_key=key,
            request_id="beta14-velocity",
        )

    async def _velocity_audits(self):
        return list(
            (
                await self.db.scalars(
                    select(AuditLog).where(AuditLog.action == VELOCITY_ACTION)
                )
            ).all()
        )

    def _reuse_session_factory(self):
        """测试把"独立审计会话"复用为测试会话（ratelimit deps 先例：测试复用内存库会话）。

        SQLite 单写者约束下无法在业务事务进行中开第二个真实连接，
        因此按限流模块自己的测试约定复用内存库会话。
        """

        @asynccontextmanager
        async def _session():
            yield self.db

        return lambda: _session()

    async def test_over_threshold_flags_audit_and_does_not_block(self):
        """窗口内超阈值：产生审计（附阈值与窗口），且不阻断当次下载。"""
        with (
            patch.object(
                access_service, "async_session_factory", self._reuse_session_factory()
            ),
            patch.object(settings, "DOWNLOAD_VELOCITY_WINDOW_COUNT", 3),
        ):
            for i in range(3):
                await self._download(f"beta14-ok-{i}")
            # 第 4 次超阈值：必须仍然完成授权签发（不阻断），并落审计标记。
            result = await self._download("beta14-over")
        self.assertIsNotNone(result.download_url)
        self.assertEqual(result.file_name, "beta14.pdf")
        audits = await self._velocity_audits()
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].actor_id, self.other.id)
        self.assertEqual(audits[0].after_state["limit"], 3)
        self.assertEqual(audits[0].after_state["window_seconds"], 300)
        self.assertEqual(audits[0].request_id, "beta14-velocity")
        # 四次授权（含超阈值那次）全部落库，说明没有拦截任何一次下载。
        grants = list((await self.db.scalars(select(DownloadGrant))).all())
        self.assertEqual(len(grants), 4)

    async def test_below_threshold_does_not_flag(self):
        """未超阈值：不产生审计，下载正常。"""
        with patch.object(settings, "DOWNLOAD_VELOCITY_WINDOW_COUNT", 10):
            results = [await self._download(f"beta14-under-{i}") for i in range(3)]
        self.assertTrue(all(result.download_url for result in results))
        self.assertEqual(await self._velocity_audits(), [])

    async def test_flag_survives_business_rollback(self):
        """审计写独立会话：业务事务回滚后速率标记仍然生效（避免回滚丢标记）。"""
        # 独立内存库承接独立会话的审计写入，与业务库物理分离。
        independent = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.addAsyncCleanup(independent.dispose)
        async with independent.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        independent_factory = async_sessionmaker(independent, expire_on_commit=False)
        downloader_id = self.other.id  # 回滚会使业务会话内对象过期，先取 ID
        with (
            patch.object(access_service, "async_session_factory", independent_factory),
            patch.object(settings, "DOWNLOAD_VELOCITY_WINDOW_COUNT", 1),
        ):
            await self._download("beta14-rb-1")
            result = await self._download("beta14-rb-2")  # 第二次超阈值，写独立库
            self.assertIsNotNone(result.download_url)
            # 模拟外层请求失败：业务事务整体回滚。
            await self.db.rollback()
        # 业务库里的授权与审计都随回滚消失。
        self.assertEqual(await self._velocity_audits(), [])
        self.assertEqual(list((await self.db.scalars(select(DownloadGrant))).all()), [])
        # 独立库里的速率标记仍在（会话内读取，避免关闭后属性过期触发懒加载）。
        async with independent_factory() as session:
            audits = list(
                (
                    await session.scalars(
                        select(AuditLog).where(AuditLog.action == VELOCITY_ACTION)
                    )
                ).all()
            )
            self.assertEqual(len(audits), 1)
            self.assertEqual(audits[0].actor_id, downloader_id)


if __name__ == "__main__":
    unittest.main()
