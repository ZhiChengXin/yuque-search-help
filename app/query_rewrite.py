from __future__ import annotations

import re


def _soften(query: str) -> str:
    text = (query or "").strip()
    if not text:
        return ""
    return re.sub(r"([\u4e00-\u9fff])\1+", r"\1", text)


# 口语 → 补充检索词（不替换原句，只追加规范化说法）
_REWRITE_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("转圈圈", "转圈", "一直转", "转不停", "转呀转"), "加载卡住 无响应"),
    (("打不开来", "打不开", "打不开页面", "进不去"), "无法打开"),
    (("加载不出来", "加载失败", "出不来", "刷不出来"), "加载失败 卡住"),
    (("白屏", "一片白", "空白页"), "页面空白 崩溃"),
    (("崩了", "崩溃了", "闪退", "卡死"), "网页崩溃"),
    (("加邮箱", "绑邮箱", "绑邮件", "添加邮箱"), "绑定邮箱 添加账号"),
    (("建联系人", "新建联系人", "加联系人", "创建联系人"), "创建联系人"),
    (("公海客户", "客户公海", "扔公海"), "公海"),
    (("怎么设置签名", "设签名", "改签名"), "邮件签名 签名管理"),
    (("已读", "看没看", "有没有看"), "千里眼 已读回执"),
    (("发不出去", "发不了", "发送失败"), "发送失败 邮件发送"),
    (("收不到", "没收到邮件", "收件没有"), "收件 同步"),
    (("权限不够", "没权限", "无权限"), "权限 开通权限"),
    (("怎么导出", "导出来", "导出数据"), "导出"),
    (("模板", "套模板"), "模板管理"),
]


def rewrite_query(query: str) -> str:
    """轻量查询改写：保留原句，追加业务规范化词，提升语义召回。"""
    raw = (query or "").strip()
    if not raw:
        return ""
    soft = _soften(raw) or raw
    extras: list[str] = []
    seen = set()
    blob = soft.lower()
    for keys, hint in _REWRITE_HINTS:
        if any(k.lower() in blob for k in keys):
            for part in hint.split():
                key = part.lower()
                if key not in seen and key not in blob:
                    seen.add(key)
                    extras.append(part)
    if not extras:
        return soft
    return f"{soft} {' '.join(extras)}".strip()
