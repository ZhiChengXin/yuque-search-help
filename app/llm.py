from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger("yuque-help-search")

RERANK_SYSTEM = """你是售后知识库检索助手。根据用户问题，从候选章节中选出真正能解答问题的条目并打分。
只依据候选内容，不要编造文档里没有的步骤，不要把多条候选混在一起回答。
输出严格 JSON，不要 Markdown：
{"ranked":[{"id":"候选id","score":0到100的数字}],"answer":"只用得分最高那一条原文归纳的简短答复，没有把握则空字符串"}
规则：
- score 表示匹配度，最相关接近 100；不能真正回答问题时不要入选
- 若候选都答不上（例如用户问采购，候选却是邮件签名），ranked 必须返回空数组 []
- 最多返回 8 条，按相关度从高到低
- 用户说法口语化、和标题不完全一致时，按语义判断
- answer 不超过 80 字，且必须来自 ranked 第一条；若第一条不能回答用户问题，answer 必须为空字符串
"""

MISS_SYSTEM = """你是孚盟MX售后检索助手，说话自然、温和、专业，像同事帮忙，不要公文腔。
用户问题在现有知识库里没有找到合适文档。
用大白话简短说明：可能资料还没收录，可以换个说法再搜，或点反馈告诉我们。
不要编造操作步骤，不要用「敬请」「兹」「予以」这类书面词。
输出严格 JSON：
{"message":"给用户看的说明，2句左右即可"}
"""


class LLMReranker:
    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "https://openrouter.ai/api/v1").rstrip("/")
        self.model = model or "deepseek/deepseek-chat"

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def rerank(self, query: str, results: list[dict[str, Any]], limit: int = 8) -> dict[str, Any]:
        if not self.enabled or not results:
            return {"results": results, "answer": "", "used": False}

        candidates = results[: min(12, len(results))]
        payload = [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "doc_title": item.get("doc_title"),
                "preview": (item.get("preview") or item.get("snippet") or "")[:520],
            }
            for item in candidates
        ]
        user = (
            f"用户问题：{query}\n\n候选章节：\n{json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            data = self._chat(RERANK_SYSTEM, user)
            ranked = data.get("ranked") if isinstance(data, dict) else None
            answer = str((data or {}).get("answer") or "").strip()
            if not isinstance(ranked, list):
                return {"results": results, "answer": "", "used": False}
            if not ranked:
                return {"results": [], "answer": "", "used": True}

            by_id = {item.get("id"): item for item in candidates}
            merged: list[dict[str, Any]] = []
            seen = set()
            for row in ranked:
                if not isinstance(row, dict):
                    continue
                rid = row.get("id")
                item = by_id.get(rid)
                if not item or rid in seen:
                    continue
                try:
                    score = float(row.get("score") or 0)
                except (TypeError, ValueError):
                    score = 0.0
                if score < 45:
                    continue
                cloned = dict(item)
                cloned["match_percent"] = round(min(100.0, max(0.0, score)), 1)
                cloned["llm_ranked"] = True
                merged.append(cloned)
                seen.add(rid)
            if not merged:
                return {"results": [], "answer": "", "used": True}
            merged.sort(key=lambda x: x.get("match_percent") or 0, reverse=True)
            return {"results": merged[:limit], "answer": answer, "used": True}
        except Exception:
            logger.exception("大模型重排失败，回退关键词结果")
            return {"results": results, "answer": "", "used": False}

    def assist_miss(self, query: str) -> str:
        if not self.enabled:
            return (
                "这边暂时没翻到能直接对上的资料。你可以换个关键词再试试，"
                "或点「我要反馈」把问题留下。"
            )
        user = f"用户问题：{query}\n请生成未命中时的说明，语气自然温和。"
        try:
            data = self._chat(MISS_SYSTEM, user, temperature=0.3)
            message = str((data or {}).get("message") or "").strip()
            if message:
                return message
        except Exception:
            logger.exception("未命中说明生成失败")
        return (
            "这边暂时没翻到能直接对上的资料，可能相关文档还没同步进来。"
            "你可以换个关键词再试试，或点「我要反馈」把问题留下，我们后续补上。"
        )

    def _chat(self, system: str, user: str, temperature: float = 0.1) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://local.fumeng-help-search",
            "X-Title": "Fumeng MX Help Search",
        }
        body = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=35.0) as client:
            resp = client.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"LLM HTTP {resp.status_code}: {resp.text[:240]}")
        content = (
            ((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        )
        return _parse_json_object(content)


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
