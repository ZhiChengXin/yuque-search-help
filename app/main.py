from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import get_settings
from .index_service import KnowledgeIndex

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("yuque-help-search")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


class SearchRequest(BaseModel):
    q: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(10, ge=1, le=30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    index = KnowledgeIndex(settings)
    app.state.index = index
    app.state.settings = settings

    async def bootstrap():
        result = await index.async_sync()
        if not result.get("ok"):
            logger.error("首次同步失败: %s", result.get("message"))

    async def periodic_sync():
        while True:
            await asyncio.sleep(settings.sync_interval_seconds)
            await index.async_sync()

    boot_task = asyncio.create_task(bootstrap())
    sync_task = asyncio.create_task(periodic_sync())
    try:
        yield
    finally:
        boot_task.cancel()
        sync_task.cancel()


app = FastAPI(title="Yuque Help Search", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    index: KnowledgeIndex = app.state.index
    return {"ok": True, **index.status()}


@app.get("/api/status")
async def status():
    index: KnowledgeIndex = app.state.index
    settings = app.state.settings
    return {
        **index.status(),
        "app_title": settings.app_title,
        "sync_interval_seconds": settings.sync_interval_seconds,
    }


@app.get("/api/search")
async def search(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(10, ge=1, le=30),
    mode: str = Query("all", max_length=20),
):
    index: KnowledgeIndex = app.state.index
    if not index.status().get("ready"):
        return {
            "ok": False,
            "message": "知识库尚未同步完成，请稍后重试",
            "query": q,
            "results": [],
            "contacts": [],
            "confidence": "none",
            "advice": "知识库同步中",
            "status": index.status(),
        }
    payload = await asyncio.to_thread(index.search, q, limit, mode)
    return {
        "ok": True,
        "query": q,
        "mode": mode,
        "count": len(payload.get("results") or []),
        **payload,
    }


@app.get("/api/suggest")
async def suggest(
    mode: str = Query("all", max_length=20),
    limit: int = Query(6, ge=1, le=12),
):
    index: KnowledgeIndex = app.state.index
    if not index.status().get("ready"):
        return {"ok": False, "mode": mode, "items": [], "source": "empty"}
    items = await asyncio.to_thread(index.suggest, mode, limit)
    return {
        "ok": True,
        "mode": mode,
        "items": items,
        "source": "index" if items else "empty",
    }


@app.post("/api/search")
async def search_post(body: SearchRequest):
    return await search(q=body.q, limit=body.limit)


@app.post("/api/sync")
async def sync_now():
    index: KnowledgeIndex = app.state.index
    result = await index.async_sync()
    return {**result, "status": index.status()}
