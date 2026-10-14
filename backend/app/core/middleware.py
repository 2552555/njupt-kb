"""请求中间件：请求 ID、来源校验与 CSRF（设计文档 §14.2、§27、§28）。"""

from __future__ import annotations

import logging
import re
import time
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
        return any(origin.startswith(o.rstrip("/")) for o in settings.CORS_ORIGINS)

    @staticmethod
    def _reject(request: Request, code: str, message: str) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "req_unknown")
        return JSONResponse(status_code=403, content=error_body(code, message, request_id, None))


__all__ = ["RequestContextMiddleware", "CSRFMiddleware", "actor_id_var"]
