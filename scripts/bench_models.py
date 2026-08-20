# -*- coding: utf-8 -*-
"""用同一套售后场景，对比 OpenRouter 上多个聊天模型的重排准确度与速度。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.config import get_settings
from app.index_service import KnowledgeIndex
from app.llm import LLMReranker, RERANK_SYSTEM, MISS_SYSTEM

# 候选模型：中文友好 + 常见性价比档位
CANDIDATE_MODELS = [
    "qwen/qwen-2.5-72b-instruct",  # 当前默认
    "qwen/qwen-2.5-32b-instruct",
    "qwen/qwen-2.5-14b-instruct",
    "qwen/qwen3-32b",
    "qwen/qwen3-14b",
    "qwen/qwen3-8b",
    "deepseek/deepseek-chat-v3-0324",
    "deepseek/deepseek-chat",
    "google/gemini-2.0-flash-001",
    "google/gemini-2.5-flash",
    "meta-llama/llama-3.3-70b-instruct",
    "moonshotai/kimi-k2",
]

# 场景：期望 top 标题包含关键词；reject=True 表示应判未命中（ranked 空）
CASES = [
    {
        "q": "邮件设置页面 一直转圈圈",
        "expect_any": ["转圈", "设置无法打开", "权限"],
        "reject": False,
    },
    {
        "q": "打开邮件页面卡住转圈加载不出来",
        "expect_any": ["转圈", "设置无法打开", "权限"],
        "reject": False,
    },
    {
        "q": "绑定邮箱",
        "expect_any": ["绑定", "邮箱", "账号"],
        "reject": False,
    },
    {
        "q": "今天天气怎么样",
        "expect_any": [],
        "reject": True,
    },
    {
        "q": "客户管理怎么创建联系人",
        "expect_any": ["联系人", "创建", "客户"],
        "reject": False,  # 有文档更好；没有也不应乱答采购无关
        "forbid_any": ["签名管理", "千里眼", "退信"],
    },
]


def probe_model(reranker: LLMReranker) -> tuple[bool, str, float]:
    t0 = time.perf_counter()
    try:
        data = reranker._chat(
            "只输出 JSON：{\"ok\":true}",
            "ping",
            temperature=0,
        )
        ms = (time.perf_counter() - t0) * 1000
        ok = isinstance(data, dict)
        return ok, "ok" if ok else "bad_json", ms
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        return False, str(exc)[:120], ms


def score_case(case: dict, ranked: list[dict], used: bool) -> dict:
    titles = " ".join((r.get("title") or "") for r in ranked[:3])
    reject = bool(case.get("reject"))
    if reject:
        good = used and len(ranked) == 0
        return {"ok": good, "detail": "reject_empty" if good else f"got_{len(ranked)}"}
    if not ranked:
        # 文档可能本就没有：不算致命失败，记 partial
        return {"ok": False, "detail": "empty_ranked", "partial": True}
    hit = any(k in titles for k in (case.get("expect_any") or []))
    forbid = case.get("forbid_any") or []
    bad = any(k in titles for k in forbid)
    ok = hit and not bad
    return {
        "ok": ok,
        "detail": f"top={(ranked[0].get('title') or '')[:40]}",
        "partial": (not hit) and not bad,
    }


def main() -> None:
    settings = get_settings()
    if not settings.llm_api_key:
        print("NO_API_KEY")
        return

    idx = KnowledgeIndex(settings)
    # 等待/触发同步
    if not idx.engine.sections:
        print("syncing…")
        idx.sync()
    print(
        f"index sections={len(idx.engine.sections)} contacts={len(idx.engine.contacts)} "
        f"vectors={idx.vector_count}"
    )

    # 先探测可用模型
    available: list[str] = []
    print("\n=== PROBE MODELS ===")
    for model in CANDIDATE_MODELS:
        rr = LLMReranker(settings.llm_api_key, settings.llm_base_url, model)
        ok, msg, ms = probe_model(rr)
        print(f"{'OK' if ok else 'NO'} {model} {ms:.0f}ms {msg}")
        if ok:
            available.append(model)
    if not available:
        print("没有可用模型")
        return

    # 准备每个 case 的关键词候选（不含 LLM）
    prepared = []
    for case in CASES:
        payload = idx.engine.search(
            case["q"], limit=12, mode="all", keep_weak=True, retrieve_limit=16, skip_vector=True
        )
        payload = idx.engine.apply_vector_recall(
            case["q"], payload, pull=16, keep_weak=True
        )
        cands = payload.get("results") or []
        prepared.append((case, cands))
        print(f"case candidates q={case['q']!r} n={len(cands)}")

    rows = []
    print("\n=== BENCHMARK ===")
    for model in available:
        rr = LLMReranker(settings.llm_api_key, settings.llm_base_url, model)
        latencies = []
        score = 0
        total = 0
        details = []
        for case, cands in prepared:
            total += 1
            t0 = time.perf_counter()
            if not cands and case.get("reject"):
                # 无候选时测未命中话术速度
                try:
                    _ = rr.assist_miss(case["q"])
                    used = True
                    ranked = []
                    answer = ""
                    used_flag = True
                except Exception as exc:  # noqa: BLE001
                    details.append(f"miss_fail:{exc}")
                    continue
            else:
                out = rr.rerank(case["q"], cands, limit=8)
                ranked = out.get("results") or []
                used_flag = bool(out.get("used"))
                answer = out.get("answer") or ""
            ms = (time.perf_counter() - t0) * 1000
            latencies.append(ms)
            sc = score_case(case, ranked, used_flag)
            if sc.get("ok"):
                score += 1
            elif sc.get("partial"):
                score += 0.5
            details.append(
                f"{case['q'][:16]}|{ms:.0f}ms|{'OK' if sc.get('ok') else 'NG'}|{sc.get('detail')}"
            )
            time.sleep(0.3)
        avg = sum(latencies) / max(len(latencies), 1)
        row = {
            "model": model,
            "score": score,
            "total": total,
            "avg_ms": round(avg),
            "min_ms": round(min(latencies)) if latencies else None,
            "max_ms": round(max(latencies)) if latencies else None,
            "details": details,
        }
        rows.append(row)
        print(
            f"{model} score={score}/{total} avg={avg:.0f}ms "
            f"min={min(latencies):.0f} max={max(latencies):.0f}"
        )

    # 综合：准确度优先，其次速度
    def rank_key(r: dict):
        return (-float(r["score"]), float(r["avg_ms"] or 9e9))

    rows.sort(key=rank_key)
    print("\n=== RANKING (accuracy then speed) ===")
    for i, r in enumerate(rows, 1):
        print(
            f"{i}. {r['model']}  score={r['score']}/{r['total']}  "
            f"avg={r['avg_ms']}ms  range={r['min_ms']}-{r['max_ms']}ms"
        )

    out_path = Path("data") / "model_bench.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsaved {out_path}")
    if rows:
        best = rows[0]
        print(f"\nRECOMMEND: {best['model']}")


if __name__ == "__main__":
    main()
