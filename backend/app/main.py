import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import engine, Base

# Import all models to ensure they are registered with Base
from app.models import *  # noqa: F401,F403


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables on startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    yield
    await engine.dispose()


app = FastAPI(
    title="南邮知识库 API",
    description="NJUPT Knowledge Base Platform API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files for uploads
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# Register all routers
from app.api.auth import router as auth_router
from app.api.colleges import router as colleges_router
from app.api.courses import router as courses_router
from app.api.resources import router as resources_router
from app.api.upload import router as upload_router
from app.api.comments import router as comments_router
from app.api.favorites import router as favorites_router
from app.api.points import router as points_router
from app.api.baoyan import router as baoyan_router
from app.api.competitions import router as competitions_router
from app.api.transfer import router as transfer_router
from app.api.kaoyan import router as kaoyan_router
from app.api.scraper import router as scraper_router
from app.api.ai import router as ai_router
from app.api.search import router as search_router
from app.api.notifications import router as notifications_router

app.include_router(auth_router)
app.include_router(colleges_router)
app.include_router(courses_router)
app.include_router(resources_router)
app.include_router(upload_router)
app.include_router(comments_router)
app.include_router(favorites_router)
app.include_router(points_router)
app.include_router(baoyan_router)
app.include_router(competitions_router)
app.include_router(transfer_router)
app.include_router(kaoyan_router)
app.include_router(scraper_router)
app.include_router(ai_router)
app.include_router(search_router)
app.include_router(notifications_router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "njupt-kb"}
