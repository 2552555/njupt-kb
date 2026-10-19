"""Expanded P2 browser fixtures; frontend :3109 / SSR fixture :8019."""

import json
import threading
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright, expect
from browser_beta4 import CID, RID, FID, NOW, USER, FixtureAPI, public_data, page_of

BASE = "http://127.0.0.1:3109"
QID = "00000000-0000-0000-0000-000000000050"
ART = Path(__file__).resolve().parents[3] / ".review" / "beta9"


def fixtures(context, role="contributor", state=None):
    if state is None:
        state = {
            "request": dict(
                id=QID,
                course_id=CID,
                requester_id=CID,
                revision=1,
                description="需要数据结构复习资料",
                scope=None,
                status="open",
                follower_count=2,
                resolved_resource_id=None,
                resolved_resource_title=None,
                resolution_available=False,
                handled_at=None,
                created_at=NOW,
            ),
            "responses": [],
            "conflict": False,
            "unavailable": False,
            "retried": False,
            "notify_read": False,
            "more_read": False,
            "fail_retry": False,
        }

    def respond(route):
        req = route.request
        parsed = urlparse(req.url)
        path = parsed.path.removeprefix("/api/v1")
        params = parse_qs(parsed.query)
        method = req.method
        data = public_data(path) if method == "GET" else None
        status = 200
        row = state["request"]
        if path == "/me":
            data = dict(USER, role=role)
        elif path == "/auth/csrf":
            data = {"csrf_token": "fixture"}
        elif path == "/requests":
            data = page_of(
                [
                    dict(
                        row,
                        id="00000000-0000-0000-0000-000000000051",
                        description="第二页需求",
                    )
                ]
                if params.get("cursor")
                else [row]
            )
            if not params.get("cursor"):
                data.update(has_more=True, next_cursor="next")
        elif path == f"/requests/{QID}":
            data = dict(row)
            if state["unavailable"]:
                data.update(
                    resolution_available=False,
                    resolved_resource_id=None,
                    resolved_resource_title=None,
                )
        elif path == f"/requests/{QID}/resources":
            data = page_of([dict(id=RID, title="已发布的复习笔记")])
        elif path == f"/requests/{QID}/responses":
            if method == "POST":
                assert req.post_data_json["resource_id"] == RID
                state["responses"] = [
                    dict(
                        id=FID,
                        request_id=QID,
                        submitted_by=CID,
                        resource_id=RID,
                        resource_title="已发布的复习笔记",
                        available=True,
                        created_at=NOW,
                    )
                ]
                data = state["responses"][0]
            else:
                data = page_of(state["responses"])
        elif path == f"/requests/{QID}/status":
            body = req.post_data_json
            if state["conflict"]:
                state["conflict"] = False
                row["revision"] += 1
                status, data = (
                    409,
                    {
                        "error": {
                            "code": "STALE_REVISION",
                            "message": "需求已变化，请刷新后重试",
                        }
                    },
                )
            else:
                assert body["expected_revision"] == row["revision"]
                row.update(status=body["status"], revision=row["revision"] + 1)
                row.update(
                    resolved_resource_id=body.get("resource_id"),
                    resolution_available=body["status"] == "fulfilled",
                    resolved_resource_title="已发布的复习笔记"
                    if body["status"] == "fulfilled"
                    else None,
                )
                data = row
        elif path == "/notifications/read-all":
            state["notify_read"] = state["more_read"] = True
            data = {"updated": 2}
        elif path.startswith("/notifications/") and method == "PATCH":
            state["notify_read"] = True
            data = {}
        elif path == "/notifications":
            more = bool(params.get("cursor"))
            data = page_of(
                [
                    dict(
                        id=FID if not more else RID,
                        type="request_updated",
                        title="第二页通知" if more else "资料需求有更新",
                        body="资料需求已确认解决。",
                        is_read=state["more_read"] if more else state["notify_read"],
                        read_at=None,
                        created_at=NOW,
                        data={"request_id": QID},
                    )
                ]
            )
            if not more:
                data.update(has_more=True, next_cursor="next")
        elif path == "/me/resources":
            data = page_of([])
        elif path == "/me/corrections":
            data = page_of(
                [
                    dict(
                        id=FID,
                        resource_id=RID,
                        description="纠错记录",
                        status="open",
                        created_at=NOW,
                    )
                ]
            )
        elif path == "/me/requests":
            data = page_of(
                [
                    dict(
                        row,
                        description="第二页本人需求"
                        if params.get("cursor")
                        else "本人需求",
                    )
                ]
            )
            if not params.get("cursor"):
                data.update(has_more=True, next_cursor="next")
        elif path == "/admin/comments":
            data = page_of(
                [
                    dict(
                        id=FID,
                        resource_id=RID,
                        user_id=CID,
                        display_name="评论作者",
                        parent_id=None,
                        body=None,
                        status="hidden",
                        revision=2,
                        created_at=NOW,
                    )
                ]
            )
        elif path == "/admin/events":
            data = page_of(
                []
                if state["retried"]
                else [
                    dict(
                        id=FID,
                        event_type="request.updated",
                        status="failed",
                        attempts=1,
                        created_at=NOW,
                    )
                ]
            )
        elif path.endswith("/retry"):
            assert req.post_data_json["expected_attempts"] == 1
            if state["fail_retry"]:
                state["fail_retry"] = False
                status, data = (
                    503,
                    {"error": {"code": "FIXTURE", "message": "测试排队失败"}},
                )
            else:
                state["retried"] = True
                data = {"id": FID, "status": "pending"}
        elif path == "/me/follows":
            data = page_of(
                [
                    dict(
                        id=FID,
                        target_type="course",
                        target_id=CID,
                        target={
                            "name": "第二页关注" if params.get("cursor") else "数据结构"
                        },
                        created_at=NOW,
                    )
                ]
            )
            if not params.get("cursor"):
                data.update(has_more=True, next_cursor="next")
        route.fulfill(status=status if data is not None else 404, json=data)

    context.route("**/api/v1/**", respond)
    return state


