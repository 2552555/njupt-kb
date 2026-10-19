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
from app import __version__
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import CSRFMiddleware, RequestContextMiddleware, SecurityHeadersMiddleware
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
    version=__version__,
    docs_url=f"{settings.API_PREFIX}/docs" if settings.ENV != "prod" else None,
    redoc_url=None,
    openapi_url=f"{settings.API_PREFIX}/openapi.json" if settings.ENV != "prod" else None,
    lifespan=lifespan,
)

# 顺序：安全响应头/传输安全注册在最外层，先于 CORS 与路由生效，
# 使其作用于全部响应（含 CORS 预检），并在反向代理 TLS 终止场景下优先拦截明文请求。
app.add_middleware(SecurityHeadersMiddleware)

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
    return {"status": "ok", "service": "njupt-kb-api", "version": __version__}


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


@app.get(f"{settings.API_PREFIX}/features", tags=["features"])
async def feature_flags() -> dict[str, bool]:
    """只公开界面所需的启用状态，不返回配置或供应商信息。"""
    return {
        "commerce_enabled": settings.COMMERCE_ENABLED and not settings.COMMERCE_COMPLIANCE_GATE_CLOSED,
        "ai_enabled": settings.AI_ENABLED,
    }


# --- 业务模块路由（顺序与 docs/ARCHITECTURE.md 一致）---
from app.modules.access.router import router as access_router  # noqa: E402
from app.modules.ai.router import router as ai_router  # noqa: E402
from app.modules.assignment.router import router as assignment_router  # noqa: E402
from app.modules.cases.router import router as cases_router  # noqa: E402
from app.modules.catalog.router import router as catalog_router  # noqa: E402
from app.modules.commerce.router import router as commerce_router  # noqa: E402
from app.modules.content.router import router as content_router  # noqa: E402
from app.modules.content.guides_router import router as guides_router  # noqa: E402
from app.modules.creator.router import router as creator_router  # noqa: E402
from app.modules.defense.router import honeypot_handler  # noqa: E402
from app.modules.defense.router import router as defense_router  # noqa: E402
from app.modules.identity.router import router as identity_router  # noqa: E402
from app.modules.identity.mfa_router import router as mfa_router  # noqa: E402
from app.modules.interaction.router import router as interaction_router  # noqa: E402
from app.modules.jobs.router import router as jobs_router  # noqa: E402
from app.modules.licensing.router import router as licensing_router  # noqa: E402
from app.modules.moderation.router import router as moderation_router  # noqa: E402
from app.modules.notification.router import router as notification_router  # noqa: E402
from app.modules.ops.router import router as ops_router  # noqa: E402
from app.modules.reco.router import router as reco_router  # noqa: E402
from app.modules.search.router import router as search_router  # noqa: E402
from app.modules.settlement.router import router as settlement_router  # noqa: E402
from app.modules.topics.router import router as topics_router  # noqa: E402

for _router in (
    # beta16：统一搜索先注册，占用 `/search`；interaction 的资源搜索已迁至 `/search/resources`。
    search_router,
    identity_router,
    mfa_router,
    catalog_router,
    content_router,
    guides_router,
    moderation_router,
    access_router,
    interaction_router,
    notification_router,
    jobs_router,
    commerce_router,
    licensing_router,
    creator_router,
    assignment_router,
    cases_router,
    ai_router,
    topics_router,
    defense_router,
    reco_router,
    ops_router,
    settlement_router,
):
    app.include_router(_router, prefix=settings.API_PREFIX)


# 蜜罐路径按配置单独注册：不进入 OpenAPI、不在正常导航出现，响应固定且无业务能力。
# 若配置值与真实路由同路径，启动即失败，避免误把真实接口变成诱饵。
if settings.HONEYPOT_ENABLED:
    if any(getattr(route, "path", None) == settings.HONEYPOT_PATH for route in app.routes):
        raise RuntimeError(f"蜜罐路径与既有路由冲突: {settings.HONEYPOT_PATH}")
    app.add_api_route(
        settings.HONEYPOT_PATH,
        honeypot_handler,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
