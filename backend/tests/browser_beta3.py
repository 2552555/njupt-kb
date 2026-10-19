"""Browser contract checks using explicit test API fixtures, not production data.

Start the frontend on 127.0.0.1:3100, then run this script with Playwright installed.
These checks complement backend tests; they do not exercise PostgreSQL/S3/Celery.
"""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect


RID = "00000000-0000-0000-0000-000000000001"
VID = "00000000-0000-0000-0000-000000000002"
FID = "00000000-0000-0000-0000-000000000003"
REVIEW = "00000000-0000-0000-0000-000000000004"
NOW = "2026-10-15T00:00:00Z"


def fixtures(context, reviewer=False):
    version = dict(
        id=VID,
        resource_id=RID,
        version_no=2,
        title="Beta3 测试新版",
        description="浏览器契约测试内容",
        change_summary="修正旧版内容",
        review_status="pending" if reviewer else "draft",
        preview_disabled=True,
        submitted_at=None,
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    seen = []

    def respond(route):
        request = route.request
        path = request.url.split("/api/v1", 1)[-1].split("?", 1)[0]
        method = request.method
        data = None
        if path == "/auth/csrf":
            data = {"csrf_token": "fixture-token"}
        elif path == "/me":
            data = dict(
                id="00000000-0000-0000-0000-000000000005",
                email="fixture@example.test",
                role="reviewer" if reviewer else "contributor",
                status="active",
                display_name="测试用户",
                avatar_url=None,
                email_verified=True,
                school_verified=False,
            )
        elif path == "/me/resources":
            data = {
                "items": [
                    dict(
                        id=RID,
                        course_id=RID,
                        publication_status="unpublished",
                        revision=1,
                        latest_version=version,
                        updated_at=NOW,
                    )
                ],
                "next_cursor": None,
                "has_more": False,
            }
        elif path in ["/me/corrections", "/me/requests"]:
            data = {"items": [], "next_cursor": None, "has_more": False}
        elif path == f"/resources/{RID}/versions":
            data = [version]
        elif path == f"/resources/{RID}/versions/{VID}" and method == "GET":
            data = dict(
                resource_id=RID,
                course_id=RID,
                resource_type="note",
                publication_status="unpublished",
                resource_revision=1,
                version=version,
                can_edit=not reviewer,
                tags=[],
                review_reason="请核对最新版",
                rights=dict(
                    rights_basis="original",
                    declaration_text_version="v1",
                    source="本人整理",
                    original_author="测试用户",
                    license=None,
                ),
                files=[
                    dict(
                        id=FID,
                        original_name="new-version.pdf",
                        detected_mime="application/pdf",
                        size=100,
                        sha256=None,
                        page_count=1,
                        scan_status="passed",
                        purpose="primary",
                        display_order=0,
                        created_at=NOW,
                    )
                ],
            )
        elif path == f"/resources/{RID}/versions/{VID}" and method == "PATCH":
            body = request.post_data_json
            assert body["revision"] == version["revision"]
            version.update(
                {key: body[key] for key in ["title", "description", "change_summary"]}
            )
            version["revision"] += 1
            seen.append("saved")
            data = version
        elif path == f"/resources/{RID}/versions/{VID}/submit":
            version["review_status"] = "pending"
            version["revision"] += 1
            seen.append("submitted")
            data = dict(
                version_id=VID,
                review_id=REVIEW,
                review_status="pending",
                submitted_at=NOW,
            )
        elif path == "/admin/reviews":
            data = {
                "items": []
                if "approved" in seen
                else [
                    dict(
                        id=REVIEW,
                        resource_id=RID,
                        version_id=VID,
                        submitter_id=RID,
                        status="pending",
                        snapshot_revision=7,
                        assigned_reviewer_id=None,
                        submitted_at=NOW,
                        decided_at=None,
                        created_at=NOW,
                        updated_at=NOW,
                        can_decide=True,
                    )
                ],
                "next_cursor": None,
                "has_more": False,
            }
        elif path == "/admin/moderation-summary":
            data = dict(
                reviews={"pending": 1},
                reports={"open": 0},
                oldest_pending_submitted_at=NOW,
            )
        elif path == f"/admin/reviews/{REVIEW}/decision":
            body = request.post_data_json
            assert body["snapshot_revision"] == 7 and body["decision"] == "approve"
            seen.append("approved")
            data = dict(
                id=REVIEW,
                review_id=REVIEW,
                version_id=VID,
                reviewer_id=RID,
                decision="approve",
                reason=body["reason"],
                snapshot_revision=7,
                created_at=NOW,
            )
        if data is None:
            route.fulfill(
                status=404,
                json={"error": {"code": "TEST_ROUTE_MISSING", "message": path}},
            )
        else:
            route.fulfill(json=data)

    context.route("**/api/v1/**", respond)
    return seen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser")
    parser.add_argument("--base-url", default="http://127.0.0.1:3100")
    parser.add_argument("--artifacts", required=True)
    args = parser.parse_args()
    artifacts = Path(args.artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, executable_path=args.browser
        )
        context = browser.new_context(viewport={"width": 1365, "height": 900})
        seen = fixtures(context)
        page = context.new_page()
        page.goto(args.base_url + "/me/resources")
        page.get_by_role("link", name="查看与管理").click()
        expect(page.get_by_label("标题", exact=True)).to_have_value("Beta3 测试新版")
        page.get_by_label("标题", exact=True).fill("Beta3 已保存的修订")
        expect(page.get_by_role("button", name="提交审核", exact=True)).to_be_enabled()
        page.get_by_role("button", name="保存草稿", exact=True).click()
        expect(
            page.get_by_role("heading", name="Beta3 已保存的修订 · V2")
        ).to_be_visible()
        page.get_by_role("button", name="提交审核", exact=True).click()
        expect(
            page.get_by_text("这个快照已冻结。需要修改时请创建新修订。")
        ).to_be_visible()
        page.screenshot(
            path=str(artifacts / "author-version-desktop.png"), full_page=True
        )
        assert seen == ["saved", "submitted"], seen
        context.close()

        context = browser.new_context(
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
        )
        seen = fixtures(context, reviewer=True)
        page = context.new_page()
        page.goto(args.base_url + "/admin/reviews")
        page.get_by_role("link", name="查看原件与预览").click()
        expect(page.get_by_label("标题", exact=True)).to_have_value("Beta3 测试新版")
        expect(page.get_by_label("标题", exact=True)).to_be_disabled()
        expect(page.get_by_text("new-version.pdf", exact=False)).to_be_visible()
        expect(page.get_by_role("button", name="创建修订")).to_have_count(0)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), "mobile overflow"
        page.screenshot(
            path=str(artifacts / "review-version-mobile.png"), full_page=True
        )
        page.goto(args.base_url + "/admin/reviews")
        page.get_by_role("button", name="审核", exact=True).click()
        page.get_by_label("审核理由（必填）").fill("已核对待审新版与来源")
        page.get_by_role("button", name="批准", exact=True).click()
        expect(page.get_by_text("当前没有待审版本")).to_be_visible()
        assert seen == ["approved"], seen
        browser.close()
    print(
        json.dumps(
            {
                "author_edit_submit": "PASS",
                "review_exact_version_mobile": "PASS",
                "mode": "browser with explicit API fixtures",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
