from __future__ import annotations

import random
import re
from typing import Any

import jieba
from rank_bm25 import BM25Okapi

from .models import ContactRecord, DocSection

CUSTOM_WORDS = [
    "孚盟邮",
    "孚盟邮件",
    "操作手册",
    "问题排查",
    "特定邮件",
    "网页崩溃",
    "业务线",
    "模块分工",
    "一对一群发",
    "千里眼",
    "自动回复",
]

for word in CUSTOM_WORDS:
    jieba.add_word(word)

STOPWORDS = {
    "怎么",
    "如何",
    "什么",
    "一个",
    "一下",
    "这个",
    "那个",
    "可以",
    "需要",
    "进行",
    "相关",
    "问题",
    "以及",
    "或者",
    "我们",
    "你们",
    "他们",
    "如果",
    "还有",
    "没有",
}


def tokenize(text: str) -> list[str]:
    text = (text or "").strip().lower()
    if not text:
        return []
    text = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", " ", text)
    tokens: list[str] = []
    for part in text.split():
        if re.fullmatch(r"[A-Za-z0-9]+", part):
            tokens.append(part)
            continue
        tokens.extend(t for t in jieba.lcut_for_search(part) if t.strip())
    result = []
    seen = set()
    for t in tokens:
        if t in STOPWORDS or len(t.strip()) == 0:
            continue
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result


def make_snippet(text: str, query: str, radius: int = 70) -> str:
    preview = make_preview(text, query, max_chars=radius * 2)
    return preview["text"]


