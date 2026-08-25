from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger("yuque-help-search")

RERANK_SYSTEM = """你是孚盟MX售后知识库检索助手。根据用户问题，从候选章节中选出真正能解答问题的条目并打分。
只依据候选内容，不要编造文档里没有的步骤，不要把多条候选混在一起回答。
输出严格 JSON，不要 Markdown：
{"ranked":[{"id":"候选id","score":0到100的数字}],"answer":"只用得分最高那一条原文归纳的简短答复，没有把握则空字符串"}
规则：
- score 表示「能否解决用户问题」的语义匹配度，最相关接近 100；只是标题沾边但不能解答时分数应低于 55
- 若候选都答不上（例如用户问采购，候选却是邮件签名），ranked 必须返回空数组 []
- 最多返回 8 条，按相关度从高到低
- 用户说法口语化、和标题不完全一致时，按业务语义判断（如转圈≈加载卡住，绑邮箱≈绑定邮箱账号）
- 优先选能直接给操作步骤/处理办法的章节，而不是仅目录名相似
- answer 不超过 80 字，且必须来自 ranked 第一条；若第一条不能回答用户问题，answer 必须为空字符串
"""

MISS_SYSTEM = """你是孚盟MX售后检索助手，说话自然、温和、专业，像同事帮忙，不要公文腔。
用户问题在现有知识库里没有找到合适文档。
用大白话简短说明：可能资料还没收录，可以换个说法再搜，或点反馈告诉我们。
不要编造操作步骤，不要用「敬请」「兹」「予以」这类书面词。
输出严格 JSON：
{"message":"给用户看的说明，2句左右即可"}
"""

EXPAND_SYSTEM = """你是孚盟MX售后知识库检索助手。用户会用口语提问，与文档标题用词可能不一致。
根据用户问题，生成 2～3 条适合在知识库中检索的短句（每条 6～28 字），覆盖同义说法与业务模块词。
输出严格 JSON，不要 Markdown：
{"queries":["检索句1","检索句2"]}
规则：
- 保留用户原意，可补充模块名（客户/孚盟邮/WhatsApp/公海/线索等）
- 「头像/图标」「绑邮箱/添加账号」「转圈/加载卡住」等口语要展开
- 不要编造文档里没有的功能名
- queries 最多 3 条，去重，不要与原句完全相同"""


class LLMReranker:
    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "https://openrouter.ai/api/v1").rstrip("/")
        self.model = model or "qwen/qwen3.7-plus"

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def expand_queries(self, query: str, *, limit: int = 3) -> list[str]:
        """生成多条检索变体，用于多路召回与未命中二轮检索。"""
        raw = (query or "").strip()
        if not self.enabled or len(raw) < 4:
            return []
        try:
            data = self._chat(
                EXPAND_SYSTEM,
                f"用户问题：{raw}",
                temperature=0.15,
                timeout=8.0,
            )
            rows = data.get("queries") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                return []
            out: list[str] = []
            seen = {raw.lower(), soften_for_dedupe(raw)}
            for item in rows:
                text = re.sub(r"\s+", " ", str(item or "").strip())
                if len(text) < 4 or len(text) > 48:
                    continue
                key = soften_for_dedupe(text)
                if key in seen:
                    continue
                seen.add(key)
                out.append(text)
                if len(out) >= limit:
                    break
            return out
        except Exception:
            logger.exception("查询扩展失败")
            return []

    def rerank(self, query: str, results: list[dict[str, Any]], limit: int = 8) -> dict[str, Any]:
        if not self.enabled or not results:
            return {"results": results, "answer": "", "used": False}

        candidates = results[: min(16, len(results))]
        payload = [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "doc_title": item.get("doc_title"),
                "preview": (item.get("preview") or item.get("snippet") or "")[:600],
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
        # 未命中文案用固定话术，避免外网 LLM 超时拖垮整次检索
        return (
            "这边暂时没翻到能直接对上的资料，可能相关文档还没同步进来。"
            "你可以换个关键词再试试，或点「我要反馈」把问题留下，我们后续补上。"
        )

    def _chat(
        self,
        system: str,
        user: str,
        temperature: float = 0.1,
        timeout: float = 6.0,
    ) -> dict[str, Any]:
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
        with httpx.Client(timeout=timeout) as client:
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


def soften_for_dedupe(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip().lower())

