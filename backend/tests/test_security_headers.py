"""安全响应头与传输安全中间件、生产环境错误处理硬化（fastapi.TestClient）。

仅验证本地可执行的代码级硬化。依赖反向代理的强传输强制与前端 next.config 运行时生效
属于部署侧验证，标注 NOT_RUN（见交付报告）。
"""

import unittest
from unittest.mock import PropertyMock, patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app

EXPECTED_STATIC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), payment=()",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "X-XSS-Protection": "0",
}


def _prod_patch():
    # is_prod / cookie_secure 均为 property，使用 PropertyMock 覆盖。
    return (
        patch.object(Settings, "is_prod", new_callable=PropertyMock, return_value=True),
        patch.object(Settings, "cookie_secure", new_callable=PropertyMock, return_value=True),
    )


class SecurityHeadersDevTests(unittest.TestCase):
    """默认 dev 环境：下发静态安全头，但不下发 HSTS，且不强制 HTTPS。"""

    def setUp(self):
        self.client = TestClient(app)

    def test_static_security_headers_present(self):
        resp = self.client.get("/api/v1/health/live")
        self.assertEqual(resp.status_code, 200)
        for key, value in EXPECTED_STATIC_HEADERS.items():
            self.assertEqual(resp.headers.get(key), value, f"缺少/不匹配安全头 {key}")

    def test_cache_control_no_store_by_default(self):
        resp = self.client.get("/api/v1/health/live")
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")

    def test_hsts_absent_in_dev(self):
        resp = self.client.get("/api/v1/health/live")
        self.assertNotIn("Strict-Transport-Security", resp.headers)

    def test_http_allowed_in_dev(self):
        resp = self.client.get("/api/v1/health/live")
        self.assertEqual(resp.status_code, 200)


class SecurityHeadersProdTests(unittest.TestCase):
    """prod 环境：下发 HSTS；非 https 请求返回 403；反向代理 X-Forwarded-Proto 可放行。"""

    def test_hsts_present_in_prod(self):
        p1, p2 = _prod_patch()
        with p1, p2:
            client = TestClient(app, base_url="https://testserver")
            resp = client.get("/api/v1/health/live")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(
                resp.headers.get("Strict-Transport-Security"),
                "max-age=31536000; includeSubDomains",
            )
            for key, value in EXPECTED_STATIC_HEADERS.items():
                self.assertEqual(resp.headers.get(key), value)

    def test_insecure_http_rejected_in_prod(self):
        p1, p2 = _prod_patch()
        with p1, p2:
            client = TestClient(app, base_url="http://testserver")
            resp = client.get("/api/v1/health/live")
            self.assertEqual(resp.status_code, 403)
            self.assertEqual(resp.json()["error"]["code"], "INSECURE_TRANSPORT")

    def test_forwarded_proto_https_allowed_in_prod(self):
        p1, p2 = _prod_patch()
        with p1, p2:
            client = TestClient(app, base_url="http://testserver")
            resp = client.get(
                "/api/v1/health/live", headers={"X-Forwarded-Proto": "https"}
            )
            self.assertEqual(resp.status_code, 200)


class ProdErrorLeakTests(unittest.TestCase):
    """prod 环境：未处理异常返回通用错误，不泄露堆栈/内部路径/配置。"""

    ROUTE = "/__sec_test_boom"
    LEAK_MARKER = "SECRET_PATH_/var/lib/njupt-kb/db.conf"

    def setUp(self):
        async def _boom():
            raise RuntimeError(self.LEAK_MARKER)

        app.add_api_route(self.ROUTE, _boom, methods=["GET"])

    def tearDown(self):
        for i, r in enumerate(app.routes):
            if getattr(r, "path", None) == self.ROUTE:
                app.routes.pop(i)
                break

    def test_prod_500_does_not_leak_internal_detail(self):
        p1 = patch.object(Settings, "is_prod", new_callable=PropertyMock, return_value=True)
        p2 = patch.object(Settings, "cookie_secure", new_callable=PropertyMock, return_value=False)
        with p1, p2:
            # ServerErrorMiddleware 在发送 500 响应后仍会向上抛原异常（设计如此，便于服务端记录与测试），
            # 故关闭 raise_server_exceptions 以取回已下发的 500 响应体做断言。
            client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
            resp = client.get(self.ROUTE)
            self.assertEqual(resp.status_code, 500)
            body = resp.json()
            self.assertEqual(body["error"]["code"], "INTERNAL_ERROR")
            self.assertNotIn(self.LEAK_MARKER, resp.text)
            self.assertNotIn("/var/lib", body["error"]["message"])


if __name__ == "__main__":
    unittest.main()
