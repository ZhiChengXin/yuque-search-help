from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import get_settings
from .feedback import prepare_feedback, run_feedback_job
from .index_service import KnowledgeIndex

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("yuque-help-search")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

_webhook_sync_task: asyncio.Task | None = None


def seconds_until_daily_at(hhmm: str) -> float:
    """距下一次本地 HH:MM 的秒数（用于每天定时同步）。"""
    text = (hhmm or "").strip()
    if not text:
        raise ValueError("empty sync_daily_at")
    parts = text.split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


class SearchRequest(BaseModel):
    q: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(10, ge=1, le=30)


class FeedbackRequest(BaseModel):
    description: str = Field("", max_length=4000)
    images: list[str] = Field(default_factory=list)
    query: str = Field("", max_length=200)
    mode: str = Field("", max_length=20)


async def _debounced_sync(index: KnowledgeIndex, delay: float, reason: str) -> None:
    try:
        await asyncio.sleep(max(0.5, delay))
        logger.info("webhook 触发同步 reason=%s", reason)
        result = await index.async_sync()
        if not result.get("ok"):
            logger.error("webhook 同步失败: %s", result.get("message"))
        else:
            logger.info(
                "webhook 同步完成 sections=%s",
                (result.get("section_count") or result.get("status", {}).get("section_count")),
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("webhook 同步异常")


def schedule_webhook_sync(index: KnowledgeIndex, delay: float, reason: str) -> None:
    global _webhook_sync_task
    if _webhook_sync_task and not _webhook_sync_task.done():
        _webhook_sync_task.cancel()
    _webhook_sync_task = asyncio.create_task(_debounced_sync(index, delay, reason))


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings.cache_clear()
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
            daily_at = (settings.sync_daily_at or "").strip()
            if daily_at:
                delay = seconds_until_daily_at(daily_at)
                next_at = datetime.now() + timedelta(seconds=delay)
                logger.info(
                    "下次定时同步 %s（约 %.1f 小时后，本地时间）",
                    next_at.strftime("%Y-%m-%d %H:%M"),
                    delay / 3600.0,
                )
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(max(60, settings.sync_interval_seconds))
            logger.info("开始定时同步")
            result = await index.async_sync()
            if not result.get("ok"):
                logger.error("定时同步失败: %s", result.get("message"))

    boot_task = asyncio.create_task(bootstrap())
    sync_task = asyncio.create_task(periodic_sync())
    try:
        yield
    finally:
        global _webhook_sync_task
        if _webhook_sync_task and not _webhook_sync_task.done():
            _webhook_sync_task.cancel()
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
        "sync_daily_at": settings.sync_daily_at,
    }


@app.get("/api/search")
async def search(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(5, ge=1, le=30),
    mode: str = Query("all", max_length=20),
    fast: bool = Query(False),
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
    payload = await asyncio.to_thread(index.search, q, limit, mode, fast=fast)
    return {
        "ok": True,
        "query": q,
        "mode": mode,
        "count": len(payload.get("results") or []),
        **payload,
    }


@app.get("/api/lookup")
async def lookup(id: str = Query(..., min_length=1, max_length=200)):
    index: KnowledgeIndex = app.state.index
    if not index.status().get("ready"):
        return {"ok": False, "message": "知识库尚未同步完成", "results": []}
    payload = await asyncio.to_thread(index.lookup, id)
    if not payload:
        return {"ok": False, "message": "未找到该章节", "results": []}
    return {"ok": True, "count": 1, **payload}


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
    """内部/运维手动同步；页面已改为语雀 Webhook + 定时同步。"""
    index: KnowledgeIndex = app.state.index
    result = await index.async_sync()
    return {**result, "status": index.status()}


@app.post("/api/yuque/webhook")
async def yuque_webhook(
    request: Request,
    token: str = Query("", max_length=200),
    x_yuque_token: str | None = Header(default=None, alias="X-Yuque-Token"),
):
    """语雀知识库「消息推送」回调：文档发布/更新后防抖同步索引。"""
    settings = app.state.settings
    secret = (settings.yuque_webhook_secret or "").strip()
    provided = (token or x_yuque_token or "").strip()
    if secret and provided != secret:
        return JSONResponse({"ok": False, "message": "unauthorized"}, status_code=401)

    payload: Any
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    action = ""
    title = ""
    if isinstance(payload, dict):
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        action = str(
            payload.get("webhook_subject_type")
            or payload.get("action")
            or payload.get("event")
            or ""
        )
        if isinstance(data, dict):
            title = str(data.get("title") or data.get("path") or "")[:120]
    reason = f"{action}:{title}" if title or action else "yuque-webhook"
    schedule_webhook_sync(
        app.state.index,
        float(settings.yuque_webhook_debounce_seconds or 12),
        reason,
    )
    return {"ok": True, "accepted": True, "reason": reason}


@app.post("/api/feedback")
async def feedback(body: FeedbackRequest, background_tasks: BackgroundTasks):
    settings = app.state.settings
    prepared = await asyncio.to_thread(
        prepare_feedback,
        description=body.description,
        images=body.images[:6],
        query=body.query,
        mode=body.mode,
    )
    if not prepared.get("ok"):
        return {"ok": False, "message": prepared.get("message", "提交失败")}

    record = prepared["record"]
    parsed_images = prepared["parsed_images"]

    async def _write_in_background() -> None:
        await asyncio.to_thread(run_feedback_job, settings, record, parsed_images)

    background_tasks.add_task(_write_in_background)
    return {
        "ok": True,
        "message": "已提交",
        "title": prepared.get("title") or "",
    }
