from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from .config import Settings
from .embeddings import EmbeddingClient, LocalVectorIndex
from .llm import LLMReranker
from .search_engine import SearchEngine, query_tokens, soften_query, wants_owner_contacts
from .yuque_client import YuqueClient

logger = logging.getLogger("yuque-help-search")


class KnowledgeIndex:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = SearchEngine()
        self.llm = LLMReranker(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
        )
        self.embed_client = EmbeddingClient(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.embedding_model,
        )
        self.vector_index: LocalVectorIndex | None = None
        if settings.embedding_enabled and self.embed_client.enabled:
            from pathlib import Path

            self.vector_index = LocalVectorIndex(
                Path(settings.data_dir) / "embeddings",
                self.embed_client,
            )
            self.engine.set_vector_index(self.vector_index)
            self.vector_count = self.vector_index.size
        else:
            self.vector_count = 0
        self._lock = threading.RLock()
        self.last_synced_at: float | None = None
        self.last_error: str | None = None
        self.syncing = False
        self.doc_count = 0
        self.section_count = 0
        self.contact_count = 0

    def _build_client(self) -> YuqueClient:
        return YuqueClient(
            space_login=self.settings.yuque_login,
            repo=self.settings.yuque_repo,
            host=self.settings.yuque_host,
            token=self.settings.yuque_token,
            username=self.settings.yuque_username,
            password=self.settings.yuque_password,
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
            # 本地向量索引：增量更新，原文仍只在语雀
            vector_stats = {"total": 0, "embedded": 0, "reused": 0}
            if self.vector_index is not None:
                try:
                    vector_stats = self.vector_index.rebuild(sections)
                    self.vector_count = int(vector_stats.get("total") or 0)
                except Exception:
                    logger.exception("本地向量索引更新失败，将仅使用关键词召回")
                    self.vector_count = 0
            else:
                self.vector_count = 0
            logger.info(
                "同步完成：文档 %s，章节 %s，联系人 %s，向量 %s",
                self.doc_count,
                self.section_count,
                self.contact_count,
                self.vector_count,
            )
            return {
                "ok": True,
                "doc_count": self.doc_count,
                "section_count": self.section_count,
                "contact_count": self.contact_count,
                "vector_count": self.vector_count,
                "vector_embedded": vector_stats.get("embedded", 0),
                "vector_reused": vector_stats.get("reused", 0),
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

    def search(self, query: str, limit: int = 10, mode: str = "all", *, fast: bool = False) -> dict[str, Any]:
        mode_l = (mode or "all").strip().lower()
        # 主动问负责人：优先短路到联系人，避免文档噪声
        if mode_l != "owner" and wants_owner_contacts(query):
            with self._lock:
                contacts = self.engine.match_contacts(query, limit=min(limit, 8))
            if contacts:
                return {
                    "results": [],
                    "contacts": [c.as_dict() for c in contacts],
                    "confidence": "high",
                    "advice": "已帮你找到相关模块的测试/产品负责人，可直接联系。",
                    "llm_used": False,
                    "suggest_feedback": False,
                }

        use_llm = (
            self.llm.enabled
            and not fast
            and mode_l != "owner"
        )
        # 关键词召回持锁；向量/大模型网络请求绝不能占锁，否则整站卡住
        with self._lock:
            payload = self.engine.search(
                query,
                limit=limit,
                mode=mode,
                keep_weak=use_llm,
                retrieve_limit=20 if use_llm else None,
                skip_vector=True,
            )
        # 锁外做向量召回（含 embedding API）
        payload = self.engine.apply_vector_recall(
            query,
            payload,
            pull=20 if use_llm else max(limit, 12),
            keep_weak=use_llm,
        )
        results = payload.get("results") or []
        # 标题已精确命中，或关键词已很强时，跳过大模型秒回
        top_pct = float((results[0].get("match_percent") if results else 0) or 0)
        strong_enough = bool(results) and (
            results[0].get("exact_title") or top_pct >= 88
        )
        if use_llm and strong_enough and _results_cover_query(query, results):
            payload["results"] = results[:limit]
            payload["llm_used"] = False
            payload["confidence"] = "high"
            payload["suggest_feedback"] = False
            payload["contacts"] = []
            payload["advice"] = ""
            return payload
        if not use_llm:
            # 模块负责人模式：只返回分工表结果，不要走「文档未命中」话术
            if mode_l == "owner":
                payload["suggest_feedback"] = False
                return payload
            # 已有联系人结果（引擎侧短路）直接返回
            if payload.get("contacts") and not results:
                payload["suggest_feedback"] = False
                return payload
            if not results or not _results_cover_query(query, results):
                return self._miss_payload(query, payload)
            payload["suggest_feedback"] = False
            payload["contacts"] = []
            return payload
        if not results:
            return self._miss_payload(query, payload)

        reranked = self.llm.rerank(query, results, limit=max(limit, 8))
        if reranked.get("used"):
            fused = _fuse_ranks(query, results, reranked.get("results") or [], limit)
            fused = [item for item in fused if float(item.get("match_percent") or 0) >= 55]
            if not _results_cover_query(query, fused):
                fused = []
            payload["results"] = fused
            payload["llm_used"] = True
            for item in payload["results"]:
                pct = float(item.get("match_percent") or 0)
                item["match_hint"] = f"匹配度 {pct:.1f}%"
            answer = reranked.get("answer") or ""
            if payload["results"]:
                top = payload["results"][0]
                top_pct = float(top.get("match_percent") or 0)
                payload["confidence"] = "high" if top_pct >= 75 else "medium"
                payload["suggest_feedback"] = False
                payload["contacts"] = []
                payload["advice"] = answer if answer and top_pct >= 65 else ""
            else:
                return self._miss_payload(query, payload)
        else:
            kept = results[:limit]
            if not _results_cover_query(query, kept):
                return self._miss_payload(query, payload)
            payload["results"] = kept
            payload["llm_used"] = False
            payload["suggest_feedback"] = False
            payload["contacts"] = []
        return payload

    def _miss_payload(self, query: str, base: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(base or {})
        # 只有主动问负责人时才带出联系人；普通文档检索不附带
        contacts: list[Any] = []
        if wants_owner_contacts(query):
            with self._lock:
                contacts = self.engine.match_contacts(query, limit=5)
        payload["results"] = []
        payload["contacts"] = [c.as_dict() for c in contacts]
        payload["llm_used"] = bool(self.llm.enabled)
        if contacts:
            payload["confidence"] = "high"
            payload["suggest_feedback"] = False
            payload["advice"] = "已帮你找到相关模块的测试/产品负责人，可直接联系。"
            return payload
        payload["confidence"] = "none"
        payload["suggest_feedback"] = True
        payload["advice"] = self.llm.assist_miss(query)
        return payload

    def lookup(self, section_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self.engine.lookup(section_id)
        if not item:
            return None
        return {
            "results": [item],
            "contacts": [],
            "confidence": "high",
            "advice": "",
            "llm_used": False,
            "suggest_feedback": False,
        }

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
                "vector_count": self.vector_count,
                "vector_enabled": self.vector_index is not None and self.vector_index.enabled,
                "last_synced_at": self.last_synced_at,
                "last_error": self.last_error,
                "book": f"{self.settings.yuque_login}/{self.settings.yuque_repo}",
                "book_url": (
                    f"{self.settings.yuque_host}/"
                    f"{self.settings.yuque_login}/{self.settings.yuque_repo}"
                ),
            }


def _results_cover_query(query: str, results: list[dict[str, Any]]) -> bool:
    """关键业务词至少命中一半，避免「采购」却命中「签名管理」这类假匹配。"""
    if not results:
        return False
    top = results[0]
    if top.get("exact_title"):
        return True

    try:
        vec_score = float(top.get("vector_score") or 0)
    except (TypeError, ValueError):
        vec_score = 0.0

    soft = soften_query(query)
    keys = [
        t
        for t in query_tokens(query)
        if len(t) >= 2
        and t
        not in {
            "怎么",
            "如何",
            "什么",
            "配置",
            "设置",
            "进行",
            "页面",
            "一直",
            "今天",
            "怎么样",
            "怎样",
        }
    ]
    normalized_keys = []
    seen = set()
    for t in keys:
        nt = soften_query(t) or t
        if nt not in seen:
            seen.add(nt)
            normalized_keys.append(nt)
    core = [t for t in normalized_keys if len(t) >= 3] or normalized_keys

    blob = " ".join(
        [
            str(top.get("title") or ""),
            str(top.get("doc_title") or ""),
            str(top.get("preview") or ""),
            str(top.get("snippet") or ""),
            str(top.get("answer") or ""),
            " ".join(top.get("catalog_path") or []),
        ]
    ).lower()
    soft_blob = soften_query(blob)
    hits = sum(1 for t in core if t in blob or t in soft_blob) if core else 0
    lexical_ok = (not core) or (hits >= 1 and hits / max(len(core), 1) >= 0.34)

    # 向量很高也要有一点字面交集；否则当误召回
    if top.get("vector_hit") and vec_score >= 0.55 and lexical_ok:
        return True
    if top.get("vector_hit") and vec_score >= 0.45 and lexical_ok and hits / max(len(core), 1) >= 0.45:
        return True
    if not core:
        return True
    if len(core) == 1:
        return hits >= 1
    return hits / len(core) >= 0.40


def _fuse_ranks(
    query: str,
    lexical: list[dict[str, Any]],
    llm_items: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """关键词召回与模型打分融合；模型未选中的弱结果直接丢掉。"""
    if not llm_items:
        # 模型明确认为都无关时，不再用弱关键词结果硬撑
        strong = [
            dict(item, match_percent=float(item.get("match_percent") or 0))
            for item in lexical
            if item.get("exact_title")
        ]
        return strong[:limit]

    llm_map = {item.get("id"): item for item in llm_items if item.get("id")}
    q_tokens = [t for t in query_tokens(query) if len(t) >= 2]
    fused: list[dict[str, Any]] = []
    for item in lexical:
        rid = item.get("id")
        llm_item = llm_map.get(rid)
        if not llm_item and not item.get("exact_title") and not item.get("vector_hit"):
            continue
        blob = f"{item.get('title') or ''} {item.get('doc_title') or ''}".lower()
        soft_blob = soften_query(blob)
        overlap = sum(1 for t in q_tokens if t in blob or soften_query(t) in soft_blob)
        strong = (
            bool(item.get("exact_title"))
            or overlap >= 2
            or (item.get("vector_hit") and float(item.get("vector_score") or 0) >= 0.42)
        )
        bm25 = float(item.get("match_percent") or 0)
        if llm_item:
            llm_pct = float(llm_item.get("match_percent") or 0)
            score = llm_pct * 0.72 + bm25 * 0.28
            if strong:
                score = max(score, bm25, llm_pct)
                score = min(100.0, score + 6)
        else:
            score = bm25
        cloned = dict(item)
        cloned["match_percent"] = round(min(100.0, max(0.0, score)), 1)
        cloned["llm_ranked"] = bool(llm_item)
        fused.append(cloned)
    fused.sort(
        key=lambda x: (float(x.get("match_percent") or 0), 1 if x.get("llm_ranked") else 0),
        reverse=True,
    )
    return fused[:limit]
