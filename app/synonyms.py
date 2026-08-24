from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("yuque-help-search")

# 内置高频口语对照（可选；更多可放 data/synonyms.json，无需改代码）
DEFAULT_SYNONYM_GROUPS: list[list[str]] = [
    ["孚盟邮", "孚盟邮件", "邮箱", "邮件"],
    ["转圈", "转圈圈", "加载中", "卡住", "无响应", "一直转"],
    ["打不开", "无法打开", "打不开来"],
    ["同步", "没同步", "同步不全", "未同步"],
    ["权限", "无权限", "没权限", "未开通权限"],
    ["WhatsApp", "WA", "whatsapp"],
    ["签名", "邮件签名", "签名管理"],
    ["千里眼", "已读回执", "阅读跟踪"],
    ["绑定", "绑定邮箱", "添加账号"],
]

_GROUPS: list[list[str]] | None = None
_MAPPING: dict[str, list[str]] | None = None


def _normalize_groups(raw: list) -> list[list[str]]:
    groups: list[list[str]] = []
    for item in raw:
        if not isinstance(item, list):
            continue
        cleaned = [str(x).strip() for x in item if str(x).strip()]
        if len(cleaned) >= 2:
            groups.append(cleaned)
    return groups


def load_synonym_groups(data_dir: str | Path | None = None) -> list[list[str]]:
    """合并内置组与 data/synonyms.json（若存在）。"""
    groups = [list(g) for g in DEFAULT_SYNONYM_GROUPS]
    if data_dir:
        path = Path(data_dir) / "synonyms.json"
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                extra = payload.get("groups") if isinstance(payload, dict) else payload
                if isinstance(extra, list):
                    groups.extend(_normalize_groups(extra))
                    logger.info("已加载同义词配置 %s groups=%s", path, len(extra))
            except Exception:
                logger.exception("读取同义词配置失败: %s", path)
    # 去重：按 frozenset 合并
    merged: list[list[str]] = []
    seen: set[frozenset[str]] = set()
    for g in groups:
        key = frozenset(x.lower() for x in g)
        if key in seen:
            continue
        seen.add(key)
        merged.append(g)
    return merged


def configure_synonyms(data_dir: str | Path | None = None) -> None:
    global _GROUPS, _MAPPING
    _GROUPS = load_synonym_groups(data_dir)
    mapping: dict[str, list[str]] = {}
    for group in _GROUPS:
        lowered = [g.lower() for g in group]
        for g in lowered:
            mapping[g] = lowered
    _MAPPING = mapping


def _ensure_mapping() -> dict[str, list[str]]:
    global _MAPPING
    if _MAPPING is None:
        configure_synonyms()
    return _MAPPING or {}


def expand_terms(terms: list[str]) -> list[str]:
    """把分词结果按同义词组扩展（去重保序）。"""
    if not terms:
        return []
    mapping = _ensure_mapping()
    seen = set()
    out: list[str] = []
    for t in terms:
        key = (t or "").strip().lower()
        if not key:
            continue
        candidates = [t]
        for syn in mapping.get(key, []):
            candidates.append(syn)
        for c in candidates:
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out
