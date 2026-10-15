"""应用装配。

设计文档 §7：一个模块化后端。§31.1：启动时检查必要配置。
设计文档 §31.2：不以启动时 create_all 替代数据库版本演进，建表由 Alembic 负责。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import CSRFMiddleware, RequestContextMiddleware
from app.core.ratelimit import redis_healthy
from app.core.storage import storage
from app.db import engine

# 必须在应用装配前导入，确保所有表注册到 Base.metadata
import app.models_registry  # noqa: F401  isort:skip

configure_logging()
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", extra={"env": settings.ENV, "app": settings.APP_NAME})
    if settings.ENV != "prod":
        try:
            created = await storage.ensure_buckets()
            logger.info("storage_buckets_ready", extra={"buckets": created})
        except Exception as exc:  # noqa: BLE001 - 开发环境允许对象存储稍后可用
            logger.warning("storage_bucket_init_failed", extra={"error": str(exc)})
    yield
    await engine.dispose()
    logger.info("shutdown")


app = FastAPI(
    title=settings.APP_NAME,
    version="0.0.1bate2",
    docs_url=f"{settings.API_PREFIX}/docs" if settings.ENV != "prod" else None,
    redoc_url=None,
    openapi_url=f"{settings.API_PREFIX}/openapi.json" if settings.ENV != "prod" else None,
    lifespan=lifespan,
)

# 顺序：来源校验依赖 CORS 头，故 CORS 在 CSRF 之外。
app.add_middleware(RequestContextMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Idempotency-Key", settings.CSRF_HEADER_NAME, "X-Request-Id"],
    expose_headers=["X-Request-Id"],
)

install_error_handlers(app)


# --- 健康检查（§25.2）：不向外暴露凭证与连接串 ---
health_router = APIRouter(tags=["health"])


@health_router.get("/health/live")
async def health_live() -> dict[str, str]:
    """进程存活。"""
    return {"status": "ok", "service": "njupt-kb-api", "version": "0.0.1bate2"}


@health_router.get("/health/ready")
async def health_ready(response: Response) -> dict[str, object]:
    """必要依赖就绪。AI 不是 API 就绪检查的必要依赖（§31.1）。"""
    checks: dict[str, bool] = {}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:  # noqa: BLE001
        checks["database"] = False
    checks["object_storage"] = await storage.health()
    checks["redis"] = await redis_healthy()

    # 对象存储与数据库是核心路径的硬依赖；Redis 故障时核心行为按 §30 降级，不影响就绪。
    ready = checks["database"] and checks["object_storage"]
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "checks": checks}


app.include_router(health_router)
app.include_router(health_router, prefix=settings.API_PREFIX)


# --- 业务模块路由（顺序与 docs/ARCHITECTURE.md 一致）---
from app.modules.access.router import router as access_router  # noqa: E402
from app.modules.ai.router import router as ai_router  # noqa: E402
from app.modules.catalog.router import router as catalog_router  # noqa: E402
from app.modules.commerce.router import router as commerce_router  # noqa: E402
from app.modules.content.router import router as content_router  # noqa: E402
from app.modules.creator.router import router as creator_router  # noqa: E402
from app.modules.identity.router import router as identity_router  # noqa: E402
from app.modules.interaction.router import router as interaction_router  # noqa: E402
from app.modules.jobs.router import router as jobs_router  # noqa: E402
from app.modules.moderation.router import router as moderation_router  # noqa: E402
from app.modules.notification.router import router as notification_router  # noqa: E402
from app.modules.topics.router import router as topics_router  # noqa: E402

for _router in (
    identity_router,
    catalog_router,
    content_router,
    moderation_router,
    access_router,
    interaction_router,
    notification_router,
    jobs_router,
    commerce_router,
    creator_router,
    ai_router,
    topics_router,
):
    app.include_router(_router, prefix=settings.API_PREFIX)
