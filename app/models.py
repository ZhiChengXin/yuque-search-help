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
    kind: str = "section"  # section | doc | contact_row
    images: list[str] = field(default_factory=list)
    level: int = 0

    @property
    def searchable_text(self) -> str:
        return "\n".join(
            [
                self.doc_title,
                self.section_title,
                " ".join(self.catalog_path),
                self.content,
            ]
        )


HEADING_RE = re.compile(
    r"<h([1-6])\s+id=\"([^\"]+)\">([\s\S]*?)</h\1>",
    re.IGNORECASE,
)
TAG_RE = re.compile(r"<[^>]+>")
IMG_MD_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMG_HTML_RE = re.compile(
    r"<img[^>]+(?:src|data-src)=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)


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
    return urls


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
    """把带 <hN id=\"xxx\"> 的正文切成可检索章节。"""
    sections: list[DocSection] = []
    matches = list(HEADING_RE.finditer(raw or ""))
    if not matches:
        body = markdown_or_html_to_text(raw)
        sections.append(
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
                images=extract_images(raw),
            )
        )
        return sections

    # 文首到第一个标题之间的前言
    preface_raw = raw[: matches[0].start()]
    preface = markdown_or_html_to_text(preface_raw)
    if preface or extract_images(preface_raw):
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
                images=extract_images(chunk),
                level=level,
            )
        )
    return sections


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
            if rid == 0:
                continue
            cols = data.get(str(rid)) or data.get(rid) or {}
            values = []
            for i in range(7):
                cell = cols.get(str(i)) or cols.get(i) or {}
                values.append(str(cell.get("value") or "").strip())
            menu, module, is_standard, testers, business_line, product_owner, product_contact = (
                values + [""] * 7
            )[:7]
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
