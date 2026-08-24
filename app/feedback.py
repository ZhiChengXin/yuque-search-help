from __future__ import annotations

import base64
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from .config import Settings
from .yuque_client import YuqueClient

logger = logging.getLogger("yuque-help-search")

MIME_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def _split_data_url(data_url: str) -> tuple[str, str, bytes]:
    match = re.match(r"^data:(image/[\w.+-]+);base64,(.+)$", data_url.strip(), re.I | re.S)
    if not match:
        raise ValueError("截图格式无效")
    mime = match.group(1).lower()
    raw = base64.b64decode(match.group(2))
    ext = MIME_EXT.get(mime, "png")
    return mime, ext, raw


def _title_from_description(description: str, has_images: bool) -> str:
    text = re.sub(r"\s+", " ", (description or "").strip())
    if not text:
        return "截图反馈" if has_images else "问题反馈"
    text = re.split(r"[。！？!?\n]", text, maxsplit=1)[0].strip(" ，,;；:：")
    if len(text) > 36:
        text = text[:36].rstrip() + "…"
    return text or "问题反馈"


def prepare_feedback(
    *,
    description: str,
    images: list[str],
    query: str = "",
    mode: str = "",
) -> dict[str, Any]:
    """同步校验并解析截图；通过后由后台任务写入语雀。"""
    text = (description or "").strip()
    if not text and not images:
        return {"ok": False, "message": "请填写问题描述，或粘贴截图后再提交"}
    if len(text) > 4000:
        return {"ok": False, "message": "描述过长，请控制在 4000 字以内"}
    if len(images) > 6:
        return {"ok": False, "message": "一次最多上传 6 张截图"}

    parsed_images: list[tuple[str, str, bytes]] = []
    for item in images or []:
        try:
            mime, ext, raw = _split_data_url(item)
        except Exception:
            continue
        if not raw or len(raw) > 8 * 1024 * 1024:
            continue
        parsed_images.append((mime, ext, raw))
    if images and not parsed_images:
        return {"ok": False, "message": "截图无法识别，请重新粘贴后再提交"}

    record = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "title": _title_from_description(text, bool(parsed_images)),
        "description": text,
        "query": query,
        "mode": mode,
    }
    return {
        "ok": True,
        "message": "已提交",
        "title": record["title"],
        "record": record,
        "parsed_images": parsed_images,
    }


def run_feedback_job(
    settings: Settings,
    record: dict[str, Any],
    parsed_images: list[tuple[str, str, bytes]],
) -> None:
    """后台写入语雀；失败只记日志，不影响用户侧提交成功。"""
    started = time.perf_counter()
    try:
        _append_yuque(settings, record, parsed_images)
        logger.info(
            "feedback background saved title=%s images=%s elapsed=%.2fs",
            record.get("title"),
            len(parsed_images),
            time.perf_counter() - started,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "feedback background write failed title=%s images=%s",
            record.get("title"),
            len(parsed_images),
        )


def save_feedback(
    settings: Settings,
    *,
    description: str,
    images: list[str],
    query: str = "",
    mode: str = "",
) -> dict[str, Any]:
    """同步写入（脚本/测试用）。"""
    prepared = prepare_feedback(
        description=description,
        images=images,
        query=query,
        mode=mode,
    )
    if not prepared.get("ok"):
        return {"ok": False, "message": prepared.get("message", "提交失败")}
    try:
        _append_yuque(settings, prepared["record"], prepared["parsed_images"])
    except Exception as exc:  # noqa: BLE001
        logger.exception("反馈写入语雀失败")
        message = str(exc)
        if "401" in message or "权限" in message or "图集" in message:
            return {
                "ok": False,
                "message": "未能写入语雀：请确认 Token 已勾选文档权限与「修改和读取你的图集」",
            }
        return {"ok": False, "message": f"未能写入语雀，请稍后重试（{message[:80]}）"}
    return {
        "ok": True,
        "message": "反馈已写入内部文档",
        "title": prepared["title"],
    }


def _to_markdown(record: dict[str, Any], image_urls: list[str]) -> str:
    lines = [
        f"## {record['title']}",
        "",
        f"- 时间：{record['time']}",
        f"- 检索词：{record.get('query') or '-'}",
        f"- 模式：{record.get('mode') or '-'}",
        "",
        record.get("description") or "（无文字描述）",
        "",
    ]
    for i, url in enumerate(image_urls, 1):
        lines.append(f"![截图{i}]({url})")
        lines.append("")
    lines.append("---")
    return "\n".join(lines)


def _append_yuque(
    settings: Settings,
    record: dict[str, Any],
    parsed_images: list[tuple[str, str, bytes]],
) -> None:
    if not settings.yuque_token:
        raise RuntimeError("未配置语雀 Token")
    ns = f"{settings.yuque_feedback_login}/{settings.yuque_feedback_repo}"
    slug = settings.yuque_feedback_slug
    client = YuqueClient(
        space_login=settings.yuque_feedback_login,
        repo=settings.yuque_feedback_repo,
        host=settings.yuque_host,
        token=settings.yuque_token,
    )
    try:
        t0 = time.perf_counter()
        doc = client.get_doc(ns, slug)
        t_get = time.perf_counter() - t0
        doc_title = doc.get("title") or "售后反馈"
        doc_id = str(doc.get("id") or "")
        old = (doc.get("body") or "").rstrip()

        image_urls: list[str] = [""] * len(parsed_images)
        t_up = 0.0
        if parsed_images:
            t1 = time.perf_counter()

            def _one(idx_item: tuple[int, tuple[str, str, bytes]]) -> tuple[int, str]:
                i, (mime, ext, raw) = idx_item
                url = client.upload_image(
                    f"feedback-{i}.{ext}",
                    raw,
                    mime,
                    doc_id=doc_id,
                    allow_data_uri=False,
                )
                return i - 1, url

            workers = min(4, len(parsed_images))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(_one, (i, item))
                    for i, item in enumerate(parsed_images, 1)
                ]
                for fut in as_completed(futures):
                    idx, url = fut.result()
                    image_urls[idx] = url
            t_up = time.perf_counter() - t1

        md_block = _to_markdown(record, image_urls)
        body = f"{old}\n\n{md_block}".strip() if old else md_block.strip()
        t2 = time.perf_counter()
        client.update_doc_markdown(ns, slug, doc_title, body)
        t_put = time.perf_counter() - t2
        logger.info(
            "feedback yuque get=%.2fs upload=%.2fs update=%.2fs images=%s old_body=%s",
            t_get,
            t_up,
            t_put,
            len(parsed_images),
            len(old),
        )
    finally:
        client.close()
