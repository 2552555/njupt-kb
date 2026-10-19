"""Beta4 interaction checks with explicit API/storage fixtures, never real uploads.

Start the frontend with API_INTERNAL_URL=http://127.0.0.1:8014 on port 3104,
then run this script. --serve-only starts just the SSR fixture API.
"""

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright
from browser_beta3 import fixtures as version_fixtures


CID = "00000000-0000-0000-0000-000000000010"
RID = "00000000-0000-0000-0000-000000000011"
VID = "00000000-0000-0000-0000-000000000012"
FID = "00000000-0000-0000-0000-000000000013"
NOW = "2026-10-15T00:00:00Z"
COURSE = dict(id=CID, name="数据结构", code="CS101", college_name="计算机学院",
              college_id=None, aliases=[], curricula=[], description="课程详细说明", status="active")
RESULT = dict(resource_id=RID, title="数据结构复习笔记", resource_type="note",
              course=dict(course_id=CID, name=COURSE["name"], code="CS101"),
              freshness_status="current", rating=None)
FILE = dict(id=FID, original_name="notes.pdf", size=100, page_count=1,
            scan_status="passed", purpose="primary", display_order=0, created_at=NOW)
VERSION = dict(id=VID, version_no=1, title=RESULT["title"], description="笔记内容说明",
               change_summary=None, preview_disabled=False, revision=1)
RESOURCE = dict(id=RID, course_id=CID, resource_type="note", publication_status="published",
                freshness_status="current", updated_at=NOW, published_version=VERSION,
                preview_available=True)
USER = dict(id=CID, email="fixture@example.test", display_name="测试用户", role="contributor",
            status="active", email_verified=True, school_verified=False)


def page_of(items):
    return dict(items=items, has_more=False, next_cursor=None)


def public_data(path):
    if path == "/courses":
        return page_of([dict(COURSE, curriculum_count=0)])
    if path == f"/courses/{CID}":
        return COURSE
    if path.endswith("/offerings"):
        return []
    if path == "/search":
        return page_of([RESULT])
    if path == "/resources":
        return page_of([dict(id=RID, title=RESULT["title"], resource_type="note", freshness_status="current")])
    if path == f"/resources/{RID}":
        return RESOURCE
    if path.endswith("/files"):
        return dict(resource_id=RID, version_id=VID, items=[FILE])
    return None


class FixtureAPI(BaseHTTPRequestHandler):
    def do_GET(self):
        data = public_data(urlparse(self.path).path.removeprefix("/api/v1"))
        self.send_response(200 if data is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, *_args):
        pass


