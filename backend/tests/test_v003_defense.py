"""0.0.3 主动防御：蜜罐、标记、风险评分与处置的契约测试。

覆盖《AI 对抗攻击防御设计》§10.2—§10.6 的本地可验证部分：

- 诱饵路径返回固定 404，只记录信号，不写业务数据、不含原始 IP 与请求正文；
- 单主体单窗口事件上限生效，超出只累计计数；
- 观察模式不改变限流系数；enforce 模式收紧且到期恢复；
- 撤销误判清除处置但保留历史事件；
- 策略版本递增、非法阈值拒绝、最新有效版本生效；
- 标记只存摘要、明文只返回一次、退役后触发权重降低；
- 保留期清理只删防御事件；
- 管理端接口需要 `defense.manage`。

真实部署下的反向代理链、误伤评估与生产流量验证不在本文件范围内。
"""

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

import app.models_registry  # noqa: F401  # 确保全部表进入 Base.metadata
from app.config import settings
from app.core import cache
from app.core.errors import InvalidInput
from app.core.security import hash_password, token_digest
from app.db import Base, get_db
from app.modules.defense import service
from app.modules.defense.models import DefenseEvent, DefensePolicy, Honeytoken, RiskSubject
from app.modules.identity.models import User
from app.modules.ratelimit import service as ratelimit


