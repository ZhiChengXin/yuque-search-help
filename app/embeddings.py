from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("yuque-help-search")


def _content_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def parent_section_id(chunk_id: str) -> str:
    """子块 id（sec#c0）还原为章节 id。"""
    cid = (chunk_id or "").strip()
    if "#c" in cid:
        return cid.rsplit("#c", 1)[0]
    return cid


def split_body_chunks(
    body: str,
    *,
    size: int = 520,
    overlap: int = 90,
    max_chunks: int = 4,
) -> list[str]:
    """长正文按重叠窗口切分，避免一整章糊成一个向量。"""
    text = (body or "").strip()
    if not text:
        return [""]
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text) and len(chunks) < max_chunks:
        end = min(len(text), start + size)
        if end < len(text):
            br = text.rfind("\n", start + size // 2, end)
            if br > start:
                end = br
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks or [text[:size]]


def build_section_embed_items(
    *,
    section_id: str,
    title: str,
    content: str,
    answer: str = "",
    doc_title: str = "",
    catalog_path: list[str] | None = None,
) -> list[tuple[str, str]]:
    """章节 → (chunk_id, text)；长文多块重叠，提升语义命中。"""
    sid = (section_id or "").strip()
    title = (title or "").strip()
    doc = (doc_title or "").strip()
    path = " / ".join(p.strip() for p in (catalog_path or []) if (p or "").strip())
    ans = (answer or "").strip()
    body = (content or "").strip()
    head_parts = [p for p in (path, doc, title) if p]
    head = "\n".join(dict.fromkeys(head_parts))
    if ans:
        body = f"问：{title}\n答：{ans}\n{body}"
    pieces = split_body_chunks(body)
    out: list[tuple[str, str]] = []
    for i, piece in enumerate(pieces):
        text = f"{head}\n{piece}".strip()
        if len(text) < 4:
            continue
        cid = sid if len(pieces) == 1 else f"{sid}#c{i}"
        out.append((cid, text[:1800]))
    return out


def embed_text_for_section(
    title: str,
    content: str,
    answer: str = "",
    *,
    doc_title: str = "",
    catalog_path: list[str] | None = None,
) -> str:
    """兼容旧调用：返回首块文本。"""
    items = build_section_embed_items(
        section_id="_",
        title=title,
        content=content,
        answer=answer,
        doc_title=doc_title,
        catalog_path=catalog_path,
    )
    return items[0][1] if items else ""


class EmbeddingClient:
    """走 OpenRouter/OpenAI 兼容的 /embeddings 接口。"""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "https://openrouter.ai/api/v1").rstrip("/")
        self.model = model or "qwen/qwen3-embedding-4b"
        self._cache: dict[str, list[float]] = {}
        self._cache_lock = threading.Lock()
        self._cache_max = 256

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def embed_one(self, text: str, *, timeout: float = 6.0) -> list[float]:
        text = (text or "").strip() or " "
        key = f"{self.model}::{text[:2000]}"
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit is not None:
                return hit
        vec = self.embed_batch([text], timeout=timeout)[0]
        with self._cache_lock:
            if len(self._cache) >= self._cache_max:
                # 丢掉最早一半
                for old in list(self._cache.keys())[: self._cache_max // 2]:
                    self._cache.pop(old, None)
            self._cache[key] = vec
        return vec

    def embed_batch(self, texts: list[str], *, timeout: float = 8.0) -> list[list[float]]:
        if not texts:
            return []
        if not self.enabled:
            raise RuntimeError("embedding 未配置 API Key")
        # 先尽量走缓存
        cleaned = [(t or " ").strip()[:2000] or " " for t in texts]
        results: list[list[float] | None] = [None] * len(cleaned)
        missing_idx: list[int] = []
        missing_texts: list[str] = []
        with self._cache_lock:
            for i, text in enumerate(cleaned):
                key = f"{self.model}::{text}"
                hit = self._cache.get(key)
                if hit is not None:
                    results[i] = hit
                else:
                    missing_idx.append(i)
                    missing_texts.append(text)
        if missing_texts:
            url = f"{self.base_url}/embeddings"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://local.fumeng-help-search",
                "X-Title": "Fumeng MX Help Search",
            }
            body = {"model": self.model, "input": missing_texts}
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, headers=headers, json=body)
            if resp.status_code != 200:
                raise RuntimeError(f"Embedding HTTP {resp.status_code}: {resp.text[:240]}")
            data = resp.json().get("data") or []
            data = sorted(data, key=lambda x: int(x.get("index") or 0))
            with self._cache_lock:
                for offset, row in enumerate(data):
                    emb = [float(v) for v in (row.get("embedding") or [])]
                    pos = missing_idx[offset]
                    results[pos] = emb
                    key = f"{self.model}::{missing_texts[offset]}"
                    if len(self._cache) >= self._cache_max:
                        for old in list(self._cache.keys())[: self._cache_max // 2]:
                            self._cache.pop(old, None)
                    self._cache[key] = emb
        return [v or [] for v in results]


class LocalVectorIndex:
    """本地文件向量索引：语雀仍是原文，这里只存搜索用向量缓存。"""

    def __init__(self, data_dir: Path, client: EmbeddingClient) -> None:
        self.client = client
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.dir / "meta.json"
        self._lock = threading.RLock()
        self.model = client.model
        self.ids: list[str] = []
        self.hashes: list[str] = []
        self.vectors: list[list[float]] = []
        self._load()

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    @property
    def size(self) -> int:
        return len(self.ids)

    def _load(self) -> None:
        if not self.meta_path.exists():
            return
        try:
            payload = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("本地向量索引损坏，将重建")
            return
        if (payload.get("model") or "") != self.model:
            logger.info("Embedding 模型变更，将重建本地向量索引")
            return
        self.ids = list(payload.get("ids") or [])
        self.hashes = list(payload.get("hashes") or [])
        self.vectors = list(payload.get("vectors") or [])
        if not (len(self.ids) == len(self.hashes) == len(self.vectors)):
            self.ids, self.hashes, self.vectors = [], [], []

    def _save(self) -> None:
        payload = {
            "model": self.model,
            "count": len(self.ids),
            "ids": self.ids,
            "hashes": self.hashes,
            "vectors": self.vectors,
        }
        tmp = self.meta_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.meta_path)

    def rebuild(self, sections: list[Any], *, batch_size: int = 32) -> dict[str, int]:
        """按章节增量更新：内容 hash 未变则复用旧向量。"""
        if not self.enabled:
            return {"total": 0, "embedded": 0, "reused": 0}

        items: list[tuple[str, str, str]] = []
        for sec in sections:
            kind = getattr(sec, "kind", "") or ""
            if kind == "contact_row":
                continue
            sid = getattr(sec, "id", "") or ""
            if not sid:
                continue
            for cid, text in build_section_embed_items(
                section_id=sid,
                title=getattr(sec, "section_title", "") or "",
                content=getattr(sec, "content", "") or "",
                answer=getattr(sec, "answer", "") or "",
                doc_title=getattr(sec, "doc_title", "") or "",
                catalog_path=list(getattr(sec, "catalog_path", None) or []),
            ):
                items.append((cid, _content_hash(text), text))

        old = {i: (h, v) for i, h, v in zip(self.ids, self.hashes, self.vectors)}
        new_ids: list[str] = []
        new_hashes: list[str] = []
        new_vectors: list[list[float]] = []
        pending_idx: list[int] = []
        pending_texts: list[str] = []
        reused = 0

        for sid, h, text in items:
            cached = old.get(sid)
            if cached and cached[0] == h and cached[1]:
                new_ids.append(sid)
                new_hashes.append(h)
                new_vectors.append(cached[1])
                reused += 1
            else:
                pending_idx.append(len(new_ids))
                pending_texts.append(text)
                new_ids.append(sid)
                new_hashes.append(h)
                new_vectors.append([])

        embedded = 0
        failed = 0
        for start in range(0, len(pending_texts), batch_size):
            chunk = pending_texts[start : start + batch_size]
            try:
                vectors = self.client.embed_batch(chunk, timeout=30.0)
            except Exception as exc:
                failed += len(chunk)
                logger.exception("向量化失败，本批跳过：%s", exc)
                continue
            for offset, vec in enumerate(vectors):
                pos = pending_idx[start + offset]
                new_vectors[pos] = vec
                embedded += 1
        if failed and not embedded:
            logger.error("本地向量索引未写入任何向量（失败 %s 条）", failed)

        # 丢掉向量化失败的空行
        keep_ids, keep_hashes, keep_vectors = [], [], []
        for sid, h, vec in zip(new_ids, new_hashes, new_vectors):
            if vec:
                keep_ids.append(sid)
                keep_hashes.append(h)
                keep_vectors.append(vec)

        with self._lock:
            self.ids = keep_ids
            self.hashes = keep_hashes
            self.vectors = keep_vectors
            self._save()

        logger.info(
            "本地向量索引更新：共 %s，新算 %s，复用 %s",
            len(keep_ids),
            embedded,
            reused,
        )
        return {"total": len(keep_ids), "embedded": embedded, "reused": reused}

    def remove_document(self, *, doc_id: str = "", slug: str = "") -> int:
        """删除某文档相关向量（section id 以 doc_id: 开头，或含 slug）。"""
        if not self.enabled:
            return 0
        doc_id = str(doc_id or "").strip()
        slug = (slug or "").strip()
        if not doc_id and not slug:
            return 0
        prefixes = []
        if doc_id:
            prefixes.append(f"{doc_id}:")
        removed = 0
        with self._lock:
            keep_ids, keep_hashes, keep_vectors = [], [], []
            for sid, h, vec in zip(self.ids, self.hashes, self.vectors):
                drop = False
                if any(sid.startswith(p) for p in prefixes):
                    drop = True
                elif slug and (f":{slug}" in sid or sid.startswith(slug)):
                    drop = True
                if drop:
                    removed += 1
                    continue
                keep_ids.append(sid)
                keep_hashes.append(h)
                keep_vectors.append(vec)
            self.ids = keep_ids
            self.hashes = keep_hashes
            self.vectors = keep_vectors
            self._save()
        return removed

    def upsert_sections(self, sections: list[Any], *, batch_size: int = 32) -> dict[str, int]:
        """增量写入若干章节向量：先按文档清旧再合并。"""
        if not self.enabled:
            return {"total": self.size, "embedded": 0, "reused": 0}
        doc_ids = {str(getattr(s, "doc_id", "") or "") for s in sections}
        slugs = {str(getattr(s, "slug", "") or "") for s in sections}
        for did in doc_ids:
            if did:
                self.remove_document(doc_id=did)
        for slug in slugs:
            if slug:
                self.remove_document(slug=slug)

        # 在现有全量基础上追加新章节：复用 rebuild 的 hash 逻辑
        # 构造临时「全量 = 旧保留 + 新 sections」——remove 已清旧，直接对「当前 ids + 新 sections」rebuild 过重
        # 更简单：把新 sections 与现存 ids 对应的假 sections 无法拿到；改为只 embed 新 sections 再 append
        items: list[tuple[str, str, str]] = []
        for sec in sections:
            kind = getattr(sec, "kind", "") or ""
            if kind == "contact_row":
                continue
            sid = getattr(sec, "id", "") or ""
            if not sid:
                continue
            for cid, text in build_section_embed_items(
                section_id=sid,
                title=getattr(sec, "section_title", "") or "",
                content=getattr(sec, "content", "") or "",
                answer=getattr(sec, "answer", "") or "",
                doc_title=getattr(sec, "doc_title", "") or "",
                catalog_path=list(getattr(sec, "catalog_path", None) or []),
            ):
                items.append((cid, _content_hash(text), text))

        if not items:
            return {"total": self.size, "embedded": 0, "reused": 0}

        embedded = 0
        new_ids: list[str] = []
        new_hashes: list[str] = []
        new_vectors: list[list[float]] = []
        texts = [t for _, _, t in items]
        for start in range(0, len(texts), batch_size):
            chunk = texts[start : start + batch_size]
            try:
                vectors = self.client.embed_batch(chunk, timeout=30.0)
            except Exception:
                logger.exception("增量向量化失败")
                continue
            for offset, vec in enumerate(vectors):
                if not vec:
                    continue
                i = start + offset
                new_ids.append(items[i][0])
                new_hashes.append(items[i][1])
                new_vectors.append(vec)
                embedded += 1

        with self._lock:
            self.ids.extend(new_ids)
            self.hashes.extend(new_hashes)
            self.vectors.extend(new_vectors)
            self._save()
        return {"total": self.size, "embedded": embedded, "reused": 0}

    def search(self, query: str, top_k: int = 12, min_score: float = 0.28) -> list[tuple[str, float]]:
        q = (query or "").strip()
        if not self.enabled or not q or not self.vectors:
            return []
        try:
            q_vec = self.client.embed_one(q)
        except Exception:
            logger.exception("查询向量化失败")
            return []
        best: dict[str, float] = {}
        with self._lock:
            for sid, vec in zip(self.ids, self.vectors):
                score = _cosine(q_vec, vec)
                if score < min_score:
                    continue
                parent = parent_section_id(sid)
                prev = best.get(parent)
                if prev is None or score > prev:
                    best[parent] = score
        ranked = sorted(best.items(), key=lambda x: x[1], reverse=True)
        return ranked[: max(1, top_k)]
