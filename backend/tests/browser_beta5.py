"""Beta5 browser contracts with explicit API/storage fixtures, no real uploads.

Run frontend on port 3105 with API_INTERNAL_URL=http://127.0.0.1:8014.
"""
import json
import threading
from pathlib import Path
from urllib.parse import urlparse
from http.server import ThreadingHTTPServer

from playwright.sync_api import expect, sync_playwright
from browser_beta4 import CID, RID, VID, FID, NOW, COURSE, RESOURCE, USER, FixtureAPI, page_of, public_data

BASE = "http://127.0.0.1:3105"
ARTIFACTS = Path(__file__).resolve().parents[3] / ".review" / "beta5"


def fixtures(context, *, role="admin"):
    state = {"scan": "pending", "uploads": 0, "drafts": [], "submits": 0, "fail_draft": True, "corrections": [], "fixed": False, "fail_classify": False}
    user = dict(USER, role=role, avatar_url=None, gender=None, bio=None, enrollment_year=None)

    def respond(route):
        request = route.request
        path = urlparse(request.url).path.removeprefix("/api/v1")
        method = request.method
        data = public_data(path) if method == "GET" else None
        status = 200
        if path == "/auth/csrf":
            data = {"csrf_token": "fixture"}
        elif path == "/me":
            if method == "PATCH":
                user.update(request.post_data_json)
            data = user
        elif path == "/admin/imports/classify":
            data = {"items": [dict(name=name, course_id=CID, resource_type="note", reason="课程名称", needs_confirmation=False, candidates=[dict(id=CID, name=COURSE["name"], code=COURSE["code"])], type_matches=["note"]) for name in request.post_data_json["names"]]}
            if state["fail_classify"]:
                status, data = 503, {"error": {"code": "TEST_FAILURE", "message": "分类服务暂不可用"}}
        elif path == "/uploads":
            state["uploads"] += 1
            data = dict(id=f"{FID}-{state['uploads']}", upload_url="/fixture-storage", content_type="application/pdf")
        elif path.startswith("/uploads/"):
            data = dict(file_id=FID, scan_status=state["scan"], status="received")
        elif path == "/admin/imports/drafts":
            state["drafts"].append((request.headers.get("idempotency-key"), request.post_data_json))
            if state["fail_draft"]:
                state["fail_draft"] = False
                status, data = 503, {"error": {"code": "TEST_FAILURE", "message": "测试暂时保存失败"}}
            else:
                data = dict(resource_id=RID, version_id=VID, revision=1)
        elif path.endswith("/submit"):
            state["submits"] += 1
            data = dict(version_id=VID, review_status="pending")
        elif path == f"/resources/{RID}":
            data = dict(RESOURCE, published_version_id=VID, revision=1)
        elif path.endswith("/ratings/summary"):
            data = dict(average=0, count=0)
        elif path == "/corrections":
            state["corrections"].append(request.post_data_json)
            data = dict(request.post_data_json, id=FID, status="open", created_at=NOW, updated_at=NOW)
        elif path == "/admin/corrections":
            data = page_of([dict(id=FID, resource_id=RID, version_id=VID, description="课程资料被误分为笔记", suggested_course_id=CID, suggested_resource_type="exam", status="open", created_at=NOW, updated_at=NOW)])
        elif path.endswith("/classification"):
            assert request.post_data_json["expected_revision"] == 1
            state["fixed"] = True
            data = dict(id=FID, status="fixed")
        elif path.startswith("/notifications"):
            data = page_of([])
        route.fulfill(status=status if data is not None else 404, content_type="application/json", body=json.dumps(data or {"error": {"code": "FIXTURE_MISSING", "message": path}}, ensure_ascii=False))

    context.route("**/api/v1/**", respond)
    context.route("**/fixture-storage", lambda route: route.fulfill(status=200, body=""))
    return state, user


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 8014), FixtureAPI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        state, user = fixtures(context)
        page = context.new_page()
        page.goto(BASE + "/admin/imports")
        page.get_by_label("批量选择文件").set_input_files([dict(name="数据结构笔记1.pdf", mimeType="application/pdf", buffer=b"%PDF-1.4 test"), dict(name="数据结构笔记2.pdf", mimeType="application/pdf", buffer=b"%PDF-1.4 test2")])
        expect(page.get_by_role("heading", name="1. 数据结构笔记1.pdf")).to_be_visible()
        expect(page.get_by_role("button", name="批量保存草稿 / 重试")).to_be_disabled()
        for section in page.locator("section").all():
            section.get_by_label("来源说明", exact=True).fill("测试资料来源")
            section.get_by_role("checkbox").check()
        page.get_by_role("button", name="批量保存草稿 / 重试").click()
        expect(page.get_by_text("测试暂时保存失败", exact=True)).to_be_visible()
        expect(page.get_by_text("草稿已保存", exact=True)).to_have_count(1)
        assert state["uploads"] == 2 and len(state["drafts"]) == 2
        page.get_by_role("button", name="批量提交审核 / 刷新安全检查").click()
        expect(page.get_by_text("草稿已保存 · 安全检查：pending，通过后可再次提交", exact=True)).to_have_count(2)
        assert state["uploads"] == 2 and len(state["drafts"]) == 3
        assert state["drafts"][0] == state["drafts"][2], "retry must reuse same payload and key"
        assert state["submits"] == 0
        state["scan"] = "passed"
        page.get_by_role("button", name="批量提交审核 / 刷新安全检查").click()
        expect(page.get_by_text("已提交审核", exact=True)).to_have_count(2)
        assert state["submits"] == 2 and len(state["drafts"]) == 3
        page.screenshot(path=str(ARTIFACTS / "bulk-desktop.png"), full_page=True)
        checks.append("bulk per-file confirmation, source, partial failure, stable retry, scan gate, submit")

        page.goto(BASE + "/me/profile")
        page.get_by_label("昵称", exact=True).fill("自定义昵称")
        page.get_by_label("性别", exact=True).select_option("female")
        page.get_by_label("简介", exact=True).fill("喜欢数据结构")
        page.get_by_label("入学年份", exact=True).fill("2025")
        page.get_by_role("button", name="保存资料").click()
        expect(page.get_by_text("个人资料已保存", exact=True)).to_be_visible()
        page.reload()
        expect(page.get_by_label("昵称", exact=True)).to_have_value("自定义昵称")
        expect(page.get_by_label("性别", exact=True)).to_have_value("female")
        page.screenshot(path=str(ARTIFACTS / "profile-desktop.png"), full_page=True)
        checks.append("profile save and reload")

        page.goto(BASE + f"/resources/{RID}")
        page.get_by_role("button", name="举报或纠错", exact=True).click()
        page.get_by_label("提交类型").select_option("classification")
        page.get_by_label("建议资料类型", exact=True).select_option("exam")
        page.get_by_label("纠错说明（必填）").fill("资料类型应为试卷")
        page.get_by_role("button", name="提交", exact=True).click()
        expect(page.get_by_text("纠错已提交，会进入独立工单处理", exact=True)).to_be_visible()
        assert state["corrections"][-1]["suggested_resource_type"] == "exam"
        checks.append("structured classification feedback")
        page.goto(BASE + "/admin/corrections")
        page.get_by_role("button", name="处理", exact=True).click()
        page.get_by_role("checkbox", name="采纳建议并修复分类", exact=False).check()
        page.get_by_label("处理说明", exact=False).fill("已核对并修复")
        page.get_by_role("button", name="提交处理").click()
        expect(page.get_by_text("纠错工单已处理", exact=True)).to_be_visible()
        assert state["fixed"]
        checks.append("admin applies suggested classification")
        context.close()

        context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        state, _user = fixtures(context)
        state["fail_classify"] = True
        page = context.new_page()
        page.goto(BASE + "/admin/imports")
        page.get_by_label("批量选择文件").set_input_files(dict(name="未知资料.pdf", mimeType="application/pdf", buffer=b"%PDF test"))
        expect(page.get_by_role("heading", name="1. 未知资料.pdf")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "bulk mobile overflow"
        page.screenshot(path=str(ARTIFACTS / "bulk-mobile.png"), full_page=True)
        checks.append("classification service failure permits manual choice; mobile bulk layout")
        page.goto(BASE + "/me/profile")
        expect(page.get_by_label("昵称", exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "profile mobile overflow"
        page.screenshot(path=str(ARTIFACTS / "profile-mobile.png"), full_page=True)
        context.close()
        context = browser.new_context()
        fixtures(context, role="reviewer")
        page = context.new_page()
        page.goto(BASE + "/admin/imports")
        expect(page.get_by_text("批量上传仅对管理员开放", exact=True)).to_be_visible()
        expect(page.get_by_label("批量选择文件")).to_have_count(0)
        checks.append("reviewer cannot use bulk upload")
        browser.close()
    server.shutdown()
    print(json.dumps({"mode": "explicit API and storage fixtures, no real services", "passed": checks}, ensure_ascii=False))


if __name__ == "__main__":
    main()