@compiles(JSONB, "sqlite")
def _jsonb_for_test(type_, compiler, **kwargs):
    """测试用内存 SQLite 没有 JSONB：按 JSON 渲染（与既有测试一致）。"""
    return "JSON"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DefenseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.factory()
        self.admin = User(
            email="def-admin@example.org",
            password_hash=hash_password("def-admin-pw"),
            role="admin",
            status="active",
        )
        self.contributor = User(
            email="def-contributor@example.org",
            password_hash=hash_password("def-contributor-pw"),
            role="contributor",
            status="active",
        )
        self.db.add_all([self.admin, self.contributor])
        await self.db.flush()
        service._event_counter.clear()
        cache.reset()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()
        service._event_counter.clear()
        cache.reset()

    # --- 辅助 ---------------------------------------------------------------

    async def _signal(self, subject="user:abc", weight=45, kind="honeypot_hit"):
        from app.core.enums import DefenseSignalKind

        return await service.record_signal(
            self.db,
            kind=DefenseSignalKind(kind),
            signal="test_signal",
            weight=weight,
            subject_kind="user",
            subject=subject,
            request_id="req-1",
            method="GET",
            path="/api/v1/resources/8f14e45f-ceea-467a-9f0c-1a2b3c4d5e6f?token=secret",
            decoy_label="unit-test",
        )

    async def _client(self):
        from app.core.deps import get_current_user
        from app.main import app

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        return app, get_current_user

    # --- 蜜罐 ---------------------------------------------------------------

    async def test_honeypot_returns_fixed_404_and_records_signal(self):
        from app.main import app

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:3000",
            ) as client:
                resp = await client.get(settings.HONEYPOT_PATH)
                self.assertEqual(resp.status_code, 404)
                body = resp.json()
                self.assertEqual(body["error"]["code"], "NOT_FOUND")
                # 固定响应：不出现诱饵说明、内部路径或任何业务数据。
                self.assertNotIn("debug-export", resp.text)
                self.assertNotIn("honeypot", resp.text.lower())
        finally:
            app.dependency_overrides.clear()

        rows = (await self.db.execute(select(DefenseEvent))).scalars().all()
        self.assertEqual(len(rows), 1)
        event = rows[0]
        self.assertEqual(event.kind, "honeypot_hit")
        self.assertEqual(event.decoy_label, "internal-debug-export")
        self.assertTrue(event.subject_key.startswith("ip:"))
        # 假名化：不保存原始回环地址。
        self.assertNotIn("127.0.0.1", event.subject_key)
        self.assertNotIn("secret", (event.path_template or ""))

    async def test_honeypot_and_token_entries_are_not_in_openapi(self):
        from app.main import app

        schema = app.openapi()
        self.assertNotIn(settings.HONEYPOT_PATH, schema["paths"])
        self.assertNotIn("/api/v1/defense/tokens/{token}", schema["paths"])

    async def test_honeypot_respects_body_size_limit(self):
        from app.main import app

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:3000",
            ) as client:
                with patch.object(settings, "HONEYPOT_MAX_BODY_BYTES", 8):
                    resp = await client.post(
                        settings.HONEYPOT_PATH, content=b"x" * 64
                    )
                self.assertEqual(resp.status_code, 404)
        finally:
            app.dependency_overrides.clear()
        rows = (await self.db.execute(select(DefenseEvent))).scalars().all()
        self.assertEqual(rows, [])

    # --- 事件上限与评分 ------------------------------------------------------

    async def test_event_cap_aggregates_but_keeps_signal_count(self):
        with patch.object(settings, "DEFENSE_MAX_EVENTS_PER_SUBJECT_WINDOW", 3):
            for _ in range(5):
                await self._signal(subject="user:cap")
        await self.db.flush()
        events = (
            (
                await self.db.execute(
                    select(DefenseEvent).where(DefenseEvent.subject_key == "user:cap")
                )
            )
            .scalars()
            .all()
        )
        self.assertEqual(len(events), 3)
        row = (
            await self.db.execute(
                select(RiskSubject).where(RiskSubject.subject_key == "user:cap")
            )
        ).scalar_one()
        self.assertEqual(row.signal_count, 5)

    async def test_multiple_signals_raise_level_in_observe_mode_without_throttle(self):
        with patch.object(settings, "DEFENSE_MODE", "observe"):
            for _ in range(3):
                await self._signal(subject="user:observe", weight=40)
        row = (
            await self.db.execute(
                select(RiskSubject).where(RiskSubject.subject_key == "user:observe")
            )
        ).scalar_one()
        self.assertEqual(row.level, "high")
        self.assertEqual(row.action, "observe")
        self.assertIsNone(row.action_expires_at)
        self.assertEqual(
            await service.factor_for(self.db, kind="user", subject="observe"), 1.0
        )

    async def test_enforce_mode_throttles_then_recovers_after_expiry(self):
        with patch.object(settings, "DEFENSE_MODE", "enforce"):
            for _ in range(2):
                await self._signal(subject="user:enforce", weight=45)
            row = (
                await self.db.execute(
                    select(RiskSubject).where(RiskSubject.subject_key == "user:enforce")
                )
            ).scalar_one()
            self.assertIn(row.action, {"throttle", "review"})
            self.assertIsNotNone(row.action_expires_at)
            cache.reset()
            factor = await service.factor_for(self.db, kind="user", subject="enforce")
            self.assertLess(factor, 1.0)

            # 处置到期后自动恢复，不需要人工干预。
            row.action_expires_at = _now() - timedelta(seconds=1)
            await self.db.flush()
            cache.reset()
            self.assertEqual(
                await service.factor_for(self.db, kind="user", subject="enforce"), 1.0
            )

    async def test_observe_mode_never_throttles_even_at_high_score(self):
        with patch.object(settings, "DEFENSE_MODE", "observe"):
            for _ in range(4):
                await self._signal(subject="user:obs2", weight=60)
            cache.reset()
            self.assertEqual(
                await service.factor_for(self.db, kind="user", subject="obs2"), 1.0
            )

    async def test_revoke_clears_action_and_keeps_history(self):
        with patch.object(settings, "DEFENSE_MODE", "enforce"):
            for _ in range(3):
                await self._signal(subject="user:revoke", weight=50)
            row = (
                await self.db.execute(
                    select(RiskSubject).where(RiskSubject.subject_key == "user:revoke")
                )
            ).scalar_one()
            await service.revoke_subject(
                self.db,
                subject_id=row.id,
                actor_id=self.admin.id,
                reason="误判：校内安全扫描",
            )
            await self.db.flush()
            self.assertEqual(row.action, "observe")
            self.assertEqual(row.level, "normal")
            self.assertEqual(row.score, 0.0)
            self.assertIsNotNone(row.revoked_at)
            events = (
                (
                    await self.db.execute(
                        select(DefenseEvent).where(
                            DefenseEvent.subject_key == "user:revoke"
                        )
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual(len(events), 3)
            cache.reset()
            self.assertEqual(
                await service.factor_for(self.db, kind="user", subject="revoke"), 1.0
            )

    async def test_decayed_score_weights_recent_signals_more(self):
        now = _now()
        score = service.decayed_score(
            [(40.0, now), (40.0, now - timedelta(seconds=900))],
            now=now,
            half_life_seconds=900,
        )
        self.assertAlmostEqual(score, 60.0, places=2)

    # --- 策略 ---------------------------------------------------------------

    async def test_policy_versions_increase_and_latest_wins(self):
        first = await service.register_policy(
            self.db,
            actor_id=self.admin.id,
            mode="observe",
            suspicious_score=30,
            high_score=70,
            throttle_factor_suspicious=0.5,
            throttle_factor_high=0.25,
            window_seconds=900,
            decay_half_life_seconds=900,
            action_ttl_seconds=900,
            valid_from=None,
            valid_until=None,
            note="初始策略",
        )
        second = await service.register_policy(
            self.db,
            actor_id=self.admin.id,
            mode="enforce",
            suspicious_score=20,
            high_score=60,
            throttle_factor_suspicious=0.4,
            throttle_factor_high=0.2,
            window_seconds=600,
            decay_half_life_seconds=600,
            action_ttl_seconds=300,
            valid_from=None,
            valid_until=None,
            note="收紧",
        )
        self.assertEqual((first.version, second.version), (1, 2))
        policy = await service.active_policy(self.db)
        self.assertEqual(policy.source, "policy:2")
        self.assertEqual(policy.mode, "enforce")
        self.assertEqual(policy.window_seconds, 600)

    async def test_policy_rejects_invalid_thresholds(self):
        with self.assertRaises(InvalidInput):
            await service.register_policy(
                self.db,
                actor_id=self.admin.id,
                mode="enforce",
                suspicious_score=80,
                high_score=70,
                throttle_factor_suspicious=0.5,
                throttle_factor_high=0.25,
                window_seconds=900,
                decay_half_life_seconds=900,
                action_ttl_seconds=900,
                valid_from=None,
                valid_until=None,
                note=None,
            )

    async def test_expired_policy_is_ignored(self):
        await service.register_policy(
            self.db,
            actor_id=self.admin.id,
            mode="enforce",
            suspicious_score=10,
            high_score=20,
            throttle_factor_suspicious=0.5,
            throttle_factor_high=0.25,
            window_seconds=900,
            decay_half_life_seconds=900,
            action_ttl_seconds=900,
            valid_from=_now() - timedelta(days=2),
            valid_until=_now() - timedelta(days=1),
            note="已过期",
        )
        policy = await service.active_policy(self.db)
        self.assertEqual(policy.source, "settings")

    # --- 标记 ---------------------------------------------------------------

    async def test_honeytoken_stores_digest_only_and_retire_lowers_weight(self):
        row, raw = await service.create_honeytoken(
            self.db,
            actor_id=self.admin.id,
            label="isolated-sample-01",
            placement="isolated_material",
            placement_ref="fixture:1",
            expected_visitor="internal-qa",
            expires_at=None,
            note=None,
        )
        self.assertEqual(row.token_digest, token_digest(raw))
        self.assertNotEqual(row.token_digest, raw)

        label = await service.trigger_honeytoken(
            self.db,
            raw_token=raw,
            subject_kind="user",
            subject="user:token",
            request_id="req-2",
            method="GET",
            path="/api/v1/defense/tokens/{token}",
        )
        self.assertEqual(label, "isolated-sample-01")

        await service.retire_honeytoken(
            self.db, token_id=row.id, actor_id=self.admin.id, reason="试点结束"
        )
        await self.db.flush()
        await service.trigger_honeytoken(
            self.db,
            raw_token=raw,
            subject_kind="user",
            subject="user:token",
            request_id="req-3",
            method="GET",
            path="/api/v1/defense/tokens/{token}",
        )
        await self.db.flush()
        events = (
            (
                await self.db.execute(
                    select(DefenseEvent)
                    .where(DefenseEvent.subject_key == "user:token")
                    .order_by(DefenseEvent.occurred_at)
                )
            )
            .scalars()
            .all()
        )
        self.assertEqual(len(events), 2)
        self.assertGreater(events[0].weight, events[1].weight)
        self.assertEqual(events[0].decoy_label, "isolated-sample-01")

    async def test_unknown_token_is_recorded_without_label(self):
        label = await service.trigger_honeytoken(
            self.db,
            raw_token="not-a-registered-token",
            subject_kind="ip",
            subject="ip:abcdef0123456789",
            request_id="req-4",
            method="GET",
            path="/api/v1/defense/tokens/{token}",
        )
        self.assertIsNone(label)
        await self.db.flush()
        event = (await self.db.execute(select(DefenseEvent))).scalars().one()
        self.assertEqual(event.signal, "unknown_token_use")
        self.assertIsNone(event.decoy_label)

    # --- 清理 ---------------------------------------------------------------

    async def test_cleanup_only_removes_expired_defense_events(self):
        now = _now()
        self.db.add_all(
            [
                DefenseEvent(
                    kind="honeypot_hit",
                    signal="old",
                    weight=10,
                    subject_kind="user",
                    subject_key="user:old",
                    occurred_at=now - timedelta(days=40),
                ),
                DefenseEvent(
                    kind="honeypot_hit",
                    signal="fresh",
                    weight=10,
                    subject_kind="user",
                    subject_key="user:fresh",
                    occurred_at=now,
                ),
            ]
        )
        await self.db.flush()
        removed = await service.cleanup_expired(self.db, retention_days=30)
        self.assertEqual(removed, 1)
        remaining = (await self.db.execute(select(DefenseEvent))).scalars().all()
        self.assertEqual([row.signal for row in remaining], ["fresh"])

    # --- 管理端权限 ----------------------------------------------------------

    async def test_admin_endpoints_require_defense_permission(self):
        from app.core.deps import get_current_user
        from app.main import app

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:3000",
                headers={"Origin": "http://localhost:3000", "X-CSRF-Token": "test"},
                cookies={settings.CSRF_COOKIE_NAME: "test"},
            ) as client:
                app.dependency_overrides[get_current_user] = lambda: self.contributor
                self.assertEqual(
                    (await client.get("/api/v1/admin/defense/overview")).status_code,
                    403,
                )
                self.assertEqual(
                    (await client.get("/api/v1/admin/defense/events")).status_code, 403
                )
                app.dependency_overrides[get_current_user] = lambda: self.admin
                resp = await client.get("/api/v1/admin/defense/overview")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json()["mode"], settings.DEFENSE_MODE)
                resp = await client.get("/api/v1/admin/defense/policies")
                self.assertEqual(resp.status_code, 200)
                self.assertIn("active", resp.json())
        finally:
            app.dependency_overrides.clear()

    async def test_admin_can_create_honeytoken_and_sees_plaintext_once(self):
        from app.core.deps import get_current_user
        from app.main import app

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:3000",
                headers={"Origin": "http://localhost:3000", "X-CSRF-Token": "test"},
                cookies={settings.CSRF_COOKIE_NAME: "test"},
            ) as client:
                app.dependency_overrides[get_current_user] = lambda: self.admin
                resp = await client.post(
                    "/api/v1/admin/defense/honeytokens",
                    json={
                        "label": "decoy-http-01",
                        "placement": "static_decoy",
                        "note": "仅用于演练",
                    },
                )
                self.assertEqual(resp.status_code, 201)
                body = resp.json()
                self.assertTrue(body["raw_token"])
                self.assertEqual(body["token"]["label"], "decoy-http-01")

                listed = await client.get("/api/v1/admin/defense/honeytokens")
                self.assertEqual(listed.status_code, 200)
                item = listed.json()["items"][0]
                self.assertNotIn("raw_token", item)
                self.assertNotIn("token_digest", item)
        finally:
            app.dependency_overrides.clear()

    async def test_ratelimit_applies_risk_factor_only_when_below_one(self):
        """限流只被收紧、不被放宽：factor=0.5 时同一档位更早触发 429。"""
        async def fake_factor(db, *, kind, subject):
            return 0.5

        with patch.object(
            settings,
            "RATE_LIMIT_SEARCH",
            {"anon": 4, "user": 4, "contributor": 4, "reviewer": 4, "admin": 4},
        ), patch("app.modules.defense.service.factor_for", fake_factor):
            request = _request()
            allowed = 0
            for _ in range(4):
                try:
                    await ratelimit.enforce(
                        self.db, action="search", user=self.contributor, session=None, request=request
                    )
                    allowed += 1
                except Exception as exc:  # noqa: BLE001
                    from app.core.errors import RateLimited

                    self.assertIsInstance(exc, RateLimited)
            # 限额 4 * 0.5 -> 2；第 3 次起拒绝。
            self.assertEqual(allowed, 2)


