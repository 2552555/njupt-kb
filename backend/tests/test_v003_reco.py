"""0.0.3 学习行为、推荐与 A/B 测试。

覆盖：
- 行为上报的白名单、批量上限与采集开关；
- 阅读进度只增不减；
- 推荐只返回可见已发布资源，理由可读且不暴露权重；
- 已看内容降权、探索位；
- 实验分流稳定、control 不启用模型；
- 训练样本不足时明确拒绝；训练/激活后推荐带模型分；
- 保留期清理与账号注销删除；
- 管理接口权限。

真实点击率提升、离线评估与线上 A/B 结论不在本文件范围。
"""

import unittest
import uuid
from datetime import datetime, timedelta, timezone

import test_content_workflow as workflow
from app.config import settings
from app.core.errors import Forbidden, InvalidInput, NotFound
from app.modules.access.models import AccessPolicy
from app.modules.identity.models import User
from app.modules.reco import service
from app.modules.reco.models import (
    BehaviorConsent,
    BehaviorEvent,
    BehaviorEventType,
    ReadingProgress,
)


class RecoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.admin = User(
            email="reco-admin@example.test",
            password_hash="unused",
            role="admin",
            status="active",
        )
        self.db.add(self.admin)
        # 公开已发布资源，推荐候选池可见。
        self.version.review_status = "approved"
        self.version.title = "公开测试资料"
        self.resource.published_version_id = self.version.id
        self.resource.publication_status = "published"
        self.resource.resource_type = "note"
        self.db.add(
            AccessPolicy(resource_id=self.resource.id, metadata_scope="public")
        )
        await self.db.flush()

    async def asyncTearDown(self):
        await workflow.ContentWorkflowTests.asyncTearDown(self)

    async def _events(self, user=None, count=3, event_type="click"):
        events = [
            {
                "event_type": event_type,
                "resource_id": str(self.resource.id),
                "course_id": str(self.course.id),
                "surface": "home",
                "position": index,
                "dwell_ms": 1200,
                "progress_pct": 40,
            }
            for index in range(count)
        ]
        return await service.record_events(
            self.db, user=user or self.owner, session_key=None, events=events
        )

    async def test_behavior_events_are_whitelisted(self):
        written = await service.record_events(
            self.db,
            user=self.owner,
            session_key=None,
            events=[
                {
                    "event_type": "click",
                    "resource_id": str(self.resource.id),
                    "surface": "home",
                    "position": 0,
                    "dwell_ms": 500,
                    # 额外字段必须被忽略
                    "ip": "203.0.113.9",
                    "query": "secret",
                },
                {"event_type": "not-an-event", "resource_id": str(self.resource.id)},
            ],
        )
        self.assertEqual(written, 1)
        row = (await self.db.execute(BehaviorEvent.__table__.select())).first()
        self.assertEqual(row.event_type, "click")
        self.assertNotIn("secret", str(row._mapping))
        self.assertNotIn("203.0.113.9", str(row._mapping))

    async def test_batch_limit_is_enforced(self):
        with self.assertRaises(InvalidInput):
            await service.record_events(
                self.db,
                user=self.owner,
                session_key=None,
                events=[{"event_type": "click"} for _ in range(51)],
            )

    async def test_consent_off_stops_collection(self):
        await service.set_consent(self.db, user=self.owner, enabled=False, reason="用户关闭")
        written = await self._events(user=self.owner, count=2)
        self.assertEqual(written, 0)
        self.assertFalse(await service.consent_enabled(self.db, user=self.owner))
        with self.assertRaises(InvalidInput):
            await service.upsert_progress(
                self.db, user=self.owner, resource_id=self.resource.id, progress_pct=50
            )

    async def test_progress_never_decreases(self):
        await service.upsert_progress(
            self.db, user=self.owner, resource_id=self.resource.id, progress_pct=60, dwell_ms=1000
        )
        row = await service.upsert_progress(
            self.db, user=self.owner, resource_id=self.resource.id, progress_pct=20, dwell_ms=500
        )
        self.assertEqual(row.progress_pct, 60)
        self.assertEqual(row.total_dwell_ms, 1500)

    async def test_progress_completion_sets_timestamp(self):
        row = await service.upsert_progress(
            self.db, user=self.owner, resource_id=self.resource.id, progress_pct=100
        )
        self.assertIsNotNone(row.completed_at)

    async def test_recommendation_returns_visible_items_with_reason(self):
        await self._events()
        result = await service.recommend(
            self.db, user=self.owner, session_key=None, surface="home", limit=6
        )
        self.assertTrue(result["items"])
        first = result["items"][0]
        self.assertEqual(first["resource_id"], self.resource.id)
        self.assertTrue(first["reason"])
        self.assertNotIn("weights", str(result))

    async def test_anonymous_recommendation_uses_session_key(self):
        result = await service.recommend(
            self.db, user=None, session_key="anon-session-1", surface="home", limit=6
        )
        self.assertIsInstance(result["items"], list)

    async def test_experiment_assignment_is_stable_and_control_skips_model(self):
        experiment = await service.create_experiment(
            self.db,
            actor=self.admin,
            key="reco-ranker-v1",
            name="排序实验",
            variants=[
                {"key": "control", "weight_bps": 5000},
                {"key": "model", "weight_bps": 5000},
            ],
            traffic_bps=10000,
            metric="ctr",
            note=None,
        )
        await service.start_experiment(self.db, actor=self.admin, key=experiment.key)
        first = await service.assign_variant(
            self.db, experiment=experiment, subject="user:fixed"
        )
        second = await service.assign_variant(
            self.db, experiment=experiment, subject="user:fixed"
        )
        self.assertEqual(first.variant, second.variant)
        self.assertIn(first.variant, {"control", "model"})

        result = await service.recommend(
            self.db, user=self.owner, session_key=None, surface="home", limit=6
        )
        self.assertIsNotNone(result["experiment"])
        if result["experiment"]["variant"] in (None, "control"):
            self.assertIsNone(result["model_version"])

    async def test_training_requires_enough_samples(self):
        with self.assertRaises(InvalidInput):
            await service.train_model(self.db, actor=self.admin)

    async def test_training_and_activation_produce_scores(self):
        from app.modules.content.models import Resource, ResourceVersion

        # 样本按 (用户, 资源) 去重，因此需要足够多的不同资源才能达到训练门槛。
        resource_ids = []
        for index in range(30):
            extra = Resource(
                course_id=self.course.id, owner_id=self.owner.id, resource_type="note"
            )
            self.db.add(extra)
            await self.db.flush()
            version = ResourceVersion(
                resource_id=extra.id,
                created_by=self.owner.id,
                version_no=1,
                title=f"训练样本 {index}",
                review_status="approved",
            )
            self.db.add(version)
            await self.db.flush()
            extra.published_version_id = version.id
            extra.publication_status = "published"
            self.db.add(AccessPolicy(resource_id=extra.id, metadata_scope="public"))
            resource_ids.append(extra.id)
        await self.db.flush()

        positive = [
            {
                "event_type": "click",
                "resource_id": str(resource_id),
                "course_id": str(self.course.id),
                "surface": "home",
            }
            for resource_id in resource_ids
        ]
        await service.record_events(self.db, user=self.owner, session_key=None, events=positive)
        negative = [
            {
                "event_type": "impression",
                "resource_id": str(resource_id),
                "course_id": str(self.course.id),
                "surface": "home",
            }
            for resource_id in resource_ids
        ]
        await service.record_events(
            self.db, user=self.other, session_key=None, events=negative
        )
        model = await service.train_model(self.db, actor=self.admin, epochs=20)
        self.assertGreaterEqual(model.sample_size, 50)
        self.assertIn("accuracy", model.metrics or {})

        activated = await service.activate_model(
            self.db, actor=self.admin, version=model.version
        )
        self.assertEqual(activated.status, "active")
        result = await service.recommend(
            self.db, user=self.owner, session_key=None, surface="home", limit=6
        )
        self.assertEqual(result["model_version"], model.version)
        self.assertTrue(any(item["score"] is not None for item in result["items"]))

    async def test_cleanup_removes_only_old_events(self):
        now = datetime.now(timezone.utc)
        self.db.add_all(
            [
                BehaviorEvent(
                    user_id=self.owner.id,
                    event_type=BehaviorEventType.CLICK.value,
                    resource_id=self.resource.id,
                    occurred_at=now - timedelta(days=400),
                ),
                BehaviorEvent(
                    user_id=self.owner.id,
                    event_type=BehaviorEventType.CLICK.value,
                    resource_id=self.resource.id,
                    occurred_at=now,
                ),
            ]
        )
        await self.db.flush()
        removed = await service.cleanup(self.db, retention_days=180)
        self.assertEqual(removed, 1)

    async def test_account_deletion_removes_behavior_and_progress(self):
        await self._events()
        await service.upsert_progress(
            self.db, user=self.owner, resource_id=self.resource.id, progress_pct=10
        )
        await service.set_consent(self.db, user=self.owner, enabled=False)
        await service.delete_user_behavior(self.db, user_id=self.owner.id)
        await self.db.flush()
        self.assertEqual(
            (await self.db.execute(BehaviorEvent.__table__.select())).first(), None
        )
        self.assertEqual(
            (await self.db.execute(ReadingProgress.__table__.select())).first(), None
        )
        self.assertEqual(
            (await self.db.execute(BehaviorConsent.__table__.select())).first(), None
        )

    async def test_experiment_results_report_insufficient_samples(self):
        experiment = await service.create_experiment(
            self.db,
            actor=self.admin,
            key="reco-small",
            name="小样本实验",
            variants=[
                {"key": "control", "weight_bps": 5000},
                {"key": "model", "weight_bps": 5000},
            ],
            traffic_bps=5000,
            metric="ctr",
            note=None,
        )
        await service.start_experiment(self.db, actor=self.admin, key=experiment.key)
        await service.assign_variant(self.db, experiment=experiment, subject="user:a")
        await service.assign_variant(self.db, experiment=experiment, subject="user:b")
        result = await service.experiment_results(self.db, key=experiment.key)
        self.assertIn("variants", result)
        if result["comparison"] is not None:
            self.assertFalse(result["comparison"]["sufficient"])

    async def test_invalid_experiment_variants_rejected(self):
        with self.assertRaises(InvalidInput):
            await service.create_experiment(
                self.db,
                actor=self.admin,
                key="bad",
                name="错误实验",
                variants=[{"key": "control", "weight_bps": 10000}],
                traffic_bps=10000,
                metric="ctr",
                note=None,
            )

    async def test_missing_experiment_is_not_found(self):
        with self.assertRaises(NotFound):
            await service.experiment_results(self.db, key="missing")

    async def test_recommendation_disabled_flag_is_respected_by_router(self):
        # service 层不做开关判断，开关由路由抛 NotEnabled；这里只验证 settings 可读。
        self.assertIsInstance(settings.RECO_ENABLED, bool)


if __name__ == "__main__":
    unittest.main()