def make_preview(text: str, query: str, max_chars: int = 500) -> dict[str, Any]:
    """生成结果预览：不超过 max_chars 全量展示；超过则在完整句/行处截断。"""
    source = (text or "").strip()
    if not source:
        return {"text": "", "truncated": False}

    compact = re.sub(r"[ \t]+", " ", source)
    compact = re.sub(r"\n{3,}", "\n\n", compact).strip()
    if len(compact) <= max_chars:
        return {"text": compact, "truncated": False}

    q = (query or "").strip()
    lower = compact.lower()
    idx = lower.find(q.lower()) if q else -1
    if idx < 0:
        for token in tokenize(query):
            idx = lower.find(token.lower())
            if idx >= 0:
                break

    if idx < 0:
        window = compact[:max_chars]
        prefix = ""
    else:
        left = max(0, idx - max_chars // 5)
        right = min(len(compact), left + max_chars)
        left = max(0, right - max_chars)
        window = compact[left:right]
        prefix = "…" if left > 0 else ""

    # 尽量在换行/句号处截断，避免半截句子贴在图片上方
    break_at = -1
    for sep in ("\n", "。", "！", "？", "；", ".", ";", " "):
        pos = window.rfind(sep)
        if pos >= max(40, len(window) // 3):
            break_at = max(break_at, pos + (0 if sep == "\n" else 1))
    if break_at > 0:
        window = window[:break_at].rstrip("，,、 \t")
    else:
        window = window.rstrip("，,、 \t\n")

    suffix = "…"
    return {"text": f"{prefix}{window}{suffix}", "truncated": True}


def normalize_title(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"^q[:：\s]+", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def titles_exact_match(query: str, title: str) -> bool:
    q = normalize_title(query)
    t = normalize_title(title)
    if not q or not t:
        return False
    if q == t:
        return True
    # 问句与「Q：问句」互为包含且覆盖充分
    if q in t or t in q:
        shorter, longer = (q, t) if len(q) <= len(t) else (t, q)
        return len(shorter) >= 8 and len(shorter) / max(len(longer), 1) >= 0.72
    return False


class SearchEngine:
    def __init__(self) -> None:
        self.sections: list[DocSection] = []
        self.contacts: list[ContactRecord] = []
        self._corpus_tokens: list[list[str]] = []
        self._bm25: BM25Okapi | None = None

    def rebuild(self, sections: list[DocSection], contacts: list[ContactRecord]) -> None:
        # 联系人行单独展示，不混进正文检索主结果（避免噪声）
        self.sections = [s for s in sections if s.kind != "contact_row"]
        self.contacts = contacts
        self._corpus_tokens = []
        for sec in self.sections:
            weighted = (
                f"{sec.section_title} {sec.section_title} {sec.section_title} "
                f"{sec.doc_title} {sec.doc_title} "
                f"{' '.join(sec.catalog_path)} {sec.content}"
            )
            self._corpus_tokens.append(tokenize(weighted))
        self._bm25 = BM25Okapi(self._corpus_tokens) if self._corpus_tokens else None

    def search(self, query: str, limit: int = 10, mode: str = "all") -> dict[str, Any]:
        query = (query or "").strip()
        mode = (mode or "all").strip().lower()
        if not query:
            return {
                "results": [],
                "contacts": [],
                "confidence": "none",
                "advice": "请输入问题或关键字",
            }

        # 模块负责人模式：只查分工表联系人
        if mode == "owner":
            contacts = self.match_contacts(query, limit=min(limit, 8))
            confidence = "high" if contacts else "none"
            advice = (
                "已匹配到对应模块负责人，可直接联系测试/产品。"
                if contacts
                else "未匹配到模块负责人，可换一级/二级菜单名再试，例如「孚盟邮」「孚盟邮件」。"
            )
            return {
                "results": [],
                "contacts": [c.as_dict() for c in contacts],
                "confidence": confidence,
                "advice": advice,
            }

        q_tokens = tokenize(query)
        query_lower = query.lower()
        results: list[dict[str, Any]] = []

        if self.sections and self._bm25 and q_tokens:
            scores = self._bm25.get_scores(q_tokens)
            ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
            for idx, score in ranked:
                if len(results) >= limit:
                    break
                sec = self.sections[idx]
                title_l = sec.section_title.lower()
                doc_title_l = sec.doc_title.lower()
                content_l = sec.searchable_text.lower()

                bonus = 0.0
                exact_section = titles_exact_match(query, sec.section_title)
                near_section = (
                    not exact_section
                    and (
                        query_lower in title_l
                        or title_l in query_lower
                        or normalize_title(query) in normalize_title(sec.section_title)
                    )
                )
                exact_doc = query_lower in doc_title_l
                if exact_section:
                    bonus += 50.0
                elif near_section:
                    bonus += 28.0
                elif exact_doc:
                    bonus += 10.0
                elif any(t in title_l for t in q_tokens if len(t) > 1):
                    bonus += 6.0

                if query_lower in content_l:
                    bonus += 3.0

                hit_tokens = [t for t in q_tokens if t in content_l]
                coverage = len(hit_tokens) / max(len(q_tokens), 1)
                bonus += coverage * 4.0

                # 正文充实、带图的章节优先，避免空壳标题霸榜
                content_len = len(sec.content or "")
                if content_len >= 120:
                    bonus += 8.0
                elif content_len >= 60:
                    bonus += 4.0
                if sec.images:
                    bonus += 5.0 + min(len(sec.images), 3) * 0.8
                if content_len < 24 and (exact_section or near_section):
                    bonus -= 18.0
                # 过宽的目录型标题略降权，优先具体操作章节
                title = sec.section_title or ""
                if title in ("功能说明：", "功能说明", "操作步骤", "概述", "简介") or title.endswith("基本设置"):
                    bonus -= 6.0

                final_score = float(score) + bonus
                # 精确标题永远保留；弱匹配过滤
                if exact_section or near_section:
                    pass
                elif final_score < 1.2 or coverage < 0.45:
                    # 至少要有一个长度>=2 的实质词命中
                    meaningful = [t for t in hit_tokens if len(t) >= 2]
                    if len(meaningful) < max(1, int(len(q_tokens) * 0.34)):
                        continue
                    if final_score < 2.5 and not meaningful:
                        continue
                    if final_score < 3.5 and coverage < 0.5 and not (
                        any(t in title_l for t in meaningful)
                    ):
                        continue

                snippet_source = sec.content or sec.section_title
                preview = make_preview(snippet_source, query, max_chars=500)
                images = list(sec.images or [])[:2]
                results.append(
                    {
                        "id": sec.id,
                        "title": sec.section_title,
                        "doc_title": sec.doc_title,
                        "url": sec.url,
                        "doc_url": sec.doc_url,
                        "snippet": preview["text"],
                        "preview": preview["text"],
                        "truncated": bool(preview["truncated"] or len(sec.images or []) > 2),
                        "images": images,
                        "image_count": len(sec.images or []),
                        "catalog_path": sec.catalog_path,
                        "updated_at": sec.updated_at,
                        "score": round(final_score, 4),
                        "match_hint": self._match_hint(
                            sec, query_lower, hit_tokens, exact_section or near_section
                        ),
                        "has_anchor": bool(sec.section_anchor),
                        "kind": sec.kind,
                        "exact_title": exact_section,
                    }
                )

            results.sort(key=lambda x: x["score"], reverse=True)
            # 二次门禁：若最高分仍偏低且无标题命中，视为未命中文档
            if results:
                top = results[0]
                top_title = (top.get("title") or "").lower()
                strong = (
                    top.get("exact_title")
                    or query_lower in top_title
                    or top_title in query_lower
                    or top.get("score", 0) >= 8
                    or "章节标题命中" in (top.get("match_hint") or "")
                )
                meaningful_q = [t for t in q_tokens if len(t) >= 2]
                top_blob = f"{top_title} {(top.get('snippet') or '').lower()}"
                hit_ratio = (
                    sum(1 for t in meaningful_q if t in top_blob) / max(len(meaningful_q), 1)
                )
                rare_miss = any(
                    len(t) >= 4 and t not in top_blob and not any(
                        t in (r.get("title") or "").lower()
                        or t in (r.get("snippet") or "").lower()
                        for r in results[:3]
                    )
                    for t in meaningful_q
                )
                if rare_miss or (not strong and (top.get("score", 0) < 6 or hit_ratio < 0.5)):
                    results = []

            # 已精确命中标题：只保留准确结果，不再附带一堆相关项
            if results and results[0].get("exact_title"):
                results = [r for r in results if r.get("exact_title")][:1]

        # 精确命中时不返回联系人；仅在未命中/低置信度时兜底
        confidence = self._confidence(results, query_lower)
        contacts: list[ContactRecord] = []
        if confidence in ("none", "low") or not results:
            contacts = self.match_contacts(query, limit=5)
        advice = self._advice(confidence, results, contacts)

        return {
            "results": results,
            "contacts": [c.as_dict() for c in contacts],
            "confidence": confidence,
            "advice": advice,
        }

    def match_contacts(self, query: str, limit: int = 5) -> list[ContactRecord]:
        if not self.contacts:
            return []
        q = query.strip().lower()
        if not q:
            return []
        q_tokens = tokenize(query)
        scored: list[tuple[float, ContactRecord, str]] = []

        for c in self.contacts:
            menu = (c.menu or "").strip().lower()
            module = (c.module or "").strip().lower()
            business = (c.business_line or "").strip().lower()
            blob = " ".join(
                [menu, module, business, c.testers or "", c.product_owner or ""]
            ).lower()
            score = 0.0
            match_level = "weak"

            if q == module:
                score += 100
                match_level = "exact_module"
            elif q == menu:
                score += 90
                match_level = "exact_menu"
            elif module.startswith(q) or menu.startswith(q):
                score += 45
                match_level = "prefix"
            elif q in module or q in menu:
                score += 30
                match_level = "contains"
            elif module in q or menu in q:
                # 查询更长时的反向包含，弱于精确
                score += 18
                match_level = "partial"
            else:
                if q in blob:
                    score += 6
                if business and (q in business or business in q):
                    score += 8
                for t in q_tokens:
                    if len(t) > 1 and t in blob:
                        score += 1.5

            if score > 0:
                scored.append((score, c, match_level))

        scored.sort(key=lambda x: x[0], reverse=True)
        if not scored:
            return []

        # 有精确模块/菜单命中时，只保留精确项
        top_level = scored[0][2]
        if top_level in ("exact_module", "exact_menu"):
            scored = [x for x in scored if x[2] == top_level]

        seen = set()
        result: list[ContactRecord] = []
        for _score, c, _level in scored:
            key = (c.module, c.testers, c.product_owner, c.menu)
            if key in seen:
                continue
            seen.add(key)
            result.append(c)
            if len(result) >= limit:
                break
        return result

    def _match_hint(
        self,
        sec: DocSection,
        query_lower: str,
        hit_tokens: list[str],
        exact_section: bool,
    ) -> str:
        if exact_section or titles_exact_match(query_lower, sec.section_title):
            return "章节标题命中 · 可直达定位"
        if query_lower and query_lower in sec.doc_title.lower():
            return "文档标题命中"
        if hit_tokens:
            return f"正文命中：{'、'.join(hit_tokens[:4])}"
        return "相关推荐"

    def _confidence(self, results: list[dict[str, Any]], query_lower: str) -> str:
        if not results:
            return "none"
        top = results[0]
        if top.get("exact_title") or titles_exact_match(
            query_lower, top.get("title") or ""
        ):
            return "high"
        if top.get("has_anchor") and (
            query_lower in (top.get("title") or "").lower()
            or (top.get("title") or "").lower() in query_lower
        ):
            return "high"
        if top.get("score", 0) >= 12:
            return "high"
        if top.get("score", 0) >= 4:
            return "medium"
        return "low"

    def _advice(
        self,
        confidence: str,
        results: list[dict[str, Any]],
        contacts: list[ContactRecord],
    ) -> str:
        if confidence == "high":
            return "已定位到最相关章节，可直接打开语雀原文查看。"
        if confidence == "medium":
            if contacts:
                return "已找到相关文档；若仍不确定，可联系对应测试/产品确认。"
            return "已找到相关文档，建议打开原文核对细节。"
        if contacts:
            return "未找到足够准确的文档答案，建议联系对应业务线测试/产品，或查看相关文档。"
        if results:
            return "匹配度一般，建议换个说法再搜，或打开相关文档浏览。"
        return "暂未检索到匹配内容，请换关键词，或从业务线联系人获取帮助。"

    def suggest(self, mode: str = "all", limit: int = 6) -> list[dict[str, str]]:
        """从知识库随机抽取可点击的快捷问题。"""
        mode = (mode or "all").strip().lower()
        limit = max(1, min(int(limit or 6), 12))

        if mode == "owner":
            return self._suggest_contacts(limit)

        pool: list[tuple[str, str]] = []
        seen = set()
        for sec in self.sections:
            if not self._section_fits_mode(sec, mode):
                continue
            title = self._clean_suggest_title(sec.section_title)
            if not self._is_good_suggest_title(title):
                # 章节太短时退回文档标题
                title = self._clean_suggest_title(sec.doc_title)
                if not self._is_good_suggest_title(title):
                    continue
            key = normalize_title(title)
            if key in seen:
                continue
            seen.add(key)
            desc = self._suggest_desc(sec, title)
            pool.append((title, desc))

        if not pool:
            return []

        picks = random.sample(pool, k=min(limit, len(pool)))
        return [{"q": q, "desc": desc} for q, desc in picks]

    def _suggest_contacts(self, limit: int) -> list[dict[str, str]]:
        pool: list[tuple[str, str]] = []
        seen = set()
        for c in self.contacts:
            name = (c.module or c.menu or "").strip()
            if not name or len(name) < 2:
                continue
            key = normalize_title(name)
            if key in seen:
                continue
            seen.add(key)
            menu = (c.menu or "").strip()
            if menu and menu != name:
                desc = f"查找「{name}」对接人（所属：{menu}）"
            else:
                desc = f"查找「{name}」模块测试与产品对接人"
            pool.append((name, desc))
        if not pool:
            return []
        picks = random.sample(pool, k=min(limit, len(pool)))
        return [{"q": q, "desc": desc} for q, desc in picks]

    @staticmethod
    def _section_fits_mode(sec: DocSection, mode: str) -> bool:
        if mode in ("", "all"):
            return True
        blob = "/".join(sec.catalog_path or []) + f"/{sec.doc_title or ''}"
        if mode == "faq":
            return bool(re.search(r"FAQ|排查|问答|Q&A|故障", blob, re.I))
        if mode == "manual":
            return bool(re.search(r"手册|指南|操作", blob))
        return True

    @staticmethod
    def _clean_suggest_title(title: str) -> str:
        text = (title or "").strip()
        text = re.sub(r"^Q[:：\s]+", "", text, flags=re.I)
        text = re.sub(r"\s+", " ", text).strip(" ·-—|/")
        return text

    @staticmethod
    def _is_good_suggest_title(title: str) -> bool:
        if not title:
            return False
        if len(title) < 4 or len(title) > 42:
            return False
        bad = ("目录", "概述", "简介", "前言", "附录", "更新日志", "分工表", "各业务线")
        if any(b in title for b in bad):
            return False
        return True

    @staticmethod
    def _suggest_desc(sec: DocSection, title: str) -> str:
        doc = (sec.doc_title or "").strip()
        path = " / ".join(sec.catalog_path or [])
        if doc and normalize_title(doc) != normalize_title(title):
            return f"来自《{doc}》"
        if path:
            return path[:36] + ("…" if len(path) > 36 else "")
        return "点击即可检索相关内容"

    def stats(self) -> dict[str, Any]:
        return {
            "section_count": len(self.sections),
            "contact_count": len(self.contacts),
            "ready": self._bm25 is not None,
        }
