"""Beta8 browser contracts with explicit fixtures, not production integrations.

Use built frontend :3108 with API_INTERNAL_URL=http://127.0.0.1:8018.
"""

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright
from browser_beta4 import CID, RID, NOW, USER, FixtureAPI, page_of, public_data

BASE = "http://127.0.0.1:3108"
ARTIFACTS = Path(__file__).resolve().parents[3] / ".review" / "beta8"
ROOT = "00000000-0000-0000-0000-000000000040"
CHILD = "00000000-0000-0000-0000-000000000041"
OTHER = "00000000-0000-0000-0000-000000000030"
BODY = "<b>课程学习的原始评论</b>"


def fixtures(context, *, role="admin", author=False):
    user = dict(USER, id=CID if author else OTHER, role=role)
    root = dict(id=ROOT, resource_id=RID, user_id=CID, display_name="评论作者",
                parent_id=None, body=BODY, created_at=NOW, status="visible", revision=1)
    child = dict(root, id=CHILD, user_id=OTHER, display_name="回复者", parent_id=ROOT,
                 body="已有的正常回复", status="visible", revision=1)
    state = {"fail": False, "conflict": False, "actions": []}

    def respond(route):
        request = route.request
        path = urlparse(request.url).path.removeprefix("/api/v1")
        data = public_data(path) if request.method == "GET" else None
        status = 200
        if path == "/me":
            data = user
        elif path == "/auth/csrf":
            data = {"csrf_token": "fixture"}
        elif path.endswith("/ratings/summary"):
            data = {"average": 0, "count": 0}
        elif path == f"/resources/{RID}/comments":
            data = page_of([root, child])
        elif path.endswith("/withdraw") or path.endswith("/moderation"):
            payload = request.post_data_json
            state["actions"].append(payload)
            if state["fail"]:
                state["fail"] = False
                status, data = 503, {"error": {"code": "FIXTURE", "message": "测试处理失败，请重试"}}
            elif state["conflict"]:
                state["conflict"] = False
                root["revision"] += 1
                status, data = 409, {"error": {"code": "STALE_REVISION", "message": "评论状态已变化，请刷新后重试"}}
            else:
                assert payload["expected_revision"] == root["revision"]
                if path.endswith("/withdraw"):
                    root.update(status="withdrawn", body=None)
                else:
                    assert payload["reason"].strip()
                    root.update(status="hidden" if payload["hidden"] else "visible", body=None if payload["hidden"] else BODY)
                root["revision"] += 1
                data = root
        elif path.startswith("/me/"):
            data = page_of([])
        route.fulfill(status=status if data is not None else 404, json=data)

    context.route("**/api/v1/**", respond)
    return state, root


def discussion(page):
    page.goto(f"{BASE}/resources/{RID}")
    page.get_by_role("heading", name="讨论", exact=True).scroll_into_view_if_needed()
    expect(page.get_by_text(BODY, exact=True)).to_be_visible()
    return page.locator("li").filter(has=page.get_by_role("link", name="评论作者", exact=True))


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 8018), FixtureAPI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 1365, "height": 900})
            state, _ = fixtures(context)
            page = context.new_page()
            root = discussion(page)
            expect(root.locator("b")).to_have_count(0)
            root.get_by_role("button", name="隐藏评论").click()
            dialog = page.get_by_role("dialog")
            expect(dialog.get_by_role("button", name="确认处理")).to_be_disabled()
            dialog.get_by_label("处理理由").press_sequentially("test reason", delay=30)
            expect(dialog.get_by_label("处理理由")).to_have_value("test reason")
            expect(dialog.get_by_label("处理理由")).to_be_focused()
            state["fail"] = True
            dialog.get_by_role("button", name="确认处理").click()
            expect(page.get_by_text("测试处理失败，请重试", exact=True)).to_be_visible()
            expect(dialog.get_by_label("处理理由")).to_have_value("test reason")
            dialog.get_by_role("button", name="确认处理").click()
            expect(page.get_by_text("该评论已由管理员隐藏", exact=True)).to_be_visible()
            expect(page.get_by_text(BODY, exact=True)).to_have_count(0)
            expect(page.get_by_text("已有的正常回复", exact=True)).to_be_visible()
            expect(root.get_by_role("button", name="回复", exact=True)).to_have_count(0)
            page.get_by_role("heading", name="讨论", exact=True).scroll_into_view_if_needed()
            page.screenshot(path=str(ARTIFACTS / "hidden-desktop.png"))
            checks.append("admin hide, required reason, failure retry, body redaction and reply preservation")
            root.get_by_role("button", name="恢复评论").click()
            page.get_by_label("处理理由").fill("测试：复核后恢复")
            state["conflict"] = True
            page.get_by_role("dialog").get_by_role("button", name="确认处理").click()
            expect(page.get_by_role("dialog")).to_have_count(0)
            expect(page.get_by_text("评论状态已变化，请刷新后重试", exact=True)).to_be_visible()
            root.get_by_role("button", name="恢复评论").click()
            page.get_by_label("处理理由").fill("测试：刷新后恢复")
            page.get_by_role("dialog").get_by_role("button", name="确认处理").click()
            expect(page.get_by_text(BODY, exact=True)).to_be_visible()
            expect(root.get_by_role("button", name="回复", exact=True)).to_be_visible()
            checks.append("continuous typing keeps focus; conflict closes stale dialog; restore uses refreshed revision")
            author_context = browser.new_context(viewport={"width": 390, "height": 844})
            _, _ = fixtures(author_context, role="contributor", author=True)
            author_page = author_context.new_page()
            own = discussion(author_page)
            expect(own.get_by_role("button", name="隐藏评论")).to_have_count(0)
            own.get_by_role("button", name="撤回评论").click()
            author_page.get_by_role("dialog").get_by_role("button", name="确认处理").click()
            expect(author_page.get_by_text("该评论已由作者撤回", exact=True)).to_be_visible()
            expect(author_page.get_by_text(BODY, exact=True)).to_have_count(0)
            expect(author_page.get_by_text("已有的正常回复", exact=True)).to_be_visible()
            expect(own.get_by_role("button", name="恢复评论")).to_have_count(0)
            assert author_page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            expect(author_page.get_by_text("评论状态已更新", exact=True)).not_to_be_visible(timeout=6000)
            author_page.get_by_role("heading", name="讨论", exact=True).scroll_into_view_if_needed()
            author_page.screenshot(path=str(ARTIFACTS / "withdrawn-mobile.png"))
            author_page.reload()
            expect(author_page.get_by_text("该评论已由作者撤回", exact=True)).to_be_visible()
            checks.append("author withdrawal, reload, permission controls and mobile layout")
            reviewer_context = browser.new_context()
            fixtures(reviewer_context, role="reviewer")
            reviewer_page = reviewer_context.new_page()
            reviewed = discussion(reviewer_page)
            expect(reviewed.get_by_role("button", name="隐藏评论")).to_have_count(0)
            expect(reviewed.get_by_role("button", name="撤回评论")).to_have_count(0)
            checks.append("reviewer cannot moderate or withdraw other authors")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    (ARTIFACTS / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__":
    main()
