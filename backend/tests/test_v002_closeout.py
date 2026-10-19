"""0.0.2 lifecycle and session revocation regressions (isolated SQLite)."""

import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from app.core.enums import DomainEventType, ModerationDecisionType, NotificationType
from app.core.errors import Conflict, Forbidden, NotFound
from app.modules.assignment.models import ResourceAssignment
from app.modules.creator import service as creator
from app.modules.creator.models import ContributorApplication
from app.modules.identity.models import Session, User
from app.modules.notification import stream
from app.modules.notification.models import DomainEvent

import test_content_workflow as workflow


class V002CloseoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.admin = User(
            email="admin@example.test",
            password_hash="unused",
            role="admin",
            status="active",
        )
        self.application = ContributorApplication(
            user_id=self.owner.id,
            status="active",
            capability_summary="test",
            rights_materials_ref="rights/test",
            submitted_at=datetime.now(timezone.utc),
        )
        self.assignment = ResourceAssignment(
            user_id=self.owner.id, resource_id=self.resource.id, capability="maintain"
        )
        self.db.add_all([self.admin, self.application, self.assignment])
        await self.db.commit()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def _change(
        self, status="suspended", expected="active", actor=None, self_exit=False
    ):
        return await creator.change_contributor_status(
            self.db,
            application_id=self.application.id,
            actor=actor or self.admin,
            expected_status=expected,
            status=status,
            reason="test reason",
            request_id="v002",
            self_exit=self_exit,
        )

    async def test_suspend_revokes_assignments_preserves_owner_and_emits_event(self):
        await self._change()
        await self.db.flush()
        await self.db.refresh(self.assignment)
        self.assertIsNotNone(self.assignment.revoked_at)
        self.assertEqual(self.owner.role, "user")
        self.assertEqual(self.resource.owner_id, self.owner.id)
        event = await self.db.scalar(
            select(DomainEvent).where(
                DomainEvent.event_type
                == DomainEventType.CONTRIBUTOR_APPLICATION_DECIDED
            )
        )
        self.assertEqual(event.payload["status"], "suspended")

    async def test_resume_does_not_restore_old_assignments(self):
        await self._change()
        await self._change("active", "suspended")
        await self.db.refresh(self.assignment)
        self.assertEqual(self.owner.role, "contributor")
        self.assertIsNotNone(self.assignment.revoked_at)

    async def test_stale_change_rejected(self):
        await self._change()
        with self.assertRaises(Conflict):
            await self._change()

    async def test_self_exit_and_no_self_resume(self):
        with self.assertRaises(Forbidden):
            await self._change(actor=self.owner, self_exit=True)
        await self._change("exited", actor=self.owner, self_exit=True)
        self.assertEqual(self.application.status, "exited")

    async def test_other_user_cannot_exit_or_manage(self):
        with self.assertRaises(NotFound):
            await self._change("exited", actor=self.other, self_exit=True)
        with self.assertRaises(Forbidden):
            await self._change(actor=self.other)

    async def test_suspended_cannot_bypass_with_new_application(self):
        from app.modules.creator.schemas import ContributorApplicationCreate

        await self._change()
        with patch.object(creator, "check_rate_limit", return_value=True):
            with self.assertRaises(Conflict):
                await creator.submit_application(
                    self.db,
                    user=self.owner,
                    request_id="v002",
                    payload=ContributorApplicationCreate(
                        capability_summary="test", rights_materials_ref="test"
                    ),
                )

    async def test_revoked_session_stops_stream_and_releases_connection(self):
        now = datetime.now(timezone.utc)
        login = Session(
            user_id=self.owner.id,
            token_digest="a" * 64,
            issued_at=now,
            expires_at=now + timedelta(hours=1),
        )
        self.db.add(login)
        await self.db.commit()
        stream.acquire_connection()
        gen = stream.notification_stream(
            self.factory,
            user_id=self.owner.id,
            session_id=login.id,
            heartbeat=0.01,
            max_seconds=1,
        )
        self.assertIn("event: unread", await anext(gen))
        login.revoked_at = now
        await self.db.commit()
        with self.assertRaises(StopAsyncIteration):
            await anext(gen)
        self.assertEqual(stream.active_connections(), 0)

    async def test_sse_disconnect_before_generator_starts_releases_exactly_once(self):
        from unittest.mock import AsyncMock
        from starlette.requests import ClientDisconnect
        from app.modules.notification import router

        with patch.object(router, "async_session_factory", self.factory):
            response = await router.notification_stream(user=self.owner)
        self.assertEqual(stream.active_connections(), 1)
        with self.assertRaises(ClientDisconnect):
            await response(
                {
                    "type": "http",
                    "asgi": {"spec_version": "2.4"},
                    "method": "GET",
                    "headers": [],
                },
                AsyncMock(return_value={"type": "http.disconnect"}),
                AsyncMock(side_effect=OSError("synthetic disconnect")),
            )
        self.assertEqual(stream.active_connections(), 0)
        await response.body_iterator.aclose()
        self.assertEqual(stream.active_connections(), 0)

    async def test_expired_session_and_inactive_account_cannot_read_stream(self):
        now = datetime.now(timezone.utc)
        login = Session(
            user_id=self.owner.id,
            token_digest="b" * 64,
            issued_at=now,
            expires_at=now - timedelta(seconds=1),
        )
        self.db.add(login)
        await self.db.commit()
        self.assertIsNone(
            await stream.read_unread(self.factory, self.owner.id, login.id)
        )

        login.expires_at = now + timedelta(hours=1)
        self.owner.status = "suspended"
        await self.db.commit()
        self.assertIsNone(
            await stream.read_unread(self.factory, self.owner.id, login.id)
        )

    async def test_delegated_reviewer_can_decide_only_assigned_work(self):
        from app.modules.assignment.models import AdminPermissionAssignment
        from app.modules.moderation.models import Review
        from app.modules.moderation import service as moderation

        self.db.add(
            AdminPermissionAssignment(
                user_id=self.other.id, permission="resource.review", scope="global"
            )
        )
        review = Review(
            resource_id=self.resource.id,
            version_id=self.version.id,
            submitter_id=self.owner.id,
            status="pending",
            snapshot_revision=1,
        )
        self.version.review_status = "pending"
        self.db.add(review)
        await self.db.flush()
        self.assertTrue(
            await moderation.can_review(self.db, self.other, self.version.id)
        )
        review.assigned_reviewer_id = self.admin.id
        await self.db.flush()
        with self.assertRaises(Forbidden):
            await moderation.decide(
                self.db,
                review_id=review.id,
                reviewer=self.other,
                decision=ModerationDecisionType.NEEDS_INFO,
                reason="more info",
                snapshot_revision=1,
                request_id="v002",
            )
        review.assigned_reviewer_id = self.other.id
        await self.db.flush()
        await moderation.decide(
            self.db,
            review_id=review.id,
            reviewer=self.other,
            decision=ModerationDecisionType.NEEDS_INFO,
            reason="more info",
            snapshot_revision=1,
            request_id="v002",
        )
        await self.db.refresh(self.version)
        self.assertEqual(self.version.review_status, "needs_info")

    async def test_approval_does_not_publish_and_publication_rechecks_scan_and_rights(
        self,
    ):
        from app.modules.content.models import (
            ResourceFile,
            RightsDeclaration,
            VersionFile,
        )
        from app.modules.content import service as content
        from app.modules.moderation import service as moderation
        from app.modules.moderation.models import Review

        self.version.review_status = "pending"
        review = Review(
            resource_id=self.resource.id,
            version_id=self.version.id,
            submitter_id=self.owner.id,
            status="pending",
            snapshot_revision=1,
        )
        self.db.add(review)
        await self.db.flush()
        await moderation.decide(
            self.db,
            review_id=review.id,
            reviewer=self.admin,
            decision=ModerationDecisionType.APPROVE,
            reason="reviewed",
            snapshot_revision=1,
            request_id="v002",
        )
        await self.db.refresh(self.resource)
        self.assertIsNone(self.resource.published_version_id)
        self.assertEqual(self.resource.publication_status, "unpublished")
        candidates = await moderation.list_publication_candidates(self.db)
        self.assertEqual(
            [row.version_id for row in candidates.items], [self.version.id]
        )
        with self.assertRaises(Conflict):
            await content.mark_published(
                self.db,
                resource_id=self.resource.id,
                version_id=self.version.id,
                expected_revision=self.resource.revision,
                actor_id=self.admin.id,
                request_id="v002",
            )
        file = ResourceFile(
            uploader_id=self.owner.id,
            storage_key="synthetic/test.pdf",
            original_name="test.pdf",
            detected_mime="application/pdf",
            sha256="a" * 64,
            scan_status="pending",
        )
        self.db.add(file)
        await self.db.flush()
        self.db.add(
            VersionFile(version_id=self.version.id, file_id=file.id, purpose="primary")
        )
        rights = RightsDeclaration(
            version_id=self.version.id,
            declarant_id=self.owner.id,
            rights_basis="unverified",
            declaration_text_version="v1",
        )
        self.db.add(rights)
        await self.db.flush()
        with self.assertRaises(Conflict) as error:
            await content.mark_published(
                self.db,
                resource_id=self.resource.id,
                version_id=self.version.id,
                expected_revision=self.resource.revision,
                actor_id=self.admin.id,
                request_id="v002",
            )
        self.assertEqual(error.exception.code, "FILE_NOT_READY")
        file.scan_status = "passed"
        await self.db.flush()
        with self.assertRaises(Conflict) as error:
            await content.mark_published(
                self.db,
                resource_id=self.resource.id,
                version_id=self.version.id,
                expected_revision=self.resource.revision,
                actor_id=self.admin.id,
                request_id="v002",
            )
        self.assertEqual(error.exception.code, "DISTRIBUTION_NOT_ALLOWED")
        rights.rights_basis = "original"
        await self.db.flush()
        await moderation.set_publication(
            self.db,
            resource_id=self.resource.id,
            version_id=self.version.id,
            action="publish",
            expected_revision=self.resource.revision,
            actor=self.admin,
            reason="rights checked",
            request_id="v002",
        )
        await self.db.refresh(self.resource)
        self.assertEqual(self.resource.published_version_id, self.version.id)
        self.assertEqual(
            (await moderation.list_publication_candidates(self.db)).items, []
        )

    async def test_audit_export_fails_closed_when_audit_write_fails(self):
        from app.core.errors import DependencyUnavailable
        from app.modules.ops import audit_export

        with patch.object(
            audit_export,
            "record_audit",
            side_effect=RuntimeError("synthetic audit failure"),
        ):
            with self.assertRaises(DependencyUnavailable):
                await audit_export.export_audit_csv(
                    self.db, actor_id=self.admin.id, request_id="v002"
                )

    async def test_legacy_scoped_permission_not_promoted_and_explicit_regrant_is_global(
        self,
    ):
        from pydantic import ValidationError
        from app.core.permissions import effective_permissions
        from app.modules.assignment.models import AdminPermissionAssignment
        from app.modules.assignment import service as assignment
        from app.modules.assignment.schemas import PermissionAssignmentCreate

        row = AdminPermissionAssignment(
            user_id=self.other.id,
            permission="metrics.view",
            scope="catalog",
            scope_catalog_id=self.course.id,
        )
        self.db.add(row)
        await self.db.flush()
        self.assertNotIn(
            "metrics.view", await effective_permissions(self.db, self.other)
        )
        self.assertEqual(
            (await assignment.my_assignments(self.db, user=self.other)).permissions, []
        )
        with self.assertRaises(ValidationError):
            PermissionAssignmentCreate(
                user_id=self.other.id,
                permission="metrics.view",
                reason="test",
                scope="catalog",
            )
        regranted = await assignment.create_permission_assignment(
            self.db,
            actor=self.admin,
            payload=PermissionAssignmentCreate(
                user_id=self.other.id,
                permission="metrics.view",
                reason="重新核对全站职责",
            ),
            request_id="v002",
        )
        self.assertEqual(regranted.id, row.id)
        self.assertEqual(regranted.scope, "global")
        self.assertIsNone(regranted.scope_catalog_id)
        self.assertIn("metrics.view", await effective_permissions(self.db, self.other))

    async def test_licensing_state_notification_reaches_other_party_without_terms(self):
        from app.modules.licensing.models import AgreementVersion, LicenseAgreement
        from app.modules.licensing.service import PLATFORM_PARTY_ID
        from app.modules.notification import service as notifications

        agreement = LicenseAgreement(
            platform_party_id=PLATFORM_PARTY_ID,
            contributor_party_id=self.owner.id,
            title="synthetic",
            state="active",
        )
        self.db.add(agreement)
        await self.db.flush()
        version = AgreementVersion(
            agreement_id=agreement.id,
            version_no=1,
            terms_hash="private-terms-hash",
            state="active",
        )
        self.db.add(version)
        await self.db.flush()
        event = DomainEvent(
            event_type="agreement.accepted",
            subject_type="agreement_version",
            subject_id=version.id,
            payload={"party_id": str(self.owner.id), "actor_id": str(self.owner.id)},
        )
        notices = await notifications._notify_licensing_event(self.db, event)
        self.assertEqual([notice.user_id for notice in notices], [self.admin.id])
        self.assertEqual(notices[0].type, "agreement_updated")
        self.assertNotIn("private-terms-hash", str(notices[0].data))


