from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from .config import Settings
from .embeddings import EmbeddingClient, LocalVectorIndex
from .llm import LLMReranker
from .query_rewrite import rewrite_query
from .search_engine import (
    SearchEngine,
    gate_query_tokens,
    query_tokens,
    soften_query,
    wants_owner_contacts,
)
from .synonyms import configure_synonyms
from .yuque_client import YuqueClient

logger = logging.getLogger("yuque-help-search")


class KnowledgeIndex:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        configure_synonyms(settings.data_dir)
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
        original_query = (query or "").strip()
        display_limit = min(limit, 5)
        use_llm = (
            self.llm.enabled
            and not fast
            and mode_l != "owner"
        )

        # 主动问负责人：优先短路到联系人，避免文档噪声
        if mode_l != "owner" and wants_owner_contacts(original_query):
            with self._lock:
                contacts = self.engine.match_contacts(original_query, limit=min(limit, 8))
            if contacts:
                return {
                    "results": [],
                    "contacts": [c.as_dict() for c in contacts],
                    "confidence": "high",
                    "advice": "已帮你找到相关模块的测试/产品负责人，可直接联系。",
                    "llm_used": False,
                    "suggest_feedback": False,
                }

        primary_query = rewrite_query(original_query) or original_query
        expanded: list[str] = []
        first_queries = [primary_query]
        if use_llm:
            expanded = self.llm.expand_queries(original_query, limit=3)
            for eq in expanded:
                rq = rewrite_query(eq) or eq
                if rq not in first_queries:
                    first_queries.append(rq)

        payload = self._search_with_queries(
            original_query,
            first_queries,
            limit=display_limit,
            mode=mode,
            use_llm=use_llm,
            retrieve_limit=40 if use_llm else max(limit, 16),
        )
        if use_llm and not (payload.get("results") or []) and expanded:
            retry = self._search_with_queries(
                original_query,
                first_queries,
                limit=display_limit,
                mode=mode,
                use_llm=use_llm,
                retrieve_limit=56,
                second_pass=True,
                rerank_min_score=48,
            )
            if retry.get("results"):
                return retry
        return payload

    def _search_with_queries(
        self,
        original_query: str,
        search_queries: list[str],
        *,
        limit: int,
        mode: str,
        use_llm: bool,
        retrieve_limit: int,
        second_pass: bool = False,
        rerank_min_score: float = 52,
    ) -> dict[str, Any]:
        merged_lists: list[list[dict[str, Any]]] = []
        base_payload: dict[str, Any] = {}
        for sq in search_queries:
            with self._lock:
                part = self.engine.search(
                    sq,
                    limit=limit,
                    mode=mode,
                    keep_weak=use_llm,
                    retrieve_limit=retrieve_limit,
                    skip_vector=True,
                )
            part = self.engine.apply_vector_recall(
                sq,
                part,
                pull=retrieve_limit,
                keep_weak=use_llm,
            )
            merged_lists.append(part.get("results") or [])
            if not base_payload:
                base_payload = part

        results = _merge_result_lists(merged_lists, cap=retrieve_limit)
        base_payload["results"] = results

        # 仅标题精确命中时跳过大模型重排
        if use_llm and results and results[0].get("exact_title"):
            if _results_cover_query(original_query, results):
                base_payload["results"] = _sort_by_match_percent(results)[:limit]
                base_payload["llm_used"] = False
                base_payload["confidence"] = "high"
                base_payload["suggest_feedback"] = False
                base_payload["contacts"] = []
                base_payload["advice"] = ""
                return base_payload

        mode_l = (mode or "all").strip().lower()
        if not use_llm:
            if mode_l == "owner":
                base_payload["suggest_feedback"] = False
                return base_payload
            if base_payload.get("contacts") and not results:
                base_payload["suggest_feedback"] = False
                return base_payload
            if not results or not _results_cover_query(original_query, results):
                return self._miss_payload(original_query, base_payload)
            base_payload["results"] = _sort_by_match_percent(results)[:limit]
            base_payload["suggest_feedback"] = False
            base_payload["contacts"] = []
            return base_payload

        if not results:
            return self._miss_payload(original_query, base_payload)

        reranked = self.llm.rerank(original_query, results, limit=max(limit, 8))
        if reranked.get("used"):
            fused = _fuse_ranks(original_query, results, reranked.get("results") or [], limit)
            fused = [item for item in fused if float(item.get("match_percent") or 0) >= rerank_min_score]
            if not _results_cover_query(original_query, fused):
                fused = []
            base_payload["results"] = _sort_by_match_percent(fused)[:limit]
            base_payload["llm_used"] = True
            for item in base_payload["results"]:
                pct = float(item.get("match_percent") or 0)
                item["match_hint"] = f"匹配度 {pct:.1f}%"
            answer = reranked.get("answer") or ""
            if base_payload["results"]:
                top = base_payload["results"][0]
                top_pct = float(top.get("match_percent") or 0)
                base_payload["confidence"] = "high" if top_pct >= 78 else "medium"
                base_payload["suggest_feedback"] = False
                base_payload["contacts"] = []
                base_payload["advice"] = answer if answer and top_pct >= 70 else ""
                if second_pass and not base_payload["advice"]:
                    base_payload["advice"] = "已用相近说法重新匹配，请查看下列结果。"
            else:
                return self._miss_payload(original_query, base_payload)
        else:
            kept = _sort_by_match_percent(results)[:limit]
            if not _results_cover_query(original_query, kept):
                return self._miss_payload(original_query, base_payload)
            base_payload["results"] = kept
            base_payload["llm_used"] = False
            base_payload["suggest_feedback"] = False
            base_payload["contacts"] = []
            for item in base_payload["results"]:
                pct = float(item.get("match_percent") or 0)
                item["match_hint"] = f"匹配度 {pct:.1f}%"
        return base_payload

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


