from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from .config import Settings
from .search_engine import SearchEngine
from .yuque_client import YuqueClient

logger = logging.getLogger("yuque-help-search")


class KnowledgeIndex:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = SearchEngine()
        self._lock = threading.RLock()
        self.last_synced_at: float | None = None
        self.last_error: str | None = None
        self.syncing = False
        self.doc_count = 0
        self.section_count = 0
        self.contact_count = 0

    def _build_client(self) -> YuqueClient:
        return YuqueClient(
            username=self.settings.yuque_username,
            password=self.settings.yuque_password,
            space_login=self.settings.yuque_login,
            repo=self.settings.yuque_repo,
            host=self.settings.yuque_host,
        )

    def sync(self) -> dict[str, Any]:
        with self._lock:
            if self.syncing:
                return {"ok": False, "message": "同步进行中"}
            self.syncing = True
            self.last_error = None

        client = self._build_client()
        try:
            logger.info("开始同步语雀知识库…")
            client.login()
            sections, contacts = client.fetch_all()
            docs = {s.doc_id for s in sections}
            with self._lock:
                self.engine.rebuild(sections, contacts)
                self.doc_count = len(docs)
                self.section_count = len([s for s in sections if s.kind != "contact_row"])
                self.contact_count = len(contacts)
                self.last_synced_at = time.time()
            logger.info(
                "同步完成：文档 %s，章节 %s，联系人 %s",
                self.doc_count,
                self.section_count,
                self.contact_count,
            )
            return {
                "ok": True,
                "doc_count": self.doc_count,
                "section_count": self.section_count,
                "contact_count": self.contact_count,
            }
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            logger.exception("同步失败")
            return {"ok": False, "message": str(exc)}
        finally:
            client.close()
            with self._lock:
                self.syncing = False

    async def async_sync(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.sync)

    def search(self, query: str, limit: int = 10, mode: str = "all") -> dict[str, Any]:
        with self._lock:
            return self.engine.search(query, limit=limit, mode=mode)

    def suggest(self, mode: str = "all", limit: int = 6) -> list[dict[str, str]]:
        with self._lock:
            return self.engine.suggest(mode=mode, limit=limit)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "ready": self.section_count > 0 or self.contact_count > 0,
                "syncing": self.syncing,
                "doc_count": self.doc_count,
                "section_count": self.section_count,
                "contact_count": self.contact_count,
                "last_synced_at": self.last_synced_at,
                "last_error": self.last_error,
                "book": f"{self.settings.yuque_login}/{self.settings.yuque_repo}",
                "book_url": (
                    f"{self.settings.yuque_host}/"
                    f"{self.settings.yuque_login}/{self.settings.yuque_repo}"
                ),
            }
