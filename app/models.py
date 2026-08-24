from __future__ import annotations

import json
import re
import zlib
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContactRecord:
    menu: str = ""
    module: str = ""
    is_standard: str = ""
    testers: str = ""
    business_line: str = ""
    product_owner: str = ""
    product_contact: str = ""
    source_url: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "menu": self.menu,
            "module": self.module,
            "is_standard": self.is_standard,
            "testers": self.testers,
            "business_line": self.business_line,
            "product_owner": self.product_owner,
            "product_contact": self.product_contact,
            "source_url": self.source_url,
        }


@dataclass
class DocSection:
    id: str
    doc_id: str
    slug: str
    doc_title: str
    section_title: str
    section_anchor: str
    content: str
    url: str
    doc_url: str
    catalog_path: list[str] = field(default_factory=list)
    updated_at: str = ""
    kind: str = "section"  # section | doc | contact_row | qa
    images: list[str] = field(default_factory=list)
    level: int = 0
    answer: str = ""

    @property
    def searchable_text(self) -> str:
        return "\n".join(
            [
                self.doc_title,
                self.section_title,
                " ".join(self.catalog_path),
                self.content,
                self.answer,
            ]
        )


HEADING_RE = re.compile(
    r"<h([1-6])\s+id=\"([^\"]+)\">([\s\S]*?)</h\1>",
    re.IGNORECASE,
)
QA_BLOCK_RE = re.compile(
    r"(?:^|\n)\s*Q[:：]\s*(.+?)\s*\n\s*A[:：]\s*(.+?)(?=(?:\n\s*Q[:：])|$)",
    re.IGNORECASE | re.DOTALL,
)
# 语雀 FAQ 常见编号：Q1：问题 … Q2：问题（无显式 A：，答案为后续条目）
NUMBERED_QA_MARKER_RE = re.compile(r"(?:^|\n)\s*(Q\d+)\s*[:：]\s*", re.IGNORECASE)
NUMBERED_QA_RAW_MARKER_RE = re.compile(
    r"(?:^|(?<=[\n>]))\s*Q\d+\s*[:：]\s*",
    re.IGNORECASE,
)
# 操作手册常见大纲：一、 / 问题1： / 第1步 / 1、标题
OUTLINE_LINE_RE = re.compile(
    r"^(?:"
    r"(?:[一二三四五六七八九十百]+[、．.\s])"
    r"|(?:问题\d+[：:])"
    r"|(?:第\s*[一二三四五六七八九十\d]+\s*(?:步|章|节|部分)[、．.:：\s]*)"
    r"|(?:\d+[、．.]\s*\S)"
    r"|(?:\d+\s+[^\d\s].{1,40})$"
    r")",
    re.MULTILINE,
)
PROBLEM_FAQ_MARKER_RE = re.compile(r"(?:^|\n)\s*(问题\d+)\s*[：:]\s*", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
IMG_MD_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMG_HTML_RE = re.compile(
    r"<img[^>]+(?:src|data-src)=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
# 语雀 Lake 图片卡片
IMG_CARD_RE = re.compile(
    r"""(?:data-src|src|url)\s*[:=]\s*[\"'](https?://[^\"']+\.(?:png|jpe?g|gif|webp|bmp)[^\"']*)[\"']""",
    re.IGNORECASE,
)


def make_deep_url(doc_url: str, title: str = "", anchor: str = "") -> str:
    """生成语雀深链：有章节锚点优先；否则用浏览器文本定位。"""
    base = (doc_url or "").strip().split("#", 1)[0]
    if not base:
        return ""
    if anchor:
        return f"{base}#{anchor}"
    text = re.sub(r"\s+", " ", (title or "").strip())
    if len(text) < 4:
        return base
    from urllib.parse import quote

    # 语雀 FAQ 常见写法是「Q: xxx」；过长 text fragment 容易匹配失败，取更短片段
    candidates = []
    for prefix in ("Q：", "Q:", ""):
        full = f"{prefix}{text}" if prefix else text
        for size in (36, 28, 48):
            if len(full) >= min(size, 8):
                candidates.append(full[:size])
    seen = set()
    for snippet in candidates:
        key = snippet.strip()
        if len(key) < 4 or key in seen:
            continue
        seen.add(key)
        return f"{base}#:~:text={quote(key, safe='')}"
    return base


def extract_images(raw: str, limit: int = 8) -> list[str]:
    """从章节原始 HTML/Markdown 抽取图片地址。"""
    if not raw:
        return []
    urls: list[str] = []
    seen = set()
    for match in IMG_HTML_RE.finditer(raw):
        url = (match.group(1) or "").strip()
        if not url or url.startswith("data:"):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            return urls
    for match in IMG_MD_RE.finditer(raw):
        url = (match.group(1) or "").strip()
        if not url or url.startswith("data:"):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    if len(urls) < limit:
        for match in IMG_CARD_RE.finditer(raw):
            url = (match.group(1) or "").strip()
            if not url or url.startswith("data:") or url in seen:
                continue
            seen.add(url)
            urls.append(url)
            if len(urls) >= limit:
                break
    return urls


def _qa_raw_chunks(raw: str) -> list[str]:
    """按 Q： 标记切分原始正文，便于给每条 Q/A 挂上自己的截图。"""
    source = raw or ""
    if not source:
        return []
    marker = re.compile(r"(?:^|(?<=[\n>]))\s*Q\s*[:：]\s*", re.IGNORECASE)
    hits = list(marker.finditer(source))
    if not hits:
        return []
    chunks: list[str] = []
    for i, hit in enumerate(hits):
        start = hit.start()
        end = hits[i + 1].start() if i + 1 < len(hits) else len(source)
        chunks.append(source[start:end])
    return chunks


def _numbered_qa_raw_chunks(raw: str) -> list[str]:
    """按 Q1：/Q2： 编号切分原始正文。"""
    source = raw or ""
    if not source:
        return []
    hits = list(NUMBERED_QA_RAW_MARKER_RE.finditer(source))
    if not hits:
        return []
    chunks: list[str] = []
    for i, hit in enumerate(hits):
        start = hit.start()
        end = hits[i + 1].start() if i + 1 < len(hits) else len(source)
        chunks.append(source[start:end])
    return chunks


def _parse_numbered_qa_chunk(text: str) -> tuple[str, str, str] | None:
    """解析 Q1：问题 + 后续条目，返回 (Q1, 问题, 答案正文)。"""
    source = (text or "").strip()
    if not source:
        return None
    match = re.match(r"^(Q\d+)\s*[:：]\s*(.+)$", source, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    label = match.group(1).upper()
    rest = match.group(2).strip()
    lines = rest.split("\n", 1)
    question = re.sub(r"\s+", " ", lines[0]).strip()
    answer = lines[1].strip() if len(lines) > 1 else ""
    if len(question) < 4:
        return None
    return label, question, answer


def _append_numbered_qa_section(
    sections: list[DocSection],
    *,
    idx: int,
    label: str,
    question: str,
    answer: str,
    doc_id: str,
    slug: str,
    doc_title: str,
    doc_url: str,
    catalog_path: list[str],
    updated_at: str,
    id_prefix: str,
    parent_url: str,
    parent_anchor: str,
    images: list[str],
) -> None:
    section_id = f"{doc_id}:{id_prefix}-{idx + 1}"
    deep = make_deep_url(parent_url or doc_url, f"{label}：{question}", parent_anchor)
    answer_one_line = re.sub(r"\s+", " ", answer).strip(" ；;，,")
    sections.append(
        DocSection(
            id=section_id,
            doc_id=doc_id,
            slug=slug,
            doc_title=doc_title,
            section_title=question,
            section_anchor=parent_anchor,
            content=f"{label}：{question}\n{answer}".strip(),
            url=deep,
            doc_url=doc_url,
            catalog_path=catalog_path,
            updated_at=updated_at,
            kind="qa",
            images=images,
            level=0,
            answer=answer_one_line[:500] if answer_one_line else "",
        )
    )


def split_problem_faq_sections(
    text: str,
    *,
    doc_id: str,
    slug: str,
    doc_title: str,
    doc_url: str,
    catalog_path: list[str],
    updated_at: str = "",
    images: list[str] | None = None,
    id_prefix: str = "pf",
) -> list[DocSection]:
    """沟通类 FAQ：问题1：标题 … 问题2："""
    source = (text or "").strip()
    if not source:
        return []
    hits = list(PROBLEM_FAQ_MARKER_RE.finditer(source))
    if len(hits) < 2:
        return []
    sections: list[DocSection] = []
    shared_images = list(images or [])
    for idx, hit in enumerate(hits):
        label = hit.group(1)
        block_start = hit.end()
        block_end = hits[idx + 1].start() if idx + 1 < len(hits) else len(source)
        block = source[block_start:block_end].strip()
        lines = block.split("\n", 1)
        question = re.sub(r"\s+", " ", lines[0]).strip()
        answer = lines[1].strip() if len(lines) > 1 else ""
        if len(question) < 4:
            continue
        title = f"{label}：{question}"
        item_images = shared_images if (idx == 0 or len(shared_images) <= 2) else []
        answer_one_line = re.sub(r"\s+", " ", answer).strip(" ；;，,")
        sections.append(
            DocSection(
                id=f"{doc_id}:{id_prefix}-{idx + 1}",
                doc_id=doc_id,
                slug=slug,
                doc_title=doc_title,
                section_title=title,
                section_anchor="",
                content=f"{title}\n{answer}".strip(),
                url=make_deep_url(doc_url, title),
                doc_url=doc_url,
                catalog_path=catalog_path,
                updated_at=updated_at,
                kind="qa",
                images=item_images,
                level=0,
                answer=answer_one_line[:500] if answer_one_line else "",
            )
        )
    return sections


def split_outline_sections(
    text: str,
    *,
    doc_id: str,
    slug: str,
    doc_title: str,
    doc_url: str,
    catalog_path: list[str],
    updated_at: str = "",
    images: list[str] | None = None,
    id_prefix: str = "ol",
    min_blocks: int = 2,
    min_body_len: int = 60,
) -> list[DocSection]:
    """无 HTML 标题的操作手册：按 一、/1、/第N步 等大纲行切分。"""
    source = (text or "").strip()
    if len(source) < min_body_len * 2:
        return []
    lines = source.split("\n")
    markers: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or len(stripped) > 80:
            continue
        normalized = re.sub(r"^[lL•\-\*]\s*", "", stripped)
        if OUTLINE_LINE_RE.match(normalized) or OUTLINE_LINE_RE.match(stripped):
            markers.append((i, stripped))
    if len(markers) < min_blocks:
        return []
    sections: list[DocSection] = []
    shared_images = list(images or [])
    for idx, (line_no, title) in enumerate(markers):
        next_line = markers[idx + 1][0] if idx + 1 < len(markers) else len(lines)
        body = "\n".join(lines[line_no:next_line]).strip()
        if len(body) < min_body_len:
            continue
        section_id = f"{doc_id}:{id_prefix}-{idx + 1}"
        sections.append(
            DocSection(
                id=section_id,
                doc_id=doc_id,
                slug=slug,
                doc_title=doc_title,
                section_title=title[:120],
                section_anchor="",
                content=body,
                url=make_deep_url(doc_url, title),
                doc_url=doc_url,
                catalog_path=catalog_path,
                updated_at=updated_at,
                kind="section",
                images=shared_images if (idx == 0 or len(shared_images) <= 2) else [],
                level=0,
            )
        )
    return sections


def _split_plain_document(
    raw: str,
    *,
    doc_id: str,
    slug: str,
    doc_title: str,
    doc_url: str,
    catalog_path: list[str],
    updated_at: str = "",
) -> list[DocSection]:
    """无 HTML 锚点时的回退：Q/A → 问题1 → 大纲 → 整篇。"""
    qa_sections = split_qa_from_raw(
        raw,
        doc_id=doc_id,
        slug=slug,
        doc_title=doc_title,
        doc_url=doc_url,
        catalog_path=catalog_path,
        updated_at=updated_at,
    )
    if qa_sections:
        return qa_sections
    body = markdown_or_html_to_text(raw)
    images = extract_images(raw)
    for splitter, prefix in (
        (split_problem_faq_sections, "pf"),
        (split_outline_sections, "ol"),
    ):
        parts = splitter(
            body,
            doc_id=doc_id,
            slug=slug,
            doc_title=doc_title,
            doc_url=doc_url,
            catalog_path=catalog_path,
            updated_at=updated_at,
            images=images,
            id_prefix=prefix,
        )
        if parts:
            return parts
    if not body.strip():
        return []
    return [
        DocSection(
            id=f"{doc_id}:root",
            doc_id=doc_id,
            slug=slug,
            doc_title=doc_title,
            section_title=doc_title,
            section_anchor="",
            content=body,
            url=doc_url,
            doc_url=doc_url,
            catalog_path=catalog_path,
            updated_at=updated_at,
            kind="doc",
            images=images,
        )
    ]


def split_qa_from_raw(
    raw: str,
    *,
    doc_id: str,
    slug: str,
    doc_title: str,
    doc_url: str,
    catalog_path: list[str],
    updated_at: str = "",
    id_prefix: str = "qa",
    parent_title: str = "",
    parent_url: str = "",
    parent_anchor: str = "",
) -> list[DocSection]:
    """从原始 HTML/Markdown 按 Q/A 切分，并给每条挂上块内图片。"""
    chunks = _qa_raw_chunks(raw)
    if not chunks:
        chunks = _numbered_qa_raw_chunks(raw)
    if not chunks:
        text = markdown_or_html_to_text(raw)
        sections = split_qa_sections(
            text,
            doc_id=doc_id,
            slug=slug,
            doc_title=doc_title,
            doc_url=doc_url,
            catalog_path=catalog_path,
            updated_at=updated_at,
            images=extract_images(raw),
            id_prefix=id_prefix,
            parent_title=parent_title,
            parent_url=parent_url,
            parent_anchor=parent_anchor,
        )
        if sections:
            return sections
        return split_numbered_qa_sections(
            text,
            doc_id=doc_id,
            slug=slug,
            doc_title=doc_title,
            doc_url=doc_url,
            catalog_path=catalog_path,
            updated_at=updated_at,
            images=extract_images(raw),
            id_prefix=id_prefix,
            parent_title=parent_title,
            parent_url=parent_url,
            parent_anchor=parent_anchor,
        )

    sections: list[DocSection] = []
    for idx, chunk in enumerate(chunks):
        body = markdown_or_html_to_text(chunk)
        numbered = _parse_numbered_qa_chunk(body)
        if numbered:
            label, question, answer = numbered
            _append_numbered_qa_section(
                sections,
                idx=idx,
                label=label,
                question=question,
                answer=answer,
                doc_id=doc_id,
                slug=slug,
                doc_title=doc_title,
                doc_url=doc_url,
                catalog_path=catalog_path,
                updated_at=updated_at,
                id_prefix=id_prefix,
                parent_url=parent_url or doc_url,
                parent_anchor=parent_anchor,
                images=extract_images(chunk),
            )
            continue
        match = QA_BLOCK_RE.search("\n" + body)
        if not match:
            # 兼容「Q：问题」后直接跟图片再跟 A：
            loose = re.search(
                r"Q[:：]\s*(.+?)\s*A[:：]\s*(.+)$",
                body,
                re.IGNORECASE | re.DOTALL,
            )
            if not loose:
                continue
            question = re.sub(r"\s+", " ", loose.group(1)).strip()
            answer = re.sub(r"\s+", " ", loose.group(2)).strip()
        else:
            question = re.sub(r"\s+", " ", (match.group(1) or "").strip())
            answer = re.sub(r"\s+", " ", (match.group(2) or "").strip())
        question = re.sub(r"^[Qq][:：]\s*", "", question).strip()
        answer = re.sub(r"^[Aa][:：]\s*", "", answer).strip()
        if len(question) < 4 or not answer:
            continue
        section_id = f"{doc_id}:{id_prefix}-{idx + 1}"
        deep = make_deep_url(parent_url or doc_url, question, parent_anchor)
        sections.append(
            DocSection(
                id=section_id,
                doc_id=doc_id,
                slug=slug,
                doc_title=doc_title,
                section_title=question,
                section_anchor=parent_anchor,
                content=f"Q：{question}\nA：{answer}",
                url=deep,
                doc_url=doc_url,
                catalog_path=catalog_path,
                updated_at=updated_at,
                kind="qa",
                images=extract_images(chunk),
                level=0,
                answer=answer,
            )
        )
    return sections


def strip_html(text: str) -> str:
    text = TAG_RE.sub("", text or "")
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return re.sub(r"\s+", " ", text).strip()


def markdown_or_html_to_text(raw: str) -> str:
    """转成可读正文，尽量保留段落/步骤换行，便于结果预览。"""
    if not raw:
        return ""
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?is)</(p|div|h[1-6]|li|tr|blockquote)>", "\n", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)<li[^>]*>", "• ", text)
    text = re.sub(r"!\[.*?\]\(.*?\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = TAG_RE.sub(" ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    text = re.sub(r"[|*_~>#`]+", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()


def split_anchor_markdown(
    raw: str,
    *,
    doc_id: str,
    slug: str,
    doc_title: str,
    doc_url: str,
    catalog_path: list[str],
    updated_at: str = "",
) -> list[DocSection]:
    """把带 <hN id=\"xxx\"> 的正文切成可检索章节；无标题时尝试按 Q/A 切分。"""
    sections: list[DocSection] = []
    matches = list(HEADING_RE.finditer(raw or ""))
    if not matches:
        return _split_plain_document(
            raw,
            doc_id=doc_id,
            slug=slug,
            doc_title=doc_title,
            doc_url=doc_url,
            catalog_path=catalog_path,
            updated_at=updated_at,
        )

    # 文首到第一个标题之间的前言
    preface_raw = raw[: matches[0].start()]
    preface = markdown_or_html_to_text(preface_raw)
    if preface or extract_images(preface_raw):
        preface_qa = split_qa_from_raw(
            preface_raw,
            doc_id=doc_id,
            slug=slug,
            doc_title=doc_title,
            doc_url=doc_url,
            catalog_path=catalog_path,
            updated_at=updated_at,
            id_prefix="preface",
        )
        if preface_qa:
            sections.extend(preface_qa)
        elif len(preface) >= 120 or extract_images(preface_raw):
            sections.append(
                DocSection(
                    id=f"{doc_id}:preface",
                    doc_id=doc_id,
                    slug=slug,
                    doc_title=doc_title,
                    section_title=doc_title,
                    section_anchor="",
                    content=preface,
                    url=doc_url,
                    doc_url=doc_url,
                    catalog_path=catalog_path,
                    updated_at=updated_at,
                    kind="doc",
                    images=extract_images(preface_raw),
                )
            )

    for i, match in enumerate(matches):
        level = int(match.group(1))
        anchor = match.group(2).strip()
        title = strip_html(match.group(3))
        if not title:
            continue
        start = match.end()
        # 取到下一个同级/更高级标题为止，把下级说明、步骤、图片一并纳入本章预览
        end = len(raw)
        for j in range(i + 1, len(matches)):
            if int(matches[j].group(1)) <= level:
                end = matches[j].start()
                break
        chunk = raw[start:end]
        body = markdown_or_html_to_text(chunk)
        deep_url = f"{doc_url}#{anchor}" if anchor else doc_url
        qa_sections = split_qa_from_raw(
            chunk,
            doc_id=doc_id,
            slug=slug,
            doc_title=doc_title,
            doc_url=doc_url,
            catalog_path=catalog_path,
            updated_at=updated_at,
            id_prefix=anchor or f"h{i}",
            parent_title=title,
            parent_url=deep_url,
            parent_anchor=anchor,
        )
        if qa_sections and len(qa_sections) >= 2:
            sections.extend(qa_sections)
            continue
        chunk_images = extract_images(chunk)
        sections.append(
            DocSection(
                id=f"{doc_id}:{anchor}",
                doc_id=doc_id,
                slug=slug,
                doc_title=doc_title,
                section_title=title,
                section_anchor=anchor,
                content=body,
                url=deep_url,
                doc_url=doc_url,
                catalog_path=catalog_path,
                updated_at=updated_at,
                kind="section",
                images=chunk_images,
                level=level,
                answer=extract_answer_text(body),
            )
        )
    return sections


def extract_answer_text(content: str) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    match = re.search(r"A[:：]\s*(.+?)(?=(?:\n\s*Q[:：])|$)", text, re.IGNORECASE | re.DOTALL)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip(" ；;，,")[:500]
    numbered = _parse_numbered_qa_chunk(text)
    if numbered:
        _label, _question, answer = numbered
        return re.sub(r"\s+", " ", answer).strip(" ；;，,")[:500]
    return ""


def split_numbered_qa_sections(
    text: str,
    *,
    doc_id: str,
    slug: str,
    doc_title: str,
    doc_url: str,
    catalog_path: list[str],
    updated_at: str = "",
    images: list[str] | None = None,
    id_prefix: str = "qa",
    parent_title: str = "",
    parent_url: str = "",
    parent_anchor: str = "",
) -> list[DocSection]:
    """把 Q1：/Q2： 编号 FAQ 拆成独立检索条目。"""
    source = (text or "").strip()
    if not source:
        return []
    hits = list(NUMBERED_QA_MARKER_RE.finditer(source))
    if len(hits) < 2:
        return []
    sections: list[DocSection] = []
    shared_images = list(images or [])
    for idx, hit in enumerate(hits):
        label = hit.group(1).upper()
        block_start = hit.end()
        block_end = hits[idx + 1].start() if idx + 1 < len(hits) else len(source)
        block = source[block_start:block_end].strip()
        lines = block.split("\n", 1)
        question = re.sub(r"\s+", " ", lines[0]).strip()
        answer = lines[1].strip() if len(lines) > 1 else ""
        if len(question) < 4:
            continue
        item_images = shared_images if (idx == 0 or len(shared_images) <= 2) else []
        _append_numbered_qa_section(
            sections,
            idx=idx,
            label=label,
            question=question,
            answer=answer,
            doc_id=doc_id,
            slug=slug,
            doc_title=doc_title,
            doc_url=doc_url,
            catalog_path=catalog_path,
            updated_at=updated_at,
            id_prefix=id_prefix,
            parent_url=parent_url or doc_url,
            parent_anchor=parent_anchor,
            images=item_images,
        )
    return sections


def split_qa_sections(
    text: str,
    *,
    doc_id: str,
    slug: str,
    doc_title: str,
    doc_url: str,
    catalog_path: list[str],
    updated_at: str = "",
    images: list[str] | None = None,
    id_prefix: str = "qa",
    parent_title: str = "",
    parent_url: str = "",
    parent_anchor: str = "",
) -> list[DocSection]:
    """把 FAQ 正文中的 Q/A 对拆成独立检索条目。"""
    source = (text or "").strip()
    if not source or source.count("Q") < 1:
        return []
    matches = list(QA_BLOCK_RE.finditer(source))
    if len(matches) < 1:
        return []
    # 至少 1 条清晰 Q/A；整篇只有 1 条也拆出来，便于标题命中
    sections: list[DocSection] = []
    shared_images = list(images or [])
    for idx, match in enumerate(matches):
        question = re.sub(r"\s+", " ", (match.group(1) or "").strip())
        answer = re.sub(r"\s+", " ", (match.group(2) or "").strip())
        if len(question) < 4 or not answer:
            continue
        question = re.sub(r"^[Qq][:：]\s*", "", question).strip()
        answer = re.sub(r"^[Aa][:：]\s*", "", answer).strip()
        section_id = f"{doc_id}:{id_prefix}-{idx + 1}"
        deep = make_deep_url(parent_url or doc_url, question, parent_anchor)
        # 纯文本回退时无法按条切图，共享给每条（总图少时）或仅首条
        item_images = shared_images if (idx == 0 or len(shared_images) <= 2) else []
        sections.append(
            DocSection(
                id=section_id,
                doc_id=doc_id,
                slug=slug,
                doc_title=doc_title,
                section_title=question,
                section_anchor=parent_anchor,
                content=f"Q：{question}\nA：{answer}",
                url=deep,
                doc_url=doc_url,
                catalog_path=catalog_path,
                updated_at=updated_at,
                kind="qa",
                images=item_images,
                level=0,
                answer=answer,
            )
        )
    return sections


def _cell_text(cell: Any) -> str:
    """兼容语雀表格单元格新旧字段：value / v。"""
    if cell is None:
        return ""
    if isinstance(cell, dict):
        for key in ("value", "v", "text", "name"):
            if key in cell and cell[key] is not None:
                return str(cell[key]).strip()
        return ""
    return str(cell).strip()


def parse_lakesheet_contacts(body: str, source_url: str = "") -> list[ContactRecord]:
    """解析语雀 lakesheet 压缩正文，提取业务线/测试/产品联系人。"""
    if not body:
        return []
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return []

    sheet_blob = payload.get("sheet")
    if not isinstance(sheet_blob, str):
        return []
    try:
        decompressed = zlib.decompress(sheet_blob.encode("latin-1"))
        sheets = json.loads(decompressed.decode("utf-8"))
    except Exception:
        return []

    contacts: list[ContactRecord] = []
    last_menu = ""
    for sheet in sheets:
        data = (sheet or {}).get("data") or {}
        if not isinstance(data, dict) or not data:
            continue
        # 行号可能是字符串
        row_ids = sorted(int(k) for k in data.keys() if str(k).isdigit())
        for rid in row_ids:
            cols = data.get(str(rid)) or data.get(rid) or {}
            if not isinstance(cols, dict):
                continue
            values = []
            for i in range(7):
                cell = cols.get(str(i)) or cols.get(i) or {}
                values.append(_cell_text(cell))
            menu, module, is_standard, testers, business_line, product_owner, product_contact = (
                values + [""] * 7
            )[:7]

            # 跳过表头
            header_blob = "".join(values)
            if rid == 0 or (
                "一级菜单" in header_blob and "模块" in header_blob and "测试" in header_blob
            ):
                continue

            if menu:
                last_menu = menu
            else:
                menu = last_menu
            if not module and not testers and not product_owner:
                continue
            contacts.append(
                ContactRecord(
                    menu=menu,
                    module=module or menu,
                    is_standard=is_standard,
                    testers=testers,
                    business_line=business_line,
                    product_owner=product_owner,
                    product_contact=product_contact.rstrip("."),
                    source_url=source_url,
                )
            )
    return contacts


def contacts_to_sections(
    contacts: list[ContactRecord],
    *,
    doc_id: str,
    slug: str,
    doc_title: str,
    doc_url: str,
) -> list[DocSection]:
    sections: list[DocSection] = []
    for i, c in enumerate(contacts):
        title = c.module or c.menu or f"联系人-{i}"
        content = " ".join(
            [
                f"一级菜单:{c.menu}",
                f"模块:{c.module}",
                f"测试:{c.testers}",
                f"业务线:{c.business_line}",
                f"产品:{c.product_owner}",
                f"产品联系方式:{c.product_contact}",
            ]
        )
        sections.append(
            DocSection(
                id=f"{doc_id}:contact:{i}",
                doc_id=doc_id,
                slug=slug,
                doc_title=doc_title,
                section_title=title,
                section_anchor="",
                content=content,
                url=doc_url,
                doc_url=doc_url,
                catalog_path=["业务线模块分工表"],
                kind="contact_row",
            )
        )
    return sections