def _merge_result_lists(lists: list[list[dict[str, Any]]], *, cap: int = 40) -> list[dict[str, Any]]:
    """多路检索结果按 id 合并：保留最高分，多路命中略加权。"""
    by_id: dict[str, dict[str, Any]] = {}
    hit_count: dict[str, int] = {}
    for results in lists:
        for item in results:
            rid = str(item.get("id") or "")
            if not rid:
                continue
            hit_count[rid] = hit_count.get(rid, 0) + 1
            prev = by_id.get(rid)
            score = float(item.get("score") or 0)
            if not prev or score > float(prev.get("score") or 0):
                by_id[rid] = dict(item)
    merged: list[dict[str, Any]] = []
    for rid, item in by_id.items():
        if hit_count.get(rid, 0) > 1:
            item["score"] = round(float(item.get("score") or 0) * 1.06, 4)
        merged.append(item)
    merged.sort(
        key=lambda x: (
            float(x.get("rrf_score") or 0),
            float(x.get("match_percent") or 0),
            float(x.get("score") or 0),
        ),
        reverse=True,
    )
    return merged[:cap]


def _results_cover_query(query: str, results: list[dict[str, Any]]) -> bool:
    """关键业务词至少命中一部分；界面词/同义词扩展不参与，避免口语长句被误杀。"""
    if not results:
        return False
    top = results[0]
    if top.get("exact_title"):
        return True

    try:
        vec_score = float(top.get("vector_score") or 0)
    except (TypeError, ValueError):
        vec_score = 0.0

    core = [t for t in gate_query_tokens(query) if len(t) >= 3] or gate_query_tokens(query)
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
    ratio = hits / max(len(core), 1)
    lexical_ok = (not core) or (hits >= 1 and ratio >= 0.28)

    # 向量很高也要有一点字面交集；否则当误召回
    if top.get("vector_hit") and vec_score >= 0.55 and lexical_ok:
        return True
    if top.get("vector_hit") and vec_score >= 0.45 and lexical_ok and ratio >= 0.4:
        return True
    if not core:
        return True
    if len(core) == 1:
        return hits >= 1
    # 长问法更宽松：至少命中 2 个实质词，或比例不太离谱
    if hits >= 2 and ratio >= 0.28:
        return True
    return ratio >= 0.34


def _sort_by_match_percent(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """展示顺序与匹配度一致：分数高的在前，供前端「最佳」标记。"""
    return sorted(
        results,
        key=lambda x: (
            float(x.get("match_percent") or 0),
            float(x.get("rrf_score") or 0),
            float(x.get("vector_score") or 0),
            1 if x.get("llm_ranked") else 0,
        ),
        reverse=True,
    )


def _fuse_ranks(
    query: str,
    lexical: list[dict[str, Any]],
    llm_items: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """关键词召回与模型打分融合；以语义（模型）为主，避免字面分虚高。"""
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
        lexical_strong = (
            bool(item.get("exact_title"))
            or overlap >= 2
            or (item.get("vector_hit") and float(item.get("vector_score") or 0) >= 0.48)
        )
        bm25 = float(item.get("match_percent") or 0)
        if llm_item:
            llm_pct = float(llm_item.get("match_percent") or 0)
            # 语义为主：模型分决定上限，关键词只作微调
            score = llm_pct * 0.88 + bm25 * 0.12
            if item.get("exact_title"):
                score = max(score, 96.0)
            else:
                # 模型本身不高时，不让关键词把分数抬上去
                if llm_pct < 60:
                    score = min(score, llm_pct + 4)
                # 非精确标题保守封顶
                score = min(score, 92.0 if lexical_strong else 86.0)
        else:
            # 模型未选中：仅保留精确标题或较强向量，且压低展示分
            if item.get("exact_title"):
                score = bm25
            else:
                vec = float(item.get("vector_score") or 0)
                score = min(bm25, 35.0 + vec * 50.0, 78.0)
        cloned = dict(item)
        cloned["match_percent"] = round(min(100.0, max(0.0, score)), 1)
        cloned["llm_ranked"] = bool(llm_item)
        fused.append(cloned)
    fused.sort(
        key=lambda x: (float(x.get("match_percent") or 0), 1 if x.get("llm_ranked") else 0),
        reverse=True,
    )
    return fused[:limit]
