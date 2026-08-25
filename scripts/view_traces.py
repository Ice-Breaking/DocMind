"""本地调用链日志查看器（未配置 Langfuse 时使用）。

用法：PYTHONPATH=. .venv/bin/python scripts/view_traces.py [显示最近 N 条，默认 20]
"""
import sys



def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    from docmind import trace_store
    records = trace_store.recent_events(limit=n)
    if not records:
        print("暂无调用链日志（先跑一轮对话）")
        return

    total_tokens_in, total_tokens_out = 0, 0
    for r in records:
        icon = "🤖" if r.get("kind") == "generation" else "🔧"
        usage = r.get("usage")
        usage_str = f" tokens={usage['input']}+{usage['output']}" if usage else ""
        if usage:
            total_tokens_in += usage.get("input", 0)
            total_tokens_out += usage.get("output", 0)
        out = str(r.get("output", ""))[:60].replace("\n", " ")
        status = "❌" if r.get("status") == "error" else ""
        print(f"{r.get('ts','')} {icon} {r.get('name',''):<28} "
              f"{r.get('duration_ms', 0):>6}ms {status}{usage_str} | {out}")

    print(f"\n--- 最近 {min(n, len(records))} 条 | LLM 累计 tokens: "
          f"输入 {total_tokens_in} + 输出 {total_tokens_out} ---")


if __name__ == "__main__":
    main()
