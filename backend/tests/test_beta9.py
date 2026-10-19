"""P2 closure contracts on isolated SQLite; no production dependency claim."""

import unittest
from unittest.mock import patch, AsyncMock

from sqlalchemy import select, func

from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.core.enums import DomainEventType
from app.core.events import emit_event
from app.modules.access.models import AccessPolicy
from app.modules.interaction import service
from app.modules.interaction import request_management as requests
from app.modules.interaction.models import ResourceRequestFollow, Follow
from app.modules.interaction.schemas import RequestStatusUpdate
from app.modules.notification.models import DomainEvent, Notification
from app.modules.notification import service as notifications
from app.modules.notification.operations import retry_event
from app.modules.moderation.models import AuditLog
import test_content_workflow as workflow


class Beta9Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.resource.publication_status = "published"
        self.resource.published_version_id = self.version.id
        self.version.review_status = "approved"
        self.policy = AccessPolicy(
            resource_id=self.resource.id, metadata_scope="public"
        )
        self.db.add(self.policy)
        await self.db.flush()
        result = await service.create_request(
            self.db,
            user_id=self.owner.id,
            course_id=self.course.id,
            scope=None,
            description="需要课程笔记",
        )
        self.request_id = result.request.id

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def change(self, status, revision=1, resource=None, user=None):
        return await requests.change_status(
            self.db,
            user=user or self.owner,
            request_id=self.request_id,
            payload=RequestStatusUpdate(
                status=status,
                expected_revision=revision,
                resource_id=resource,
                reason="核实后处理",
            ),
            request_trace_id="beta9",
        )

    async def test_state_permissions_idempotency_and_history(self):
        with self.assertRaises(Forbidden):
            await self.change("closed", user=self.other)
        result = await self.change("fulfilled", resource=self.resource.id)
        self.assertTrue(result.resolution_available)
        self.assertEqual(result.revision, 2)
        self.assertEqual(
            (await self.change("fulfilled", resource=self.resource.id)).revision, 2
        )
        with self.assertRaises(Conflict):
            await self.change("open")
        await self.change("open", revision=2)
        await self.change("closed", revision=3)
        with self.assertRaises(Forbidden):
            await self.change("open", revision=4)
        self.other.role = "admin"
        await self.change("open", revision=4, user=self.other)
        count = await self.db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.action == "request.status_changed"
            )
        )
        self.assertEqual(count, 4)
        audits = list(
            (
                await self.db.scalars(
                    select(AuditLog).where(AuditLog.action == "request.status_changed")
                )
            ).all()
        )
        self.assertTrue(
            any(
                a.after_state.get("resolved_resource_id") == str(self.resource.id)
                for a in audits
            )
        )
        self.assertTrue(
            any(
                a.before_state.get("resolved_resource_id") == str(self.resource.id)
                and a.after_state.get("resolved_resource_id") is None
                for a in audits
            )
        )

    async def test_public_candidate_validation_and_later_revocation(self):
        self.policy.metadata_scope = "private"
        await self.db.flush()
        with self.assertRaises(NotFound):
            await self.change("fulfilled", resource=self.resource.id)
        self.policy.metadata_scope = "public"
        await self.db.flush()
        await self.change("fulfilled", resource=self.resource.id)
        self.resource.publication_status = "withdrawn"
        await self.db.flush()
        result = await requests.request_detail(self.db, self.request_id)
        self.assertEqual(result.status, "fulfilled")
        self.assertIsNone(result.resolved_resource_id)
        self.assertIsNone(result.resolved_resource_title)
        self.assertFalse(result.resolution_available)

    async def test_responses_deduplicate_and_follow_course(self):
        first = await requests.respond(
            self.db,
            user=self.other,
            request_id=self.request_id,
            resource_id=self.resource.id,
        )
        second = await requests.respond(
            self.db,
            user=self.other,
            request_id=self.request_id,
            resource_id=self.resource.id,
        )
        self.assertEqual(first.id, second.id)
        from app.modules.catalog.models import Course

        other_course = Course(
            university_id=self.school.id, name="另一课程", code="OTHER"
        )
        self.db.add(other_course)
        await self.db.flush()
        self.resource.course_id = other_course.id
        await self.db.flush()
        with self.assertRaises(InvalidInput):
            await requests.respond(
                self.db,
                user=self.other,
                request_id=self.request_id,
                resource_id=self.resource.id,
            )
        page = await requests.list_responses(self.db, self.request_id)
        self.assertIsNone(page.items[0].resource_id)
        await self.change("closed")
        with self.assertRaises(Conflict):
            await requests.respond(
                self.db,
                user=self.other,
                request_id=self.request_id,
                resource_id=self.resource.id,
            )

    async def test_request_notifications_are_once_and_do_not_include_restricted_titles(
        self,
    ):
        self.db.add(
            ResourceRequestFollow(request_id=self.request_id, user_id=self.other.id)
        )
        await self.change("fulfilled", resource=self.resource.id)
        event = await self.db.scalar(
            select(DomainEvent).where(DomainEvent.event_type == "request.updated")
        )
        event_id, owner_id, other_id = event.id, self.owner.id, self.other.id
        await self.db.commit()
        self.assertTrue(await notifications._process_event(self.db, event_id))
        self.assertFalse(await notifications._process_event(self.db, event_id))
        rows = list((await self.db.scalars(select(Notification))).all())
        self.assertEqual({row.user_id for row in rows}, {owner_id, other_id})
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("private test" not in row.body for row in rows))

    async def test_creator_and_course_followers_are_merged(self):
        self.db.add_all(
            [
                Follow(
                    user_id=self.other.id,
                    target_type="course",
                    target_id=self.course.id,
                ),
                Follow(
                    user_id=self.other.id,
                    target_type="creator",
                    target_id=self.owner.id,
                ),
            ]
        )
        event_id = await emit_event(
            self.db,
            event_type=DomainEventType.RESOURCE_PUBLISHED,
            subject_type="resource",
            subject_id=self.resource.id,
            payload={"version_id": str(self.version.id)},
        )
        await self.db.commit()
        self.assertTrue(await notifications._process_event(self.db, event_id))
        self.assertEqual(await self.db.scalar(select(func.count(Notification.id))), 1)

    async def test_failed_event_retry_preserves_successful_business_state(self):
        result = await self.change("closed")
        request_id = result.id
        event = await self.db.scalar(
            select(DomainEvent).where(DomainEvent.event_type == "request.updated")
        )
        event_id = event.id
        await self.db.commit()
        with patch.object(
            notifications,
            "_build_notifications",
            new=AsyncMock(side_effect=RuntimeError("fixture failure")),
        ):
            self.assertFalse(await notifications._process_event(self.db, event_id))
        row = await self.db.get(DomainEvent, event_id)
        self.assertEqual(row.status, "failed")
        self.assertEqual(
            (await requests.request_detail(self.db, request_id)).status, "closed"
        )
        self.other.role = "admin"
        result = await retry_event(
            self.db,
            event_id=event_id,
            expected_attempts=row.attempts,
            user=self.other,
            trace="beta9",
        )
        self.assertEqual(result["status"], "pending")
        await self.db.commit()
        self.assertTrue(await notifications._process_event(self.db, event_id))

    async def test_http_state_contract_and_admin_recovery_gate(self):
        import httpx
        from app.config import settings
        from app.core.deps import get_current_user
        from app.db import get_db
        from app.main import app

        async def database():
            yield self.db

        async def user():
            return self.owner

        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:3000",
                headers={"Origin": "http://localhost:3000", "X-CSRF-Token": "test"},
                cookies={settings.CSRF_COOKIE_NAME: "test"},
            ) as client:
                path = f"/api/v1/requests/{self.request_id}"
                self.assertEqual((await client.get(path)).status_code, 200)
                self.assertEqual(
                    (
                        await client.post(
                            path + "/status",
                            json={
                                "status": "closed",
                                "expected_revision": 1,
                                "reason": "完成",
                            },
                        )
                    ).status_code,
                    401,
                )
                app.dependency_overrides[get_current_user] = user
                self.assertEqual(
                    (await client.get("/api/v1/admin/events")).status_code, 403
                )
                response = await client.post(
                    path + "/status",
                    json={"status": "closed", "expected_revision": 1, "reason": "完成"},
                )
                self.assertEqual(response.status_code, 200, response.text)
        finally:
            app.dependency_overrides.clear()