def main():
    ART.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 8019), FixtureAPI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    checks = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1365, "height": 900})
            state = fixtures(ctx)
            page = ctx.new_page()
            page.goto(BASE + "/requests?course_id=" + CID)
            page.get_by_role("button", name="更多需求", exact=True).click()
            expect(page.get_by_text("第二页需求", exact=True)).to_be_visible()
            page.get_by_role("link", name="查看与处理需求", exact=True).first.click()
            expect(
                page.get_by_role("heading", name="资料需求", exact=True)
            ).to_be_visible()
            page.get_by_role("link", name="上传资料回应", exact=True).click()
            expect(page.get_by_text("正在回应资料需求", exact=False)).to_be_visible()
            page.get_by_role("link", name="返回需求提交候选", exact=True).click()
            page.get_by_label("选择资料", exact=False).select_option(RID)
            page.get_by_role("button", name="提交候选", exact=True).click()
            expect(
                page.get_by_role("button", name="确认解决", exact=True)
            ).to_be_visible()
            state["conflict"] = True
            page.get_by_role("button", name="确认解决", exact=True).click()
            page.get_by_role("dialog").get_by_role("button", name="确认处理").click()
            expect(page.get_by_role("dialog")).to_have_count(0)
            page.get_by_role("button", name="确认解决", exact=True).click()
            page.get_by_role("dialog").get_by_role("button", name="确认处理").click()
            expect(page.get_by_text("需求已确认解决", exact=False)).to_be_visible()
            page.screenshot(path=str(ART / "request-desktop.png"))
            checks.append(
                "request list pagination, upload context, candidate, conflict refresh and fulfillment"
            )
            state["unavailable"] = True
            page.reload()
            expect(
                page.get_by_text("原解决资料当前不可访问", exact=False)
            ).to_be_visible()
            state["unavailable"] = False
            page.get_by_role("button", name="重新打开", exact=True).click()
            reason = page.get_by_role("dialog").get_by_label("处理理由", exact=False)
            reason.press_sequentially("reopen reason", delay=20)
            expect(reason).to_be_focused()
            page.get_by_role("dialog").get_by_role("button", name="确认处理").click()
            page.get_by_role("button", name="关闭需求", exact=True).click()
            page.get_by_role("dialog").get_by_label("处理理由", exact=False).fill(
                "已不再需要"
            )
            page.get_by_role("dialog").get_by_role("button", name="确认处理").click()
            expect(
                page.get_by_role("button", name="重新打开", exact=True)
            ).to_have_count(0)
            checks.append(
                "unavailable resolution, reopen and author cannot reopen closed request"
            )
            admin = browser.new_context(viewport={"width": 390, "height": 844})
            fixtures(admin, role="admin", state=state)
            p = admin.new_page()
            p.goto(BASE + "/requests/" + QID)
            p.get_by_role("button", name="重新打开", exact=True).click()
            p.get_by_role("dialog").get_by_label("处理理由", exact=False).fill(
                "管理员复核恢复"
            )
            p.get_by_role("dialog").get_by_role("button", name="确认处理").click()
            expect(p.get_by_role("button", name="关闭需求", exact=True)).to_be_visible()
            assert p.evaluate("document.documentElement.scrollWidth<=innerWidth")
            p.screenshot(path=str(ART / "request-mobile.png"))
            p.goto(BASE + "/me/notifications")
            expect(p.get_by_text("资料需求有更新 · 未读", exact=True)).to_be_visible()
            p.get_by_role("button", name="标为已读", exact=True).click()
            expect(p.get_by_text("资料需求有更新 · 已读", exact=True)).to_be_visible()
            p.get_by_role("button", name="更多通知").click()
            expect(p.get_by_text("第二页通知 · 未读", exact=True)).to_be_visible()
            p.get_by_role("button", name="全部标为已读").click()
            expect(p.get_by_text("第二页通知 · 已读", exact=True)).to_be_visible()
            p.get_by_role("link", name="查看详情").first.click()
            expect(
                p.get_by_role("heading", name="资料需求", exact=True)
            ).to_be_visible()
            checks.append(
                "admin reopening, mobile layout, notification read state, pagination, read all and deep link"
            )
            p.goto(BASE + "/admin/comments")
            expect(p.get_by_text("正文已遮蔽", exact=True)).to_be_visible()
            expect(p.get_by_role("link", name="查看讨论并处理")).to_be_visible()
            p.goto(BASE + "/admin/events")
            state["fail_retry"] = True
            p.get_by_role("button", name="重新排队").click()
            expect(p.get_by_text("测试排队失败", exact=True)).to_be_visible()
            p.get_by_role("button", name="重新排队").click()
            expect(p.get_by_text("没有失败事件", exact=False)).to_be_visible()
            p.goto(BASE + "/me/follows")
            p.get_by_role("button", name="更多关注").click()
            expect(p.get_by_text("第二页关注", exact=True)).to_be_visible()
            checks.append(
                "comment redaction queue, event retry failure/recovery and follow pagination"
            )
            p.goto(BASE + "/me/resources")
            expect(
                p.get_by_role("link", name="查看资料", exact=True)
            ).to_have_attribute("href", f"/resources/{RID}")
            expect(
                p.get_by_role("link", name="查看与处理", exact=True)
            ).to_have_attribute("href", f"/requests/{QID}")
            p.get_by_role("button", name="更多我的需求", exact=True).click()
            expect(p.get_by_text("第二页本人需求", exact=True)).to_be_visible()
            checks.append(
                "personal request pagination and distinct correction/resource versus request links"
            )
            reviewer = browser.new_context()
            fixtures(reviewer, role="reviewer")
            r = reviewer.new_page()
            r.goto(BASE + "/admin/events")
            expect(r.get_by_text("需要管理员权限", exact=False)).to_be_visible()
            checks.append("reviewer cannot use administrator recovery page")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    (ART / "checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__":
    main()
