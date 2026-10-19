"""Comment governance contracts on isolated SQLite; not PostgreSQL concurrency proof."""

import unittest
from datetime import timedelta

from sqlalchemy import select

from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.modules.access.models import AccessPolicy
from app.modules.interaction import service
from app.modules.interaction.comment_management import manage_comment
from app.modules.interaction.models import Comment
from app.modules.moderation.models import AuditLog
import test_content_workflow as workflow


class Beta8Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.resource.publication_status = "published"
        self.resource.published_version_id = self.version.id
        self.version.review_status = "approved"
        self.policy = AccessPolicy(resource_id=self.resource.id, metadata_scope="public")
        self.db.add(self.policy)
        await self.db.flush()

    asyncTearDown = workflow.ContentWorkflowTests.asyncTearDown

    async def comment(self, parent_id=None, user=None):
        return await service.create_comment(self.db, user_id=(user or self.owner).id,
                                            resource_id=self.resource.id, body="不应泄露的原始正文", parent_id=parent_id)

    async def manage(self, comment, *, user=None, hidden=None, revision=None, reason="处理理由"):
        return await manage_comment(self.db, user=user or self.owner, resource_id=self.resource.id,
                                    comment_id=comment.id, expected_revision=revision or comment.revision,
                                    hidden=hidden, reason=reason, request_id="beta8-test")

    async def test_withdraw_retains_replies_and_redacts_all_responses(self):
        root = await self.comment()
        reply = await self.comment(root.id, self.other)
        (await self.db.get(Comment, reply.id)).created_at = root.created_at + timedelta(seconds=1)
        await self.db.flush()
        withdrawn = await self.manage(root)
        self.assertEqual(withdrawn.status, "withdrawn")
        self.assertIsNone(withdrawn.body)
        self.assertEqual((await self.db.get(Comment, root.id)).body, root.body)
        page = await service.list_comments(self.db, resource_id=self.resource.id, cursor=None, limit=1)
        self.assertEqual(page.items[0].id, root.id)
        self.assertTrue(page.has_more)
        self.assertIsNone(page.items[0].body)
        next_page = await service.list_comments(self.db, resource_id=self.resource.id,
                                                cursor=page.next_cursor, limit=1)
        self.assertEqual(next_page.items[0].id, reply.id)
        self.assertEqual(next_page.items[0].parent_id, root.id)
        await self.manage(root)  # 重试不增加审计或修订号。
        audits = list((await self.db.scalars(select(AuditLog).where(AuditLog.object_id == str(root.id)))).all())
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].action, "comment.withdrawn")
        self.assertNotIn(root.body, str(audits[0].before_state) + str(audits[0].after_state))
        with self.assertRaises(Conflict):
            await self.comment(root.id, self.other)

    async def test_hide_restore_stale_revision_and_author_withdrawal(self):
        root = await self.comment()
        self.other.role = "admin"
        hidden = await self.manage(root, user=self.other, hidden=True)
        self.assertEqual(hidden.status, "hidden")
        self.assertIsNone(hidden.body)
        self.assertEqual((await self.manage(root, user=self.other, hidden=True)).revision, 2)
        with self.assertRaises(Conflict):
            await self.manage(root, user=self.other, hidden=False)
        with self.assertRaises(Conflict):
            await self.comment(root.id)
        restored = await self.manage(hidden, user=self.other, hidden=False)
        self.assertEqual(restored.body, root.body)
        hidden_again = await self.manage(restored, user=self.other, hidden=True)
        withdrawn = await self.manage(hidden_again)
        self.assertEqual(withdrawn.status, "withdrawn")
        with self.assertRaises(Conflict):
            await self.manage(withdrawn, user=self.other, hidden=False)
        audits = list((await self.db.scalars(select(AuditLog).where(AuditLog.object_type == "comment"))).all())
        self.assertEqual(len(audits), 4)
        self.assertEqual(audits[0].reason, "处理理由")

    async def test_ownership_and_admin_permission_are_separate(self):
        root = await self.comment()
        with self.assertRaises(Forbidden):
            await self.manage(root, user=self.other)
        for role in ("contributor", "reviewer"):
            self.other.role = role
            with self.assertRaises(Forbidden):
                await self.manage(root, user=self.other, hidden=True)
        self.other.role = "admin"
        self.other.status = "suspended"
        with self.assertRaises(Forbidden):
            await self.manage(root, user=self.other, hidden=True)
        with self.assertRaises(Forbidden):
            await self.comment(user=self.other)
        self.other.status = "active"
        with self.assertRaises(InvalidInput):
            await self.manage(root, user=self.other, hidden=True, reason="   ")

    async def test_private_resource_comments_follow_metadata_permission(self):
        root = await self.comment()
        self.policy.metadata_scope = "private"
        await self.db.flush()
        for user in (None, self.other):
            with self.assertRaises(NotFound):
                await service.list_comments(self.db, resource_id=self.resource.id, cursor=None, limit=20, user=user)
        with self.assertRaises(NotFound):
            await self.comment(user=self.other)
        with self.assertRaises(NotFound):
            await self.manage(root, user=self.other)
        own = await service.list_comments(self.db, resource_id=self.resource.id, cursor=None, limit=20, user=self.owner)
        self.assertEqual(own.items[0].id, root.id)

    async def test_one_level_replies_and_hidden_child_only(self):
        root = await self.comment()
        reply = await self.comment(root.id, self.other)
        with self.assertRaises(InvalidInput):
            await self.comment(reply.id)
        self.other.role = "admin"
        await self.manage(reply, user=self.other, hidden=True)
        page = await service.list_comments(self.db, resource_id=self.resource.id, cursor=None, limit=20)
        items = {item.id: item for item in page.items}
        self.assertEqual(items[root.id].body, root.body)
        self.assertIsNone(items[reply.id].body)
        self.assertEqual(items[reply.id].status, "hidden")

    async def test_http_auth_input_and_response_contracts(self):
        import httpx
        from app.config import settings
        from app.core.deps import get_current_user
        from app.db import get_db
        from app.main import app

        root = await self.comment()

        async def database():
            yield self.db

        async def current_user():
            return self.other

        app.dependency_overrides[get_db] = database
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000",
                headers={"Origin": "http://localhost:3000", "X-CSRF-Token": "test"},
                cookies={settings.CSRF_COOKIE_NAME: "test"}) as client:
                url = f"/api/v1/resources/{self.resource.id}/comments/{root.id}"
                self.assertEqual((await client.post(url + "/withdraw", json={"expected_revision": 1})).status_code, 401)
                app.dependency_overrides[get_current_user] = current_user
                self.assertEqual((await client.post(url + "/withdraw", json={"expected_revision": 1})).status_code, 403)
                payload = {"expected_revision": 1, "hidden": True, "reason": "违规"}
                self.other.role = "reviewer"
                self.assertEqual((await client.post(url + "/moderation", json=payload)).status_code, 403)
                self.other.role = "admin"
                self.assertEqual((await client.post(url + "/moderation", json={**payload, "reason": " "})).status_code, 422)
                response = await client.post(url + "/moderation", json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIsNone(response.json()["body"])
                listed = await client.get(f"/api/v1/resources/{self.resource.id}/comments")
                self.assertIsNone(listed.json()["items"][0]["body"])
                self.assertNotIn(root.body, listed.text)
        finally:
            app.dependency_overrides.clear()
