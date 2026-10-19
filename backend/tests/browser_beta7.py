"""Beta7 browser contracts with explicit API fixtures; no production integrations.

Run built frontend on :3107 with API_INTERNAL_URL=http://127.0.0.1:8017.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright
from browser_beta4 import CID, RID, COURSE, RESULT, browser_fixtures, public_data, page_of

BASE = "http://127.0.0.1:3107"
ARTIFACTS = Path(__file__).resolve().parents[3] / ".review" / "beta7"


class FixtureAPI(BaseHTTPRequestHandler):
    empty = False
    failed = False

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.removeprefix("/api/v1")
        params = parse_qs(parsed.query)
        data = public_data(path)
        status = 200
        if path == "/recommendations":
            data = {"items": [dict(RESULT, reasons=["来自你关注的课程", "现行内容"])]}
            if self.failed:
                status, data = 503, {"error": {"code": "FIXTURE", "message": "推荐测试故障"}}
        elif path == "/resources" and params.get("resource_type") == ["guide"]:
            title = "第二页课程攻略" if params.get("cursor") else "数据结构学习攻略"
            data = page_of([dict(id=RID, title=title, resource_type="guide", freshness_status="current")])
            if not params.get("cursor"):
                data.update(has_more=True, next_cursor="fixture-next")
            if self.empty:
                data = page_of([])
            if self.failed:
                status, data = 503, {"error": {"code": "FIXTURE", "message": "攻略测试故障"}}
        self.send_response(status if data is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, *_args):
        pass


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 8017), FixtureAPI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 1365, "height": 900})
            seen = browser_fixtures(context)
            page = context.new_page()
            page.goto(BASE)
            expect(page.get_by_role("heading", name="值得看看", exact=True)).to_be_visible()
            expect(page.get_by_text("来自你关注的课程 · 现行内容", exact=True)).to_be_visible()
            checks.append("home recommendation and reason")
            page.goto(f"{BASE}/courses/{CID}")
            expect(page.get_by_role("heading", name="这门课值得看看")).to_be_visible()
            page.get_by_role("link", name="查看课程攻略", exact=True).click()
            expect(page.get_by_role("heading", name=f"{COURSE['name']} · 课程攻略")).to_be_visible()
            expect(page.get_by_role("link", name="数据结构学习攻略", exact=False)).to_be_visible()
            page.screenshot(path=str(ARTIFACTS / "guides-desktop.png"), full_page=True)
            page.get_by_role("link", name="下一页攻略").click()
            expect(page.get_by_role("link", name="第二页课程攻略", exact=False)).to_be_visible()
            page.get_by_role("link", name="返回第一页").click()
            expect(page.get_by_role("link", name="数据结构学习攻略", exact=False)).to_be_visible()
            checks.append("course guide entry, pagination and first page")
            page.get_by_role("link", name="分享课程攻略", exact=True).click()
            expect(page.get_by_label("资料类型")).to_have_value("guide")
            expect(page.get_by_text("数据结构（CS101）", exact=True)).to_be_visible()
            expect(page.get_by_text("请在攻略文件中写明", exact=False)).to_be_visible()
            page.locator('input[type="file"]').set_input_files(dict(name="guide.pdf", mimeType="application/pdf", buffer=b"%PDF-fixture"))
            page.get_by_role("button", name="保存草稿", exact=True).click()
            expect(page.get_by_text("草稿已保存", exact=False).first).to_be_visible()
            assert seen[0][0] == "save" and seen[0][1]["resource_type"] == "guide"
            assert seen[0][1]["course_id"] == CID
            checks.append("guide preselection and draft payload")
            for width in (390, 1365):
                page.set_viewport_size({"width": width, "height": 900})
                page.goto(f"{BASE}/courses/{CID}/guides")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(ARTIFACTS / f"guides-{width}.png"), full_page=True)
            checks.append("desktop and mobile without horizontal overflow")
            FixtureAPI.empty = True
            page.reload()
            expect(page.get_by_text("这门课还没有已发布的攻略", exact=False)).to_be_visible()
            FixtureAPI.empty = False
            FixtureAPI.failed = True
            page.reload()
            expect(page.get_by_text("攻略暂时无法加载", exact=False)).to_be_visible()
            page.goto(BASE)
            expect(page.get_by_text("推荐暂时无法加载", exact=False)).to_be_visible()
            expect(page.get_by_role("heading", name="近期内容", exact=True)).to_be_visible()
            checks.append("empty guide, API failure and independent content")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    (ARTIFACTS / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__":
    main()
