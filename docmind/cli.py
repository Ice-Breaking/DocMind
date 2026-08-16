"""命令行入口：不开 GUI，直接在终端对话（方便调试）。

用法：python -m docmind.cli
"""
from docmind.core import build_agent


def main():
    print("正在装配 Agent...")
    agent, store, connections = build_agent()
    print(f"可用工具: {', '.join(agent.registry.tools.keys())}\n")
    print("DocMind 已就绪（输入 quit 退出，输入 reset 重置对话）\n")

    while True:
        try:
            question = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            continue
        if question.lower() in {"quit", "exit", "q"}:
            break
        if question.lower() == "reset":
            agent.reset()
            print("(对话已重置)\n")
            continue

        print("DocMind > ", end="", flush=True)
        streamed = False
        for step in agent.ask(question):
            if step.kind == "token":
                streamed = True
                print(step.text, end="", flush=True)
            elif step.kind == "final":
                if streamed:
                    print("\n")
                else:
                    print(f"\n{step.text}\n")
            else:
                print(f"\n  [{step.kind}] {step.text}", end="", flush=True)


if __name__ == "__main__":
    main()
