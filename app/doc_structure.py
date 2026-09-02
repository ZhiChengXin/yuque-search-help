"""同步时自动识别文档结构、择优切分、并输出可检索性自检报告。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .models import (
    DocSection,
    HEADING_RE,
    NUMBERED_QA_MARKER_RE,
    PROBLEM_FAQ_MARKER_RE,
    QA_BLOCK_RE,
    markdown_or_html_to_text,
)

logger = logging.getLogger("yuque-help-search")


@dataclass
class DocumentSignals:
    """从原文/HTML 提取的结构信号，用于选择切分策略。"""

    text_len: int = 0
    html_heading_count: int = 0
    qa_block_count: int = 0
    numbered_q_count: int = 0
    problem_q_count: int = 0
    label_line_count: int = 0
    outline_line_count: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "text_len": self.text_len,
            "html_heading_count": self.html_heading_count,
            "qa_block_count": self.qa_block_count,
            "numbered_q_count": self.numbered_q_count,
            "problem_q_count": self.problem_q_count,
            "label_line_count": self.label_line_count,
            "outline_line_count": self.outline_line_count,
        }


@dataclass
class DocumentAudit:
    doc_id: str
    doc_title: str
    strategy: str
    score: float
    section_count: int
    whole_doc: bool
    signals: DocumentSignals
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "doc_title": self.doc_title,
            "strategy": self.strategy,
            "score": round(self.score, 1),
            "section_count": self.section_count,
            "whole_doc": self.whole_doc,
            "signals": self.signals.to_dict(),
            "warnings": self.warnings,
        }


LABEL_LINE_RE = re.compile(r"^.{4,42}[：:]\s*$", re.MULTILINE)
OUTLINE_PROBE_RE = re.compile(
    r"(?:^|\n)\s*(?:[一二三四五六七八九十百]+[、．.]|\d+[、．.]\s*\S|问题\d+[：:]|第\s*[一二三四五六七八九十\d]+\s*步)",
    re.MULTILINE,
)


def analyze_document_signals(raw: str, text: str | None = None) -> DocumentSignals:
    body = (text or markdown_or_html_to_text(raw)).strip()
    return DocumentSignals(
        text_len=len(body),
        html_heading_count=len(list(HEADING_RE.finditer(raw or ""))),
        qa_block_count=len(list(QA_BLOCK_RE.finditer("\n" + body))),
        numbered_q_count=len(list(NUMBERED_QA_MARKER_RE.finditer(body))),
        problem_q_count=len(list(PROBLEM_FAQ_MARKER_RE.finditer(body))),
        label_line_count=len(LABEL_LINE_RE.findall(body)),
        outline_line_count=len(OUTLINE_PROBE_RE.findall(body)),
    )


def score_sections(sections: list[DocSection], source_len: int) -> float:
    """切分质量分：条目多、标题清晰、少「整篇 doc」得高分。"""
    if not sections:
        return 0.0
    score = 0.0
    titles: set[str] = set()
    whole_docs = 0
    qa_count = 0

    for sec in sections:
        title = (sec.section_title or "").strip()
        is_whole = sec.kind == "doc" or (
            title == (sec.doc_title or "").strip() and len(sec.content or "") > max(400, source_len // 2)
        )
        if is_whole:
            whole_docs += 1
            score -= 35.0
            continue
        norm = re.sub(r"\s+", "", title.lower())
        if not norm or norm in titles:
            score -= 4.0
            continue
        titles.add(norm)
        score += 12.0
        if sec.kind == "qa":
            qa_count += 1
            score += 6.0
        if 4 <= len(title) <= 64:
            score += 3.0
        if (sec.answer or "").strip():
            score += 2.0
        if len(sec.content or "") >= 40:
            score += 1.5

    distinct = len(titles)
    if source_len > 500 and distinct >= 2:
        score += min(distinct * 2.5, 50.0)
    elif source_len > 800 and distinct <= 1:
        score -= 25.0

    if qa_count >= 3:
        score += min(qa_count * 1.5, 30.0)
    if whole_docs:
        score -= whole_docs * 15.0
    return score


def audit_document_sections(
    *,
    doc_id: str,
    doc_title: str,
    sections: list[DocSection],
    signals: DocumentSignals,
    strategy: str,
) -> DocumentAudit:
    score = score_sections(sections, signals.text_len)
    warnings: list[str] = []
    whole = any(
        s.kind == "doc"
        and len(s.content or "") > 400
        and (s.section_title or "").strip() == (s.doc_title or "").strip()
        for s in sections
    )
    distinct_titles = {
        re.sub(r"\s+", "", (s.section_title or "").lower())
        for s in sections
        if (s.section_title or "").strip() != (s.doc_title or "").strip()
    }

    if whole and signals.text_len > 500:
        warnings.append("仍保留整篇索引，长文档标题可能搜不准，建议用语雀标题或 Q/A 格式")
    if signals.text_len > 600 and len(distinct_titles) <= 1:
        warnings.append("正文较长但可检索条目过少，可能未识别文档结构")
    if signals.qa_block_count >= 3 and sum(1 for s in sections if s.kind == "qa") < signals.qa_block_count // 2:
        warnings.append("检测到大量 Q/A 文本，但入库问答条数偏少")
    if signals.html_heading_count >= 2 and len(distinct_titles) < signals.html_heading_count:
        warnings.append("HTML 章节数多于索引条目，部分大标题可能未拆开")

    audit = DocumentAudit(
        doc_id=doc_id,
        doc_title=doc_title,
        strategy=strategy,
        score=score,
        section_count=len(sections),
        whole_doc=whole,
        signals=signals,
        warnings=warnings,
    )
    if warnings:
        logger.warning(
            "文档切分待关注 [%s] %s strategy=%s score=%.1f warnings=%s",
            doc_id,
            doc_title,
            strategy,
            score,
            "; ".join(warnings),
        )
    return audit


def pick_best_sections(
    candidates: list[tuple[str, list[DocSection]]],
    source_len: int,
    *,
    margin: float = 4.0,
) -> tuple[str, list[DocSection], float]:
    """多策略切分结果择优；分数接近时优先 structured（保留 HTML 锚点）。"""
    if not candidates:
        return "empty", [], 0.0
    scored = [(name, secs, score_sections(secs, source_len)) for name, secs in candidates if secs]
    if not scored:
        return candidates[0][0], candidates[0][1], 0.0
    scored.sort(key=lambda x: x[2], reverse=True)
    best_name, best_secs, best_score = scored[0]
    if len(scored) > 1:
        second_score = scored[1][2]
        if scored[1][0] == "structured" and second_score >= best_score - margin:
            return scored[1][0], scored[1][1], second_score
    return best_name, best_secs, best_score


def summarize_audits(audits: list[DocumentAudit], *, limit: int = 8) -> dict[str, Any]:
    flagged = [a for a in audits if a.warnings]
    low_score = sorted(audits, key=lambda a: a.score)[:limit]
    return {
        "doc_total": len(audits),
        "flagged_count": len(flagged),
        "flagged": [a.to_dict() for a in flagged[:limit]],
        "lowest_score": [a.to_dict() for a in low_score if a.score < 40][:limit],
    }


def split_document(
    raw: str,
    *,
    doc_id: str,
    slug: str,
    doc_title: str,
    doc_url: str,
    catalog_path: list[str],
    updated_at: str = "",
) -> tuple[list[DocSection], DocumentAudit]:
    """同步入口：识别结构 → 多策略切分 → 择优 → 自检。"""
    from .models import (
        _split_plain_document,
        split_anchor_markdown,
        split_numbered_qa_sections,
        split_problem_faq_sections,
        split_qa_sections,
    )

    text = markdown_or_html_to_text(raw)
    signals = analyze_document_signals(raw, text)
    base = dict(
        doc_id=doc_id,
        slug=slug,
        doc_title=doc_title,
        doc_url=doc_url,
        catalog_path=catalog_path,
        updated_at=updated_at,
    )

    candidates: list[tuple[str, list[DocSection]]] = []

    structured = split_anchor_markdown(raw, **base)
    if structured:
        candidates.append(("structured", structured))

    plain = _split_plain_document(raw, **base)
    if plain:
        candidates.append(("plain", plain))

    # FAQ 信号强时，再跑一遍纯文本 Q/A 切分参与择优
    if signals.qa_block_count >= 2 or signals.problem_q_count >= 2 or signals.numbered_q_count >= 2:
        faq_sections: list[DocSection] = []
        faq_sections = split_qa_sections(text, **base, id_prefix="faq") or faq_sections
        if not faq_sections:
            faq_sections = split_problem_faq_sections(text, **base, id_prefix="pf") or faq_sections
        if not faq_sections:
            faq_sections = split_numbered_qa_sections(text, **base, id_prefix="nq") or faq_sections
        if faq_sections:
            candidates.append(("faq_text", faq_sections))

    strategy, sections, score = pick_best_sections(candidates, signals.text_len)
    if not sections:
        sections = structured or plain or []

    audit = audit_document_sections(
        doc_id=doc_id,
        doc_title=doc_title,
        sections=sections,
        signals=signals,
        strategy=strategy,
    )
    audit.score = score
    logger.info(
        "文档切分 [%s] %s → strategy=%s sections=%s score=%.1f",
        doc_id,
        doc_title,
        strategy,
        len(sections),
        score,
    )
    return sections, audit
