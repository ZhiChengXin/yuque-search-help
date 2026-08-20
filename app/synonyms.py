from __future__ import annotations

# 业务同义词：搜索时双向扩展，提升召回（不是写死某一条 FAQ）
SYNONYM_GROUPS: list[list[str]] = [
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


def expand_terms(terms: list[str]) -> list[str]:
    """把分词结果按同义词组扩展（去重保序）。"""
    if not terms:
        return []
    mapping: dict[str, list[str]] = {}
    for group in SYNONYM_GROUPS:
        lowered = [g.lower() for g in group]
        for g in lowered:
            mapping[g] = lowered
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
