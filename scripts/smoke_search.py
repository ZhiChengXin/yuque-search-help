import json
import time
import urllib.parse
import urllib.request
from statistics import mean

BASE = "http://127.0.0.1:8787"


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def search(q: str, mode: str = "all", fast: int = 0, limit: int = 5):
    qs = urllib.parse.urlencode({"q": q, "limit": limit, "mode": mode, "fast": fast})
    t0 = time.perf_counter()
    data = get(f"/api/search?{qs}")
    ms = (time.perf_counter() - t0) * 1000
    return data, ms


def main() -> None:
    status = get("/api/status")
    print("=== STATUS ===")
    print(
        json.dumps(
            {
                "ready": status.get("ready"),
                "syncing": status.get("syncing"),
                "docs": status.get("doc_count"),
                "sections": status.get("section_count"),
                "contacts": status.get("contact_count"),
                "vectors": status.get("vector_count"),
                "vector_enabled": status.get("vector_enabled"),
            },
            ensure_ascii=False,
        )
    )

    cases = [
        ("口语叠字", "all", "邮件设置页面 一直转圈圈", True),
        ("近义说法", "all", "邮件设置 页面 转圈", True),
        ("标题原文片段", "all", "孚盟邮界面的设置无法打开", True),
        ("操作类", "manual", "绑定邮箱", True),
        ("FAQ故障", "faq", "邮件内容无法显示", True),
        ("问负责人", "all", "孚盟邮的测试是谁", True),
        ("智能检索无文档", "all", "今天天气怎么样", False),
        ("负责人模式菜单", "owner", "孚盟邮", True),
        ("负责人模式文档问法", "owner", "邮件怎么设置邮箱账号", True),
        ("偏难口语", "all", "打开邮件页面卡住转圈加载不出来", True),
    ]

    print("\n=== SEARCH (fast=1 关键词+向量) ===")
    fast_times = []
    pass_n = fail_n = 0
    for tag, mode, q, expect_hit in cases:
        data, ms = search(q, mode=mode, fast=1)
        fast_times.append(ms)
        n = len(data.get("results") or [])
        c = len(data.get("contacts") or [])
        top = (data.get("results") or [{}])[0]
        title = (top.get("title") or "")[:40]
        hit = n > 0 or (mode == "owner" and c > 0) or (c > 0 and ("测试" in q or "负责人" in q))
        ok = hit if expect_hit else (n == 0 and not (c > 0 and "测试" not in q))
        if tag == "问负责人":
            ok = c > 0 or n > 0
        if tag == "智能检索无文档":
            ok = n == 0
        mark = "PASS" if ok else "FAIL"
        pass_n += mark == "PASS"
        fail_n += mark == "FAIL"
        print(
            f"[{mark}] {tag} | mode={mode} | {ms:.0f}ms | docs={n} contacts={c} "
            f"conf={data.get('confidence')} vec={top.get('vector_hit')} pct={top.get('match_percent')} | {q}"
        )
        if title:
            safe = title.encode("utf-8", "ignore").decode("utf-8")
            print(f"       top: {safe}")
        if data.get("advice") and n == 0:
            adv = (data.get("advice") or "")[:70].encode("utf-8", "ignore").decode("utf-8")
            print(f"       advice: {adv}")
        if mode == "owner" and data.get("owner_doc_hint"):
            print(f"       owner_doc_hint={data.get('owner_doc_hint')}")

    print("\n=== SEARCH (fast=0 含大模型) ===")
    llm_cases = [
        ("口语叠字+LLM", "all", "邮件设置页面 一直转圈圈"),
        ("偏难口语+LLM", "all", "打开邮件页面卡住转圈加载不出来"),
        ("操作绑定+LLM", "manual", "绑定邮箱"),
        ("无关问题+LLM", "all", "今天天气怎么样"),
        ("问负责人+LLM", "all", "孚盟邮的测试是谁"),
    ]
    llm_times = []
    for tag, mode, q in llm_cases:
        data, ms = search(q, mode=mode, fast=0)
        llm_times.append(ms)
        n = len(data.get("results") or [])
        c = len(data.get("contacts") or [])
        top = (data.get("results") or [{}])[0]
        title = (top.get("title") or "")[:42]
        print(
            f"{tag} | {ms:.0f}ms | llm_used={data.get('llm_used')} docs={n} contacts={c} "
            f"conf={data.get('confidence')} feedback={data.get('suggest_feedback')}"
        )
        if title:
            safe = title.encode("utf-8", "ignore").decode("utf-8")
            print(
                f"       top: {safe} | pct={top.get('match_percent')} vec={top.get('vector_hit')}"
            )
        if c:
            print(f"       contact0: {(data.get('contacts') or [{}])[0].get('module')}")
        if data.get("advice"):
            adv = (data.get("advice") or "")[:90].encode("utf-8", "ignore").decode("utf-8")
            print(f"       advice: {adv}")

    print("\n=== SUMMARY ===")
    print(f"case pass/fail = {pass_n}/{fail_n}")
    print(
        f"fast avg={mean(fast_times):.0f}ms  min={min(fast_times):.0f}ms  max={max(fast_times):.0f}ms"
    )
    print(
        f"llm  avg={mean(llm_times):.0f}ms  min={min(llm_times):.0f}ms  max={max(llm_times):.0f}ms"
    )


if __name__ == "__main__":
    main()
