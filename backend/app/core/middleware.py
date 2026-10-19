"""请求中间件：请求 ID、来源校验与 CSRF（设计文档 §14.2、§27、§28）。"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlsplit
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.config import settings
from app.core.errors import error_body
from app.core.logging import actor_id_var, new_request_id, request_id_var
from app.core.security import constant_time_equal

logger = logging.getLogger("app.request")

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# 未建立会话的入口不做双提交校验，改为强制来源校验（§14.2）。
SESSIONLESS_PATHS = {
    f"{settings.API_PREFIX}/auth/login",
    f"{settings.API_PREFIX}/auth/register",
    f"{settings.API_PREFIX}/auth/email-verifications",
    f"{settings.API_PREFIX}/auth/email-verifications/confirm",
    f"{settings.API_PREFIX}/auth/password-resets",
    f"{settings.API_PREFIX}/auth/password-resets/confirm",
    f"{settings.API_PREFIX}/auth/csrf",
    # 浏览器自动上报的 CSP 违规报告没有页面上下文，无法携带 CSRF 令牌；
    # 该端点只写日志、限体积、按来源限流，不读写业务数据。
    f"{settings.API_PREFIX}/csp/reports",
}

# 支付渠道的服务端回调没有浏览器会话，也没有来源头；它的安全边界是验签（§24.3），
# 因此豁免 CSRF 与来源校验，但不得借此放宽任何权限判断。
SESSIONLESS_PATTERNS = (
    re.compile(rf"^{re.escape(settings.API_PREFIX)}/payments/[^/]+/notify$"),
)


def _is_sessionless(path: str) -> bool:
    return path in SESSIONLESS_PATHS or any(p.match(path) for p in SESSIONLESS_PATTERNS)


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-Id") or new_request_id()
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers["X-Request-Id"] = request_id
        if request.url.path.startswith(settings.API_PREFIX):
            logger.info(
                "http_request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": elapsed_ms,
                },
            )
        return response


class CSRFMiddleware(BaseHTTPMiddleware):
    """Cookie 会话下的双提交校验；未建立会话的入口退回来源校验。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method not in UNSAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        if not path.startswith(settings.API_PREFIX):
            return await call_next(request)

        if _is_sessionless(path):
            # 渠道回调依赖验签而不是来源校验，跳过 Origin 检查。
            if any(p.match(path) for p in SESSIONLESS_PATTERNS):
                return await call_next(request)
            if not self._origin_allowed(request):
                return self._reject(request, "ORIGIN_NOT_ALLOWED", "请求来源不被允许")
            return await call_next(request)

        cookie_token = request.cookies.get(settings.CSRF_COOKIE_NAME)
        header_token = request.headers.get(settings.CSRF_HEADER_NAME)
        if not cookie_token or not header_token or not constant_time_equal(cookie_token, header_token):
            return self._reject(request, "CSRF_FAILED", "缺少或无效的 CSRF 令牌")
        return await call_next(request)

    @staticmethod
    def _origin_allowed(request: Request) -> bool:
        origin = request.headers.get("Origin") or request.headers.get("Referer")
        if not origin:
            # 非浏览器客户端（脚本、测试）不发送来源头；这些入口仍受限流保护。
            return True
        def site(value: str):
            try:
                parsed = urlsplit(value)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                    return None
                return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
            except ValueError:
                return None

        actual = site(origin)
        return actual is not None and actual in {site(value) for value in settings.CORS_ORIGINS}

    @staticmethod
    def _reject(request: Request, code: str, message: str) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "req_unknown")
        return JSONResponse(status_code=403, content=error_body(code, message, request_id, None))


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """为每个 API 响应注入严格安全响应头，并在生产环境强制 HTTPS 传输（补充规范 §27/§28）。

    前端页面由 Next.js 自身通过 next.config 的 headers() 配置更宽松、适合 HTML/JS 的 CSP；
    此处 API 仅返回 JSON，故采用 ``default-src 'none'`` 的最严格策略。
    """

    @staticmethod
    def _is_secure(request: Request) -> bool:
        # 反向代理终止 TLS 后，原始协议通过 X-Forwarded-Proto 透传。
        return request.url.scheme == "https" or request.headers.get("X-Forwarded-Proto", "").lower() == "https"

    @staticmethod
    def _apply(response: Response) -> None:
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=(), payment=()")
        # API 仅返回 JSON，无内联脚本/帧需求；前端页面应使用更适合 HTML 的宽松 CSP。
        headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
        headers.setdefault("X-XSS-Protection", "0")  # 已弃用，依赖 CSP 兜底
        # 敏感 JSON 避免落盘缓存；若响应已声明缓存策略则尊重原值。
        if not headers.get("Cache-Control"):
            headers["Cache-Control"] = "no-store"
        # HSTS 仅在 prod（cookie_secure 为真）下发，避免非 HTTPS 部署被锁死。
        if settings.cookie_secure:
            headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # 生产环境强制 HTTPS（应对反向代理后的 TLS 终止）。非 prod 跳过，便于本地 http 调试。
        if settings.is_prod and not self._is_secure(request):
            request_id = getattr(request.state, "request_id", "req_unknown")
            response: Response = JSONResponse(
                status_code=403,
                content=error_body("INSECURE_TRANSPORT", "仅允许通过 HTTPS 访问", request_id, None),
            )
            self._apply(response)
            return response
        response = await call_next(request)
        self._apply(response)
        return response


__all__ = ["RequestContextMiddleware", "CSRFMiddleware", "SecurityHeadersMiddleware", "actor_id_var"]