def _request():
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/search",
            "headers": [(b"host", b"testserver")],
            "client": ("203.0.113.9", 51234),
        }
    )


class CspReportTests(unittest.IsolatedAsyncioTestCase):
    """CSP 违规报告端点：无 CSRF、限体积、只写日志不落库。"""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.factory()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def _client(self):
        from app.main import app

        async def database():
            yield self.db

        app.dependency_overrides[get_db] = database
        return app

    async def test_legacy_report_is_accepted_without_csrf_token(self):
        app = await self._client()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:3000",
            ) as client:
                resp = await client.post(
                    "/api/v1/csp/reports",
                    json={
                        "csp-report": {
                            "document-uri": "https://example.org/courses/1?token=secret",
                            "violated-directive": "script-src-elem",
                            "blocked-uri": "https://evil.example/x.js?q=1",
                            "disposition": "report",
                        }
                    },
                )
                self.assertEqual(resp.status_code, 204)
                self.assertEqual(resp.text, "")
        finally:
            from app.main import app as fastapi_app

            fastapi_app.dependency_overrides.clear()

    async def test_oversized_and_malformed_reports_are_dropped(self):
        app = await self._client()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:3000",
            ) as client:
                big = await client.post(
                    "/api/v1/csp/reports",
                    content=b"x" * 9000,
                    headers={"Content-Type": "application/csp-report"},
                )
                self.assertEqual(big.status_code, 204)
                bad = await client.post(
                    "/api/v1/csp/reports",
                    content=b"{not-json",
                    headers={"Content-Type": "application/csp-report"},
                )
                self.assertEqual(bad.status_code, 204)
        finally:
            from app.main import app as fastapi_app

            fastapi_app.dependency_overrides.clear()

    async def test_reports_are_not_persisted_as_defense_events(self):
        app = await self._client()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:3000",
            ) as client:
                await client.post(
                    "/api/v1/csp/reports",
                    json=[{"type": "csp-violation", "body": {"effectiveDirective": "script-src"}}],
                )
        finally:
            from app.main import app as fastapi_app

            fastapi_app.dependency_overrides.clear()
        rows = (await self.db.execute(select(DefenseEvent))).scalars().all()
        self.assertEqual(rows, [])

    def test_report_endpoint_not_in_openapi(self):
        from app.main import app

        schema = app.openapi()
        self.assertNotIn("/api/v1/csp/reports", schema["paths"])


if __name__ == "__main__":
    unittest.main()
