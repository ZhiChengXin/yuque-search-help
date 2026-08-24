from __future__ import annotations

import random
import re
from typing import Any

import jieba
from rank_bm25 import BM25Okapi

from .models import ContactRecord, DocSection, make_deep_url
from .synonyms import expand_terms


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

# 界面/场景描述词：常被用户顺口带上，文档标题未必出现，不能因此判未命中
CONTEXT_STOPWORDS = {
    "列表",
    "页面",
    "界面",
    "模块",
    "系统",
    "里面",
    "上面",
    "下面",
    "里边",
    "功能",
    "菜单",
    "入口",
    "按钮",
    "选项",
    "地方",
    "时候",
    "情况",
    "那边",
    "这边",
    "那里",
    "这里",
    "一下",
    "那种",
    "这种",
    "操作",
    "打开",
    "进入",
    "点击",
    "看到",
    "显示的",
    "里面的",
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


def soften_query(query: str) -> str:
    """口语叠字归一：转圈圈→转圈，加载中中→加载中。"""
    text = (query or "").strip()
    if not text:
        return ""
    return re.sub(r"([\u4e00-\u9fff])\1+", r"\1", text)


def base_query_tokens(query: str) -> list[str]:
    """原句分词（不含同义词扩展），用于门禁，避免扩展词误杀。"""
    seen = set()
    out: list[str] = []
    for source in (query, soften_query(query)):
        for t in tokenize(source):
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def gate_query_tokens(query: str) -> list[str]:
    """门禁/覆盖率用的实质词：原句分词，去掉停用词与界面场景词。"""
    return [
        t
        for t in base_query_tokens(query)
        if len(t) >= 2 and t not in STOPWORDS and t not in CONTEXT_STOPWORDS
    ]


def query_tokens(query: str) -> list[str]:
    """原句 + 口语归一 + 同义词扩展后的分词，提升召回。"""
    return expand_terms(base_query_tokens(query))


def _lexical_bridge(query: str, title: str, content: str = "", answer: str = "") -> bool:
    """向量命中时至少要有一点字面/同义词交集，避免天气类误召回。"""
    tokens = [
        t
        for t in query_tokens(query)
        if len(t) >= 2
        and t
        not in {
            "怎么",
            "如何",
            "什么",
            "怎样",
            "为什么",
            "可以",
            "进行",
            "相关",
            "一个",
            "一下",
            "这个",
            "那个",
            "今天",
            "怎么样",
            "怎样啊",
        }
    ]
    if not tokens:
        return False
    blob = soften_query(
        f"{title or ''} {answer or ''} {(content or '')[:400]}"
    ).lower()
    hits = sum(1 for t in tokens if soften_query(t).lower() in blob or t.lower() in blob)
    strong = [t for t in tokens if len(t) >= 3]
    if strong and any(soften_query(t).lower() in blob or t.lower() in blob for t in strong):
        return True
    return hits / max(len(tokens), 1) >= 0.18


def _rrf_fuse(
    bm25_ids: list[str],
    vector_ids: list[str],
    *,
    k: int = 60,
) -> dict[str, float]:
    """Reciprocal Rank Fusion：多路召回免调参融合。"""
    scores: dict[str, float] = {}
    for rank, sid in enumerate(bm25_ids):
        if not sid:
            continue
        scores[sid] = scores.get(sid, 0.0) + 1.0 / (k + rank + 1)
    for rank, sid in enumerate(vector_ids):
        if not sid:
            continue
        scores[sid] = scores.get(sid, 0.0) + 1.0 / (k + rank + 1)
    return scores


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


def wants_owner_contacts(query: str) -> bool:
    """用户是否在主动找测试/产品负责人（不是 CRM 里的「创建联系人」功能）。"""
    text = (query or "").strip()
    if not text:
        return False
    # CRM 功能：创建/新建联系人 → 不当作找对接人
    if re.search(
        r"(创建|新建|新增|添加|编辑|修改|删除|导入|导出).{0,8}联系人|"
        r"联系人.{0,8}(创建|新建|新增|添加|编辑|怎么|如何|步骤)",
        text,
    ):
        return False
    # 明确找人对接
    if re.search(
        r"(负责人|对接人|联系方式|谁负责|找谁|联系谁|对接谁|测试是谁|产品是谁)",
        text,
    ):
        return True
    if re.search(r"(测试|产品).{0,6}(是谁|谁啊|找谁)", text):
        return True
    # 「联系人是谁」这类才算；单说联系人不够
    if re.search(r"联系人.{0,4}(是谁|谁啊|找谁|对接)", text):
        return True
    return False



def looks_like_doc_question(query: str) -> bool:
    """是否更像在问操作/文档，而不是查模块名。"""
    text = (query or "").strip()
    if not text:
        return False
    if wants_owner_contacts(text):
        return False
    # 短词更像菜单/模块名
    if len(text) <= 6 and not re.search(r"[？?怎么如何]", text):
        return False
    return bool(
        re.search(
            r"(怎么|如何|怎样|咋|设置|配置|操作|步骤|文档|手册|教程|指南|"
            r"为什么|为啥|无法|失败|报错|开通|绑定|查看|有没有|吗|呢|？|\?)",
            text,
        )
    )


def extract_answer_from_content(content: str) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    match = re.search(r"A[:：]\s*(.+?)(?=(?:\n\s*Q[:：])|$)", text, re.IGNORECASE | re.DOTALL)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip(" ；;，,")[:500]
    numbered = re.match(r"^Q\d+\s*[:：]\s*.+$", text, re.IGNORECASE | re.DOTALL)
    if numbered:
        body = re.sub(r"^Q\d+\s*[:：]\s*[^\n]+\n?", "", text, count=1, flags=re.IGNORECASE).strip()
        if body:
            return re.sub(r"\s+", " ", body).strip(" ；;，,")[:500]
    return ""


class SearchEngine:
    def __init__(self) -> None:
        self.sections: list[DocSection] = []
        self.contacts: list[ContactRecord] = []
        self._corpus_tokens: list[list[str]] = []
        self._bm25: BM25Okapi | None = None
        self._by_id: dict[str, DocSection] = {}
        self.vector_index = None

    def set_vector_index(self, vector_index) -> None:
        self.vector_index = vector_index

    def rebuild(self, sections: list[DocSection], contacts: list[ContactRecord]) -> None:
        # 联系人行单独展示，不混进正文检索主结果（避免噪声）
        self.sections = [s for s in sections if s.kind != "contact_row"]
        self.contacts = contacts
        self._by_id = {s.id: s for s in self.sections}
        self._corpus_tokens = []
        for sec in self.sections:
            weighted = (
                f"{sec.section_title} {sec.section_title} {sec.section_title} "
                f"{sec.doc_title} {sec.doc_title} "
                f"{' '.join(sec.catalog_path)} {sec.content}"
            )
            self._corpus_tokens.append(tokenize(weighted))
        self._bm25 = BM25Okapi(self._corpus_tokens) if self._corpus_tokens else None

    def search(
        self,
        query: str,
        limit: int = 10,
        mode: str = "all",
        *,
        keep_weak: bool = False,
        retrieve_limit: int | None = None,
        skip_vector: bool = False,
    ) -> dict[str, Any]:
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
            doc_like = looks_like_doc_question(query)
            if contacts:
                confidence = "medium" if doc_like else "high"
                advice = (
                    "当前是「模块负责人」模式，这里不查操作文档。"
                    "没找到对应文档，先给你推荐相关联系人，可以直接问测试/产品。"
                    if doc_like
                    else "已匹配到对应模块负责人，可直接联系测试/产品。"
                )
            else:
                confidence = "none"
                advice = (
                    "当前模式不查文档，也没匹配到相关联系人。"
                    "可以换一级/二级菜单名再试，或切到「智能检索」查操作说明。"
                    if doc_like
                    else "未匹配到模块负责人，可换一级/二级菜单名再试，例如「孚盟邮」「孚盟邮件」。"
                )
            return {
                "results": [],
                "contacts": [c.as_dict() for c in contacts],
                "confidence": confidence,
                "advice": advice,
                "owner_doc_hint": doc_like,
            }

        q_tokens = query_tokens(query)
        query_lower = query.lower()
        soft_query = soften_query(query)
        soft_lower = soft_query.lower()
        results: list[dict[str, Any]] = []
        pull = retrieve_limit or (max(limit, 16) if keep_weak else limit)

        if self.sections and self._bm25 and q_tokens:
            scores = self._bm25.get_scores(q_tokens)
            ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
            for idx, score in ranked:
                if len(results) >= pull:
                    break
                sec = self.sections[idx]
                title_l = sec.section_title.lower()
                doc_title_l = sec.doc_title.lower()
                content_l = sec.searchable_text.lower()

                bonus = 0.0
                exact_section = titles_exact_match(query, sec.section_title) or (
                    soft_query != query and titles_exact_match(soft_query, sec.section_title)
                )
                near_section = (
                    not exact_section
                    and (
                        query_lower in title_l
                        or soft_lower in title_l
                        or title_l in query_lower
                        or title_l in soft_lower
                        or normalize_title(query) in normalize_title(sec.section_title)
                        or normalize_title(soft_query) in normalize_title(sec.section_title)
                    )
                )
                exact_doc = query_lower in doc_title_l or soft_lower in doc_title_l
                if exact_section:
                    bonus += 50.0
                elif near_section:
                    bonus += 28.0
                elif exact_doc:
                    bonus += 10.0
                elif any(t in title_l for t in q_tokens if len(t) > 1):
                    bonus += 6.0

                if query_lower in content_l or soft_lower in content_l:
                    bonus += 3.0

                hit_tokens = [t for t in q_tokens if t in content_l]
                coverage = len(hit_tokens) / max(len(q_tokens), 1)
                bonus += coverage * 4.0

                # 门禁覆盖率：只用原句实质词，避免同义词扩展 +「列表/页面」把召回提前踢掉
                gate_toks = gate_query_tokens(query)
                gate_hits = [t for t in gate_toks if t in content_l]
                gate_coverage = len(gate_hits) / max(len(gate_toks), 1) if gate_toks else 1.0

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
                # 精确标题永远保留；弱匹配过滤（阈值放宽，让长口语问法先进候选池）
                if exact_section or near_section:
                    pass
                elif final_score < 1.0 or gate_coverage < 0.28:
                    meaningful = [t for t in gate_hits if len(t) >= 2]
                    if len(meaningful) < max(1, int(len(gate_toks) * 0.28)):
                        continue
                    if final_score < 2.0 and not meaningful:
                        continue
                    if final_score < 3.0 and gate_coverage < 0.4 and not (
                        any(t in title_l for t in meaningful)
                    ):
                        continue

                snippet_source = sec.content or sec.section_title
                answer = (sec.answer or "").strip() or extract_answer_from_content(sec.content)
                if answer and sec.kind == "qa":
                    preview = {"text": f"A：{answer}", "truncated": False}
                else:
                    preview = make_preview(snippet_source, query, max_chars=500)
                images = list(sec.images or [])[:2]
                match_percent = self._score_to_percent(
                    final_score, exact=exact_section, near=near_section
                )
                results.append(
                    {
                        "id": sec.id,
                        "title": sec.section_title,
                        "doc_title": sec.doc_title,
                        "url": make_deep_url(sec.doc_url, sec.section_title, sec.section_anchor),
                        "doc_url": sec.doc_url,
                        "snippet": preview["text"],
                        "preview": preview["text"],
                        "answer": answer,
                        "truncated": bool(preview["truncated"] or len(sec.images or []) > 2),
                        "images": images,
                        "image_count": len(sec.images or []),
                        "catalog_path": sec.catalog_path,
                        "updated_at": sec.updated_at,
                        "score": round(final_score, 4),
                        "match_percent": match_percent,
                        "match_hint": f"匹配度 {match_percent:.1f}%",
                        "has_anchor": bool(sec.section_anchor) or sec.kind == "qa",
                        "kind": sec.kind,
                        "exact_title": exact_section,
                    }
                )

            results.sort(key=lambda x: x["score"], reverse=True)
            if not keep_weak:
                # 二次门禁：不再「一刀切清空」；优先丢掉明显跑偏的，保留还能对上实质词的
                if results:
                    top = results[0]
                    top_title = (top.get("title") or "").lower()
                    strong = (
                        top.get("exact_title")
                        or query_lower in top_title
                        or top_title in query_lower
                        or top.get("score", 0) >= 8
                    )
                    meaningful_q = gate_query_tokens(query)

                    def _blob(item: dict[str, Any]) -> str:
                        return (
                            f"{(item.get('title') or '').lower()} "
                            f"{(item.get('snippet') or '').lower()} "
                            f"{(item.get('doc_title') or '').lower()} "
                            f"{' '.join(item.get('catalog_path') or []).lower()}"
                        )

                    top_blob = _blob(top)
                    hit_ratio = (
                        sum(1 for t in meaningful_q if t in top_blob) / max(len(meaningful_q), 1)
                    )
                    # 长词（>=4）若整份候选前几名都没有，才从候选里剔除「完全不含该词」的项
                    rare_terms = [
                        t
                        for t in meaningful_q
                        if len(t) >= 4
                        and not any(t in _blob(r) for r in results[:8])
                    ]
                    if rare_terms:
                        # 有罕见词全库都没命中 → 多半问法太偏；但若分数已经很高仍保留前几名供向量/上层兜底
                        if not strong or hit_ratio < 0.35:
                            results = []
                    elif not strong and top.get("score", 0) < 5 and hit_ratio < 0.35:
                        results = []
                    else:
                        # 顶名偏了但不至于全空：把覆盖更好的结果往前挪
                        results.sort(
                            key=lambda r: (
                                sum(1 for t in meaningful_q if t in _blob(r)),
                                float(r.get("score") or 0),
                            ),
                            reverse=True,
                        )

            results = results[:pull]
            self._normalize_match_percent(results)

        # 向量默认在锁外由 apply_vector_recall 执行，避免卡住整站
        if not skip_vector:
            results = self._merge_vector_hits(
                query, results, pull=max(pull, 16), keep_weak=keep_weak
            )

        # 主动问负责人：优先联系人，避免弱文档抢镜
        if wants_owner_contacts(query):
            contacts = self.match_contacts(query, limit=5)
            if contacts:
                strong_doc = bool(results and results[0].get("exact_title"))
                if not strong_doc:
                    return {
                        "results": [],
                        "contacts": [c.as_dict() for c in contacts],
                        "confidence": "high",
                        "advice": "已帮你找到相关模块的测试/产品负责人，可直接联系。",
                    }

        confidence = self._confidence(results, query_lower)
        contacts: list[ContactRecord] = []
        if wants_owner_contacts(query) and (confidence in ("none", "low") or not results):
            contacts = self.match_contacts(query, limit=5)
        advice = self._advice(confidence, results, contacts)

        return {
            "results": results,
            "contacts": [c.as_dict() for c in contacts],
            "confidence": confidence,
            "advice": advice,
        }

    def apply_vector_recall(
        self,
        query: str,
        payload: dict[str, Any],
        *,
        pull: int = 16,
        keep_weak: bool = False,
    ) -> dict[str, Any]:
        """在锁外合并向量召回，并处理「问负责人」短路。"""
        results = list(payload.get("results") or [])
        if results and results[0].get("exact_title"):
            return payload
        results = self._merge_vector_hits(
            query, results, pull=max(pull, 16), keep_weak=keep_weak
        )
        if wants_owner_contacts(query):
            contacts = self.match_contacts(query, limit=5)
            if contacts and not (results and results[0].get("exact_title")):
                payload["results"] = []
                payload["contacts"] = [c.as_dict() for c in contacts]
                payload["confidence"] = "high"
                payload["advice"] = "已帮你找到相关模块的测试/产品负责人，可直接联系。"
                return payload
        payload["results"] = results
        query_lower = (query or "").lower()
        payload["confidence"] = self._confidence(results, query_lower)
        if not payload.get("contacts"):
            payload["contacts"] = []
        if not payload.get("advice"):
            payload["advice"] = self._advice(
                payload.get("confidence") or "none", results, []
            )
        return payload

    def _merge_vector_hits(
        self,
        query: str,
        results: list[dict[str, Any]],
        *,
        pull: int,
        keep_weak: bool = False,
    ) -> list[dict[str, Any]]:
        vector_index = self.vector_index
        if vector_index is None or not getattr(vector_index, "enabled", False):
            return results
        soft = soften_query(query) or query
        min_score = 0.32 if keep_weak else 0.36
        try:
            hits = vector_index.search(
                soft, top_k=max(40, pull * 2), min_score=min_score
            )
        except Exception:
            return results
        if not hits:
            return results

        bm25_ids = [str(item.get("id") or "") for item in results if item.get("id")]
        bm25_set = set(bm25_ids)
        by_id = {item.get("id"): item for item in results}
        query_lower = (query or "").lower()
        soft_lower = soft.lower()
        vector_ids: list[str] = []

        for sid, sim in hits:
            sec = self._by_id.get(sid)
            if not sec:
                continue
            sim_f = float(sim)
            vector_ids.append(sid)
            existing = by_id.get(sid)
            if existing:
                if sim_f >= min_score:
                    vec_score = 4.0 + sim_f * 18.0
                    existing["score"] = round(
                        max(float(existing.get("score") or 0), vec_score), 4
                    )
                    existing["vector_score"] = round(sim_f, 4)
                    existing["vector_hit"] = True
                continue

            # 纯向量新进：高相似可放宽字面桥接
            if sim_f < 0.42:
                continue
            bridged = _lexical_bridge(query, sec.section_title, sec.content, sec.answer)
            if not bridged and sim_f < 0.55:
                continue
            if not keep_weak and sim_f < 0.48 and not bridged:
                continue

            exact_section = titles_exact_match(query, sec.section_title) or titles_exact_match(
                soft, sec.section_title
            )
            near_section = (
                not exact_section
                and (
                    query_lower in (sec.section_title or "").lower()
                    or soft_lower in (sec.section_title or "").lower()
                    or (sec.section_title or "").lower() in query_lower
                )
            )
            vec_score = 4.0 + sim_f * 18.0
            item = self._section_result(
                sec,
                query,
                score=vec_score,
                exact=exact_section,
                near=near_section,
            )
            item["vector_score"] = round(sim_f, 4)
            item["vector_hit"] = True
            by_id[sid] = item

        merged = list(by_id.values())
        cleaned: list[dict[str, Any]] = []
        for item in merged:
            if item.get("exact_title"):
                cleaned.append(item)
                continue
            if item.get("vector_hit") and not item.get("exact_title"):
                sec = self._by_id.get(item.get("id") or "")
                title = (item.get("title") or "")
                content = (sec.content if sec else "") or ""
                answer = (item.get("answer") or "")
                sim_f = float(item.get("vector_score") or 0)
                was_bm25 = (item.get("id") or "") in bm25_set
                if was_bm25:
                    cleaned.append(item)
                    continue
                if sim_f >= 0.55:
                    cleaned.append(item)
                    continue
                if sim_f >= 0.42 and _lexical_bridge(query, title, content, answer):
                    cleaned.append(item)
                continue
            cleaned.append(item)

        # RRF 融合 BM25 顺序与向量顺序
        rrf = _rrf_fuse(bm25_ids, vector_ids)
        for item in cleaned:
            rid = str(item.get("id") or "")
            item["rrf_score"] = round(rrf.get(rid, 0.0), 6)
            # 把 RRF 轻微并入内部 score，利于后续排序
            item["score"] = round(
                float(item.get("score") or 0) + float(item.get("rrf_score") or 0) * 40.0,
                4,
            )

        cleaned.sort(
            key=lambda x: (
                float(x.get("rrf_score") or 0),
                float(x.get("vector_score") or 0),
                float(x.get("score") or 0),
            ),
            reverse=True,
        )
        merged = cleaned[:pull]
        self._normalize_match_percent(merged)
        return merged

    def _section_result(
        self,
        sec: DocSection,
        query: str,
        *,
        score: float,
        exact: bool,
        near: bool,
    ) -> dict[str, Any]:
        answer = (sec.answer or "").strip() or extract_answer_from_content(sec.content)
        if answer and sec.kind == "qa":
            preview = {"text": f"A：{answer}", "truncated": False}
        else:
            preview = make_preview(sec.content or sec.section_title, query, max_chars=500)
        images = list(sec.images or [])[:2]
        match_percent = self._score_to_percent(score, exact=exact, near=near)
        return {
            "id": sec.id,
            "title": sec.section_title,
            "doc_title": sec.doc_title,
            "url": make_deep_url(sec.doc_url, sec.section_title, sec.section_anchor),
            "doc_url": sec.doc_url,
            "snippet": preview["text"],
            "preview": preview["text"],
            "answer": answer,
            "truncated": bool(preview["truncated"] or len(sec.images or []) > 2),
            "images": images,
            "image_count": len(sec.images or []),
            "catalog_path": sec.catalog_path,
            "updated_at": sec.updated_at,
            "score": round(score, 4),
            "match_percent": match_percent,
            "match_hint": f"匹配度 {match_percent:.1f}%",
            "has_anchor": bool(sec.section_anchor) or sec.kind == "qa",
            "kind": sec.kind,
            "exact_title": exact,
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
            return "匹配度 100.0%"
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
        # 已有结果卡时不额外提示「已定位…」
        if results:
            return ""
        if contacts:
            return "已找到可联系的人，可直接联系对应测试/产品确认。"
        return "暂未检索到匹配内容，请换关键词，或从业务线联系人获取帮助。"

    def suggest(self, mode: str = "all", limit: int = 6) -> list[dict[str, str]]:
        """从知识库随机抽取可点击的快捷问题。"""
        mode = (mode or "all").strip().lower()
        limit = max(1, min(int(limit or 6), 12))

        if mode == "owner":
            return self._suggest_contacts(limit)

        pool: list[dict[str, str]] = []
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
            pool.append(
                {
                    "q": title,
                    "desc": desc,
                    "id": sec.id,
                    "url": make_deep_url(sec.doc_url, sec.section_title, sec.section_anchor),
                    "kind": sec.kind,
                }
            )

        if not pool:
            return []

        return random.sample(pool, k=min(limit, len(pool)))

    def lookup(self, section_id: str) -> dict[str, Any] | None:
        """按章节 id 直接取一条结果，供推荐点击秒开。"""
        target = (section_id or "").strip()
        if not target:
            return None
        for sec in self.sections:
            if sec.id != target:
                continue
            answer = (sec.answer or "").strip() or extract_answer_from_content(sec.content)
            preview_text = f"A：{answer}" if answer and sec.kind == "qa" else (sec.content or "")[:500]
            return {
                "id": sec.id,
                "title": sec.section_title,
                "doc_title": sec.doc_title,
                "url": make_deep_url(sec.doc_url, sec.section_title, sec.section_anchor),
                "doc_url": sec.doc_url,
                "snippet": preview_text,
                "preview": preview_text,
                "answer": answer,
                "truncated": len(sec.content or "") > 500,
                "images": list(sec.images or [])[:2],
                "image_count": len(sec.images or []),
                "catalog_path": sec.catalog_path,
                "updated_at": sec.updated_at,
                "score": 100.0,
                "match_percent": 100.0,
                "match_hint": "匹配度 100.0%",
                "has_anchor": bool(sec.section_anchor) or sec.kind == "qa",
                "kind": sec.kind,
                "exact_title": True,
            }
        return None

    def _suggest_contacts(self, limit: int) -> list[dict[str, str]]:
        pool: list[dict[str, str]] = []
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
            testers = (c.testers or "").strip()
            product = (c.product_owner or "").strip()
            if testers or product:
                bits = []
                if testers:
                    bits.append(f"测试 {testers}")
                if product:
                    bits.append(f"产品 {product}")
                desc = " · ".join(bits)
            elif menu and menu != name:
                desc = f"所属：{menu}"
            else:
                desc = "查找模块测试与产品对接人"
            pool.append(
                {
                    "q": name,
                    "desc": desc,
                    "menu": menu,
                    "testers": testers,
                    "product_owner": product,
                    "product_contact": (c.product_contact or "").strip(),
                    "kind": "owner",
                }
            )
        if not pool:
            return []
        return random.sample(pool, k=min(limit, len(pool)))

    @staticmethod
    def _score_to_percent(score: float, *, exact: bool, near: bool) -> float:
        """关键词分 → 展示百分比。刻意保守：标题沾边不等于高置信。"""
        if exact:
            return 100.0
        if near:
            # 标题包含/被包含：最多约 78，避免动辄 96%
            return round(min(78.0, 50.0 + max(0.0, score) * 0.85), 1)
        mapped = 10.0 + max(0.0, score) * 2.8
        return round(min(66.0, mapped), 1)

    @staticmethod
    def _normalize_match_percent(results: list[dict[str, Any]]) -> None:
        """用向量语义校正百分比；不做「第一条人为抬分」。"""
        if not results:
            return
        for r in results:
            if r.get("exact_title"):
                r["match_percent"] = 100.0
                r["match_hint"] = "匹配度 100.0%"
                continue

            base = float(r.get("match_percent") or 0)
            vec = float(r.get("vector_score") or 0)
            if vec > 0:
                # cosine 约 0.30~0.85 → 语义分约 32~90
                sem = min(90.0, max(0.0, (vec - 0.30) / 0.55) * 78.0 + 32.0)
                pct = sem * 0.72 + base * 0.28
            else:
                # 无向量信号时再压一档，避免纯字面虚高
                pct = base * 0.82

            if vec and vec < 0.42:
                pct = min(pct, 78.0)
            elif not vec:
                pct = min(pct, 74.0)
            else:
                pct = min(pct, 90.0)

            r["match_percent"] = round(max(0.0, pct), 1)
            r["match_hint"] = f"匹配度 {r['match_percent']:.1f}%"

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