class EnumMigrationTests(unittest.TestCase):
    def test_frozen_migration_contains_current_event_notification_and_decision_values(
        self,
    ):
        path = (
            Path(__file__).resolve().parents[1]
            / "alembic/versions/0012_v002_enum_checks.py"
        )
        spec = importlib.util.spec_from_file_location("v002_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        enums = {
            "domain_events": DomainEventType,
            "notifications": NotificationType,
            "moderation_decisions": ModerationDecisionType,
        }
        for table, column, name, old, new in module.CHECKS:
            self.assertEqual(set(new), {member.value for member in enums[table]})
            self.assertTrue(set(old) <= set(new))

    def test_existing_sqlite_constraints_upgrade_without_losing_notifications(self):
        import uuid
        import sqlalchemy as sa
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from app.db import Base

        path = (
            Path(__file__).resolve().parents[1]
            / "alembic/versions/0012_v002_enum_checks.py"
        )
        spec = importlib.util.spec_from_file_location("v002_upgrade_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        metadata = sa.MetaData(naming_convention=Base.metadata.naming_convention)
        for table in Base.metadata.tables.values():
            table.to_metadata(metadata)
        for table_name, column, name, old, new in module.CHECKS:
            table = metadata.tables[table_name]
            table.constraints.remove(
                next(item for item in table.constraints if item.name == name)
            )
            table.append_constraint(
                sa.CheckConstraint(
                    column + " IN (" + ",".join(repr(value) for value in old) + ")",
                    name=sa.schema.conv(name),
                )
            )
        engine = sa.create_engine("sqlite:///:memory:")
        try:
            with engine.begin() as connection:
                metadata.create_all(connection)
                notifications = metadata.tables["notifications"]
                payload = dict(
                    user_id=uuid.uuid4(),
                    type="review_result",
                    title="old",
                    body="old",
                    data={},
                    is_read=False,
                )
                connection.execute(notifications.insert().values(**payload))
                with self.assertRaises(sa.exc.IntegrityError):
                    with connection.begin_nested():
                        connection.execute(
                            notifications.insert().values(
                                **{**payload, "type": "agreement_updated"}
                            )
                        )
                with Operations.context(MigrationContext.configure(connection)):
                    module.upgrade()
                connection.execute(
                    notifications.insert().values(
                        **{**payload, "type": "agreement_updated"}
                    )
                )
                self.assertEqual(
                    connection.scalar(
                        sa.select(sa.func.count()).select_from(notifications)
                    ),
                    2,
                )
                self.assertEqual(
                    connection.scalar(
                        sa.select(notifications.c.title).where(
                            notifications.c.type == "review_result"
                        )
                    ),
                    "old",
                )
        finally:
            engine.dispose()
