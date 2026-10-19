"""0.0.3 在线课程攻略：编辑、版本提交与审核契约。

覆盖：
- 草稿只有作者可见，游客 404；
- 提交生成新版本并给作者通知；
- 审核通过后公开列表可见，正文取已发布版本；
- 发布后编辑不覆盖已公开正文，新版本待审时旧版仍公开；
- 驳回时旧公开版本保留；从未公开过的文章驳回后游客 404；
- revision 冲突、非作者编辑、重复审核均被拒绝；
- 撤回后从公开列表消失；
- 待审队列与审核接口要求 RESOURCE_REVIEW。
"""

import unittest
import uuid

from sqlalchemy import select

import test_content_workflow as workflow
from app.core.errors import Conflict, Forbidden, NotFound
from app.modules.content import guides_service
from app.modules.content.guides_models import GuideArticleStatus
from app.modules.identity.models import User
from app.modules.notification.models import Notification

BODY = (
    "这门课的重点在前六章，复习时先过一遍作业题，再对照往年试卷检查薄弱环节。"
    "实验部分建议提前一周准备环境，报告按模板写清楚步骤、结果与误差分析。"
    "考前两周把错题重新做一遍，遇到不确定的结论回到教材对应章节核对。"
)


class GuideArticleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await workflow.ContentWorkflowTests.asyncSetUp(self)
        self.admin = User(
            email="guide-admin@example.test",
            password_hash="unused",
            role="admin",
            status="active",
        )
        self.db.add(self.admin)
        await self.db.flush()

    async def asyncTearDown(self):
        await workflow.ContentWorkflowTests.asyncTearDown(self)

    async def _create(self, author=None):
        return await guides_service.create_article(
            self.db,
            author=author or self.owner,
            course_id=self.course.id,
            title="数据结构期末复习路线",
            summary="按章节梳理重点与练习顺序",
            body_markdown=BODY,
        )

    async def _publish(self, article):
        await guides_service.submit_article(self.db, actor=self.owner, article_id=article.id)
        await guides_service.review_article(
            self.db,
            reviewer=self.admin,
            article_id=article.id,
            version_no=article.latest_version_no,
            approve=True,
            note="内容合规",
        )

    async def test_draft_is_private_to_author(self):
        article = await self._create()
        mine = await guides_service.get_article(self.db, article_id=article.id, viewer=self.owner)
        self.assertEqual(mine["source"], "draft")
        with self.assertRaises(NotFound):
            await guides_service.get_article(self.db, article_id=article.id, viewer=self.other)
        with self.assertRaises(NotFound):
            await guides_service.get_article(self.db, article_id=article.id, viewer=None)
        page = await guides_service.list_articles(
            self.db, course_id=self.course.id, cursor=None, limit=10
        )
        self.assertEqual(page["items"], [])

    async def test_submit_creates_version_and_notifies_author(self):
        article = await self._create()
        version = await guides_service.submit_article(
            self.db, actor=self.owner, article_id=article.id
        )
        self.assertEqual(version.version_no, 1)
        self.assertEqual(version.status, GuideArticleStatus.PENDING.value)
        self.assertEqual(article.status, GuideArticleStatus.PENDING.value)
        notifications = (
            (
                await self.db.execute(
                    select(Notification).where(Notification.user_id == self.owner.id)
                )
            )
            .scalars()
            .all()
        )
        self.assertTrue(any("攻略" in item.title for item in notifications))

    async def test_approval_publishes_and_listing_uses_published_body(self):
        article = await self._create()
        await self._publish(article)
        self.assertEqual(article.status, GuideArticleStatus.PUBLISHED.value)
        self.assertEqual(article.published_version_no, 1)

        page = await guides_service.list_articles(
            self.db, course_id=self.course.id, cursor=None, limit=10
        )
        self.assertEqual([item.id for item in page["items"]], [article.id])

        public = await guides_service.get_article(
            self.db, article_id=article.id, viewer=None
        )
        self.assertEqual(public["source"], "published")
        self.assertIn("前六章", public["body_markdown"])

    async def test_edit_after_publish_keeps_old_body_until_new_version_approved(self):
        article = await self._create()
        await self._publish(article)

        await guides_service.update_article(
            self.db,
            actor=self.owner,
            article_id=article.id,
            expected_revision=article.revision,
            title=None,
            summary=None,
            body_markdown=(
                "补充：实验报告占 30%，需要提前准备环境并预留调试时间。"
                "报告要写清楚每一步的输入、输出与误差来源，便于助教复核。"
                "如果环境搭建失败，先检查依赖版本，再联系实验老师确认。"
            ),
        )
        await guides_service.submit_article(self.db, actor=self.owner, article_id=article.id)

        # 新版本待审期间，读者仍看到已发布的第 1 版。
        public = await guides_service.get_article(
            self.db, article_id=article.id, viewer=None
        )
        self.assertEqual(public["version_no"], 1)
        self.assertIn("前六章", public["body_markdown"])

        await guides_service.review_article(
            self.db,
            reviewer=self.admin,
            article_id=article.id,
            version_no=2,
            approve=True,
            note="补充有效",
        )
        public = await guides_service.get_article(
            self.db, article_id=article.id, viewer=None
        )
        self.assertEqual(public["version_no"], 2)
        self.assertIn("实验报告", public["body_markdown"])

    async def test_rejection_keeps_previous_published_version(self):
        article = await self._create()
        await self._publish(article)
        await guides_service.update_article(
            self.db,
            actor=self.owner,
            article_id=article.id,
            expected_revision=article.revision,
            title=None,
            summary=None,
            body_markdown=(
                "这一段是会被驳回的内容，长度足够通过校验，但不包含可核实的课程信息。"
                "它没有给出资料来源，也没有说明适用学期，因此不能作为公开攻略。"
            ),
        )
        await guides_service.submit_article(self.db, actor=self.owner, article_id=article.id)
        await guides_service.review_article(
            self.db,
            reviewer=self.admin,
            article_id=article.id,
            version_no=2,
            approve=False,
            note="缺少来源",
        )
        self.assertEqual(article.status, GuideArticleStatus.PUBLISHED.value)
        public = await guides_service.get_article(
            self.db, article_id=article.id, viewer=None
        )
        self.assertEqual(public["version_no"], 1)

    async def test_rejection_without_published_version_hides_article(self):
        article = await self._create()
        await guides_service.submit_article(self.db, actor=self.owner, article_id=article.id)
        await guides_service.review_article(
            self.db,
            reviewer=self.admin,
            article_id=article.id,
            version_no=1,
            approve=False,
            note="信息不足",
        )
        self.assertEqual(article.status, GuideArticleStatus.REJECTED.value)
        with self.assertRaises(NotFound):
            await guides_service.get_article(self.db, article_id=article.id, viewer=None)

    async def test_stale_revision_and_foreign_editor_are_rejected(self):
        article = await self._create()
        await guides_service.update_article(
            self.db,
            actor=self.owner,
            article_id=article.id,
            expected_revision=article.revision,
            title="新标题",
            summary=None,
            body_markdown=None,
        )
        with self.assertRaises(Conflict):
            await guides_service.update_article(
                self.db,
                actor=self.owner,
                article_id=article.id,
                expected_revision=1,
                title="旧版本写入",
                summary=None,
                body_markdown=None,
            )
        with self.assertRaises(Forbidden):
            await guides_service.update_article(
                self.db,
                actor=self.other,
                article_id=article.id,
                expected_revision=article.revision,
                title="越权编辑",
                summary=None,
                body_markdown=None,
            )

    async def test_double_review_is_conflict(self):
        article = await self._create()
        await guides_service.submit_article(self.db, actor=self.owner, article_id=article.id)
        await guides_service.review_article(
            self.db,
            reviewer=self.admin,
            article_id=article.id,
            version_no=1,
            approve=True,
            note=None,
        )
        with self.assertRaises(Conflict):
            await guides_service.review_article(
                self.db,
                reviewer=self.admin,
                article_id=article.id,
                version_no=1,
                approve=True,
                note=None,
            )

    async def test_withdraw_removes_from_public_list(self):
        article = await self._create()
        await self._publish(article)
        await guides_service.withdraw_article(
            self.db, actor=self.owner, article_id=article.id, reason="内容需要重写"
        )
        page = await guides_service.list_articles(
            self.db, course_id=self.course.id, cursor=None, limit=10
        )
        self.assertEqual(page["items"], [])
        with self.assertRaises(NotFound):
            await guides_service.get_article(self.db, article_id=article.id, viewer=None)

    async def test_pending_queue_lists_only_pending_versions(self):
        article = await self._create()
        await guides_service.submit_article(self.db, actor=self.owner, article_id=article.id)
        page = await guides_service.list_pending(self.db, limit=10)
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["article_id"], article.id)
        self.assertEqual(page["items"][0]["version_no"], 1)

    async def test_unknown_article_is_not_found(self):
        with self.assertRaises(NotFound):
            await guides_service.get_article(
                self.db, article_id=uuid.uuid4(), viewer=self.owner
            )


if __name__ == "__main__":
    unittest.main()
