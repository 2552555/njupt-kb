"""统一错误体与异常处理器（设计文档 §25.1）。

业务代码只抛 AppError 子类；HTTP 状态码与错误体形状由本模块保证一致。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings

DEFAULT_CODES: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
    status.HTTP_401_UNAUTHORIZED: "UNAUTHENTICATED",
    status.HTTP_403_FORBIDDEN: "FORBIDDEN",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_409_CONFLICT: "CONFLICT",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "VALIDATION_ERROR",
    status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
    status.HTTP_503_SERVICE_UNAVAILABLE: "DEPENDENCY_UNAVAILABLE",
}


class AppError(Exception):
    """所有可预期业务错误的基类。"""

    http_status: int = status.HTTP_400_BAD_REQUEST
    code: str = "BAD_REQUEST"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        self.details = details or {}
        # 429 等需要随响应下发的头（如 Retry-After）；不改变错误体形状。
        self.headers = headers or None


class InvalidInput(AppError):
    http_status = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "VALIDATION_ERROR"


class Unauthenticated(AppError):
    http_status = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHENTICATED"


class Forbidden(AppError):
    http_status = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"


class NotFound(AppError):
    """私有对象按统一策略返回 404 避免泄露存在性（§25.1）。"""

    http_status = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"


class Conflict(AppError):
    """状态或幂等冲突（§25.1）。并发审核、过期页面提交都返回它（§13.3、§29）。"""

    http_status = status.HTTP_409_CONFLICT
    code = "CONFLICT"


class RateLimited(AppError):
    http_status = status.HTTP_429_TOO_MANY_REQUESTS
    code = "RATE_LIMITED"


class DependencyUnavailable(AppError):
    """依赖不可用时必须显式失败，不能默认放行（§30）。"""

    http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "DEPENDENCY_UNAVAILABLE"


class NotEnabled(AppError):
    """阶段功能未启用（§25.2）：不提供"返回成功但未实现"的占位行为。"""

    http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "FEATURE_NOT_ENABLED"


def error_body(code: str, message: str, request_id: str, details: dict[str, Any] | None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "details": details or {},
        }
    }


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or "req_unknown"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=error_body(exc.code, exc.message, _request_id(request), exc.details),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=error_body(
                "VALIDATION_ERROR",
                "请求参数不符合要求",
                _request_id(request),
                {"fields": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = DEFAULT_CODES.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, str(exc.detail), _request_id(request), None),
        )

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        # 生产环境不向客户端泄露堆栈/内部路径/配置细节；dev 环境保留异常信息便于排查。
        if settings.is_prod:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content=error_body("INTERNAL_ERROR", "服务器内部错误", _request_id(request), None),
            )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body("INTERNAL_ERROR", str(exc), _request_id(request), None),
        )