def browser_fixtures(context, *, guest=False, scan="passed", fail_save=False, fail_submit=False):
    seen = []
    failures = {"save": fail_save, "submit": fail_submit}

    def respond(route):
        request = route.request
        path = urlparse(request.url).path.removeprefix("/api/v1")
        method = request.method
        data = public_data(path) if method == "GET" else None
        status = 200
        if path == "/me":
            data = USER if not guest else {"error": {"code": "UNAUTHORIZED", "message": "未登录"}}
            status = 200 if not guest else 401
        elif path == "/auth/csrf":
            data = {"csrf_token": "fixture-token"}
        elif path == "/uploads" and method == "POST":
            data = dict(id=FID, upload_url="/fixture-storage", content_type="application/pdf")
        elif path.startswith("/uploads/"):
            data = dict(file_id=FID, scan_status=scan, status="completed")
        elif path == "/resources" and method == "POST":
            seen.append(("save", request.post_data_json))
            if failures["save"]:
                failures["save"] = False
                status, data = 503, {"error": {"code": "TEST_FAILURE", "message": "测试保存失败"}}
            else:
                data = dict(resource_id=RID, version_id=VID, revision=1)
        elif method == "PATCH" and path.endswith(VID):
            seen.append(("update", request.post_data_json))
            data = dict(VERSION, revision=2)
        elif path.endswith("/submit"):
            seen.append(("submit", None))
            if failures["submit"]:
                failures["submit"] = False
                status, data = 503, {"error": {"code": "TEST_FAILURE", "message": "测试提交失败"}}
            else:
                data = dict(version_id=VID, review_status="pending")
        elif path.endswith("/preview-grants"):
            seen.append(("preview", request.post_data_json))
            data = dict(preview_url="/fixture-preview", url_expires_at=NOW)
        elif path.endswith("/download-grants"):
            seen.append(("download", request.post_data_json))
            data = dict(download_url="/fixture-download", url_expires_at=NOW)
        elif path.endswith("/ratings/summary"):
            data = dict(average=0, count=0)
        elif path == "/requests" and method == "POST":
            seen.append(("request", request.post_data_json))
            data = dict(id=RID, created=True)
        elif path.startswith("/requests") or path.startswith("/me/"):
            data = page_of([])
        route.fulfill(status=status if data is not None else 404, json=data)

    context.route("**/api/v1/**", respond)
    context.route("**/fixture-storage", lambda route: route.fulfill(status=200, body=""))
    context.route("**/fixture-preview", lambda route: route.fulfill(content_type="text/html", body="<p>测试预览</p>"))
    context.route("**/fixture-download", lambda route: route.fulfill(
        content_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="fixture.pdf"'}, body="%PDF-test"))
    return seen


def upload(page, base):
    page.goto(f"{base}/upload?course_id={CID}")
    expect(page.get_by_text("数据结构（CS101）", exact=True)).to_be_visible()
    page.locator('input[type="file"]').set_input_files(dict(name="notes.pdf", mimeType="application/pdf", buffer=b"%PDF-fixture"))
    expect(page.get_by_label("资料标题", exact=False)).to_have_value("notes")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3104")
    parser.add_argument("--artifacts", default=".review/beta4")
    parser.add_argument("--serve-only", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 8014), FixtureAPI)
    if args.serve_only:
        server.serve_forever()
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    artifacts = Path(args.artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    checks = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1365, "height": 900})
        seen = browser_fixtures(context)
        page = context.new_page()
        page.goto(args.base_url)
        page.screenshot(path=str(artifacts / "home-desktop.png"), full_page=True)
        page.get_by_label("搜索课程或资料").fill("数据结构")
        page.get_by_role("button", name="搜索", exact=True).click()
        page.get_by_role("link", name="数据结构复习笔记", exact=False).click()
        page.get_by_role("button", name="查看预览", exact=True).click()
        expect(page.get_by_role("dialog")).to_be_visible()
        assert [event[0] for event in seen] == ["preview"]
        checks.append("search-detail-preview: 3 actions")
        page.goto(f"{args.base_url}/resources/{RID}")
        with page.expect_download():
            page.get_by_role("button", name="下载原件").click()
        assert seen[-1][0] == "download"
        checks.append("authorized download")
        page.goto(f"{args.base_url}/search")
        expect(page.get_by_role("link", name="数据结构复习笔记", exact=False)).to_be_visible()
        checks.append("browse without keyword")
        page.goto(f"{args.base_url}/search?q=数据结构")
        page.get_by_label("关键词").fill("线性代数")
        page.get_by_label("资料类型").select_option("exam")
        expect(page).to_have_url(re.compile("resource_type=exam"))
        expect(page.get_by_label("关键词")).to_have_value("线性代数")
        page.go_back()
        expect(page.get_by_label("资料类型")).to_have_value("")
        expect(page.get_by_label("关键词")).to_have_value("数据结构")
        checks.append("filter-back state")
        upload(page, args.base_url)
        submit = page.get_by_role("button", name="提交审核", exact=True)
        expect(submit).to_be_enabled()
        page.screenshot(path=str(artifacts / "upload-desktop.png"), full_page=True)
        submit.click()
        expect(page.get_by_text("审核通过后资料才会公开。你可以在我的资料中查看进度与审核意见。")).to_be_visible()
        assert [event[0] for event in seen][-2:] == ["save", "submit"]
        checks.append("upload auto-save-submit")
        context.close()

        for failure in ["save", "submit"]:
            context = browser.new_context()
            seen = browser_fixtures(context, **{f"fail_{failure}": True})
            page = context.new_page()
            upload(page, args.base_url)
            submit = page.get_by_role("button", name="提交审核", exact=True)
            expect(submit).to_be_enabled()
            submit.click()
            expect(page.get_by_text(f"测试{'保存' if failure == 'save' else '提交'}失败", exact=True)).to_be_visible()
            if failure == "save":
                assert not any(event[0] == "submit" for event in seen)
            else:
                page.get_by_label("资料标题", exact=False).fill("修改后的笔记")
            submit.click()
            expect(page.get_by_text("审核通过后资料才会公开。你可以在我的资料中查看进度与审核意见。")).to_be_visible()
            assert [event[0] for event in seen] == (["save", "save", "submit"] if failure == "save" else ["save", "submit", "update", "submit"])
            context.close()
            checks.append(f"{failure} failure retry")

        context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        seen = browser_fixtures(context, scan="pending")
        page = context.new_page()
        upload(page, args.base_url)
        expect(page.get_by_role("button", name="提交审核", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="保存草稿", exact=True)).to_be_enabled()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "upload mobile overflow"
        page.screenshot(path=str(artifacts / "upload-mobile.png"), full_page=True)
        checks.append("pending scan cannot submit")
        page.goto(f"{args.base_url}/courses/{CID}")
        expect(page.get_by_role("heading", name="分类资料")).to_be_visible()
        assert page.locator("details").get_attribute("open") is None
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "mobile overflow"
        page.screenshot(path=str(artifacts / "course-mobile.png"), full_page=True)
        page.get_by_role("link", name="为这门课求资料").click()
        expect(page.get_by_label("课程", exact=True)).to_have_value("数据结构（CS101）")
        page.get_by_label("需求描述").fill("需要期末试卷及答案")
        page.get_by_role("button", name="发布需求", exact=True).click()
        expect(page.get_by_text("需求已发布", exact=True)).to_be_visible()
        assert seen[-1][1]["course_id"] == CID
        checks.append("course-request context and mobile layout")
        context.close()

        context = browser.new_context()
        seen = version_fixtures(context)
        page = context.new_page()
        page.goto(args.base_url + "/me/resources")
        page.get_by_role("link", name="查看与管理").click()
        page.get_by_label("标题", exact=True).fill("Beta4 直接提交修订")
        page.get_by_role("button", name="提交审核", exact=True).click()
        expect(page.get_by_text("这个快照已冻结。需要修改时请创建新修订。")).to_be_visible()
        assert seen == ["saved", "submitted"]
        checks.append("existing draft auto-save-submit")
        context.close()

        context = browser.new_context()
        browser_fixtures(context, guest=True)
        page = context.new_page()
        page.goto(f"{args.base_url}/upload?course_id={CID}")
        expect(page).to_have_url(re.compile("/login\\?next="))
        assert parse_qs(urlparse(page.url).query)["next"] == [f"/upload?course_id={CID}"]
        page.goto(f"{args.base_url}/requests?course_id={CID}&new=1")
        expect(page.get_by_text("已带入课程：数据结构。")).to_be_visible()
        page.get_by_role("link", name="登录并继续").click()
        expect(page).to_have_url(re.compile("/login\\?next="))
        assert parse_qs(urlparse(page.url).query)["next"] == [f"/requests?course_id={CID}&new=1"]
        page.goto(f"{args.base_url}/resources/{RID}")
        expect(page.get_by_role("link", name="登录后下载")).to_have_attribute("href", re.compile("next="))
        checks.append("guest login retains target")
        browser.close()
    server.shutdown()
    print(json.dumps(dict(mode="explicit fixtures; no PostgreSQL/S3/Celery", passed=checks), ensure_ascii=False))


if __name__ == "__main__":
    main()
