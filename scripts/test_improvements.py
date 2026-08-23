#!/usr/bin/env python3
"""测试 6 大改进方案的验证脚本

运行方式：
    python scripts/test_improvements.py

前置条件：
    1. 服务已启动（python -m docmind.app）
    2. 至少配置一个搜索引擎（推荐 SERPER_API_KEY）
"""
import json
import time
from typing import Any

import requests

BASE_URL = "http://127.0.0.1:7860"
SESSION_ID = f"test_{int(time.time())}"


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    END = "\033[0m"


def print_section(title: str):
    print(f"\n{'=' * 60}")
    print(f"{Colors.BLUE}{title}{Colors.END}")
    print('=' * 60)


def print_test(name: str):
    print(f"\n{Colors.YELLOW}测试: {name}{Colors.END}")


def print_pass(msg: str):
    print(f"{Colors.GREEN}✓ {msg}{Colors.END}")


def print_fail(msg: str):
    print(f"{Colors.RED}✗ {msg}{Colors.END}")


def stream_chat(question: str) -> dict[str, Any]:
    """发送流式聊天请求，返回完整结果"""
    url = f"{BASE_URL}/api/chat/stream"
    data = {"question": question, "session_id": SESSION_ID}

    start_time = time.time()
    final_answer = ""
    events = []

    try:
        resp = requests.post(url, json=data, stream=True, timeout=60)
        resp.raise_for_status()

        for line in resp.iter_lines():
            if not line:
                continue
            line = line.decode('utf-8')
            if not line.startswith('data: '):
                continue

            try:
                event = json.loads(line[6:])
                events.append(event)
                if event.get("kind") == "final":
                    final_answer = event.get("answer", "")
            except json.JSONDecodeError:
                pass

        elapsed = time.time() - start_time
        return {
            "success": True,
            "answer": final_answer,
            "elapsed": elapsed,
            "events": events,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "elapsed": time.time() - start_time,
        }


def test_version_comparison():
    """测试 1：版本号比较（问题4+6：术语+歧义消解）"""
    print_test("版本号比较：3.9 vs 3.11")

    result = stream_chat("Python 3.9 和 3.11 哪个版本更新？")

    if not result["success"]:
        print_fail(f"请求失败: {result['error']}")
        return False

    answer = result["answer"].lower()

    # 检查是否正确识别 3.11 > 3.9
    if "3.11" in answer and ("更新" in answer or "大" in answer or ">" in answer):
        print_pass("正确识别 3.11 > 3.9")
    else:
        print_fail("未正确识别版本大小")
        print(f"回答: {result['answer'][:200]}")
        return False

    # 检查是否解释了版本号规则
    if "版本号" in answer or "按位" in answer or "次版本" in answer:
        print_pass("包含版本号比较规则解释")
    else:
        print_fail("未解释版本号规则")

    print(f"响应时间: {result['elapsed']:.2f}s")
    return True


def test_timeliness():
    """测试 2：时效性问题（问题5）"""
    print_test("时效性问题：今年/最新事件")

    result = stream_chat("2026年夏季奥运会在哪里举办？")

    if not result["success"]:
        print_fail(f"请求失败: {result['error']}")
        return False

    # 检查是否调用了 web_search
    web_search_called = any(
        e.get("kind") == "step" and "web_search" in e.get("text", "")
        for e in result["events"]
    )

    if web_search_called:
        print_pass("调用了联网搜索")
    else:
        print_fail("未调用联网搜索")
        return False

    # 检查是否有时效性声明
    answer = result["answer"]
    if "知识截止" in answer or "训练" in answer or "联网" in answer or "搜索" in answer:
        print_pass("包含时效性声明")
    else:
        print_fail("未包含时效性声明")

    print(f"响应时间: {result['elapsed']:.2f}s")
    print(f"答案摘要: {result['answer'][:150]}...")
    return True


def test_slang():
    """测试 3：俚语/黑话理解（问题4）"""
    print_test("俚语理解：钓鱼黑话")

    result = stream_chat("钓鱼中的'上岸报户拐老板'是什么意思？")

    if not result["success"]:
        print_fail(f"请求失败: {result['error']}")
        return False

    answer = result["answer"].lower()

    # 检查是否正确理解术语
    keywords = ["钓到", "渔获", "赚", "黑坑", "收费"]
    matched = sum(1 for kw in keywords if kw in answer)

    if matched >= 2:
        print_pass(f"正确理解钓鱼黑话（命中{matched}个关键词）")
    else:
        print_fail(f"未正确理解黑话（仅命中{matched}个关键词）")
        print(f"回答: {result['answer'][:200]}")
        return False

    print(f"响应时间: {result['elapsed']:.2f}s")
    return True


def test_web_search_speed():
    """测试 4：联网搜索速度（问题3）+ 缓存"""
    print_test("联网搜索速度与缓存")

    question = "LLM 是什么意思？"

    # 第一次请求
    print("第一次查询（冷启动）...")
    result1 = stream_chat(question)

    if not result1["success"]:
        print_fail(f"第一次请求失败: {result1['error']}")
        return False

    elapsed1 = result1["elapsed"]
    print(f"第一次响应时间: {elapsed1:.2f}s")

    if elapsed1 > 15:
        print_fail(f"响应时间过长（>{elapsed1:.2f}s），可能未启用并发优化")
    elif elapsed1 > 10:
        print(f"{Colors.YELLOW}⚠ 响应时间较长（{elapsed1:.2f}s），建议检查搜索引擎配置{Colors.END}")
    else:
        print_pass(f"响应速度良好（{elapsed1:.2f}s）")

    # 第二次请求（测试语义缓存）
    print("\n第二次查询（测试缓存）...")
    time.sleep(1)
    result2 = stream_chat(question)

    if not result2["success"]:
        print_fail(f"第二次请求失败: {result2['error']}")
        return False

    elapsed2 = result2["elapsed"]
    print(f"第二次响应时间: {elapsed2:.2f}s")

    # 检查是否命中缓存
    cache_hit = any(
        e.get("kind") == "cache"
        for e in result2["events"]
    )

    if cache_hit:
        print_pass(f"命中语义缓存，秒回（{elapsed2:.2f}s）")
    elif elapsed2 < elapsed1 * 0.5:
        print_pass(f"第二次明显更快（{elapsed1:.2f}s → {elapsed2:.2f}s）")
    else:
        print(f"{Colors.YELLOW}⚠ 未命中缓存，可能问题相似度不够或缓存未启用{Colors.END}")

    return True


def test_long_answer():
    """测试 5：长回答不截断（问题1）"""
    print_test("长回答防截断")

    result = stream_chat(
        "详细介绍 RAG 技术的完整实现方案，包括文档切片、向量检索、混合检索、"
        "重排序、上下文构建等各个环节的技术细节和优化技巧"
    )

    if not result["success"]:
        print_fail(f"请求失败: {result['error']}")
        return False

    answer = result["answer"]
    answer_length = len(answer)

    print(f"回答长度: {answer_length} 字符")

    # 检查是否完整（长回答通常 > 500 字）
    if answer_length > 500:
        print_pass("回答较完整，未被截断")
    elif answer_length < 200:
        print_fail("回答过短，可能被截断")
        return False
    else:
        print(f"{Colors.YELLOW}⚠ 回答长度适中（{answer_length}字），建议人工检查是否完整{Colors.END}")

    # 检查结尾是否完整（不以半句话结束）
    if answer.endswith(("。", "！", "？", ".", "!", "?")):
        print_pass("回答以完整句子结尾")
    else:
        print_fail(f"回答可能被截断，结尾：...{answer[-50:]}")
        return False

    print(f"响应时间: {result['elapsed']:.2f}s")
    return True


def test_ambiguity_resolution():
    """测试 6：歧义消解（问题6）"""
    print_test("歧义消解：多轮指代")

    # 先问一个问题建立上下文
    result1 = stream_chat("什么是 Docker？")
    if not result1["success"]:
        print_fail("建立上下文失败")
        return False

    print_pass("上下文已建立")

    # 使用指代词追问
    time.sleep(1)
    result2 = stream_chat("它的主要优势是什么？")

    if not result2["success"]:
        print_fail(f"追问失败: {result2['error']}")
        return False

    answer = result2["answer"].lower()

    # 检查是否正确理解指代（回答应该包含 Docker 相关内容）
    docker_keywords = ["docker", "容器", "镜像", "隔离"]
    matched = sum(1 for kw in docker_keywords if kw in answer)

    if matched >= 2:
        print_pass(f"正确理解指代词'它'（命中{matched}个相关词）")
    else:
        print_fail(f"未能正确理解指代（仅命中{matched}个相关词）")
        print(f"回答: {result2['answer'][:200]}")
        return False

    # 检查是否有查询改写
    rewrite_detected = any(
        e.get("kind") == "rewrite"
        for e in result2["events"]
    )

    if rewrite_detected:
        print_pass("检测到查询改写（多轮消解）")
    else:
        print(f"{Colors.YELLOW}⚠ 未检测到查询改写，可能直接理解成功{Colors.END}")

    print(f"响应时间: {result2['elapsed']:.2f}s")
    return True


def main():
    print(f"\n{Colors.BLUE}{'=' * 60}")
    print("DocMind 质量改进验证测试")
    print(f"{'=' * 60}{Colors.END}")
    print(f"测试目标: {BASE_URL}")
    print(f"会话 ID: {SESSION_ID}")

    # 检查服务是否可用
    try:
        requests.get(f"{BASE_URL}/", timeout=5)
        print_pass("服务连接正常")
    except Exception as e:
        print_fail(f"无法连接到服务: {e}")
        print("\n请先启动服务: python -m docmind.app")
        return

    # 运行所有测试
    tests = [
        ("问题1: 长回答防截断", test_long_answer),
        ("问题3: 联网搜索速度", test_web_search_speed),
        ("问题4: 术语/俚语理解", test_slang),
        ("问题5: 时效性数据", test_timeliness),
        ("问题6: 歧义消解（版本号）", test_version_comparison),
        ("问题6: 歧义消解（多轮指代）", test_ambiguity_resolution),
    ]

    results = {}

    for name, test_func in tests:
        print_section(name)
        try:
            results[name] = test_func()
        except Exception as e:
            print_fail(f"测试异常: {e}")
            results[name] = False

        time.sleep(2)  # 避免请求过快

    # 总结
    print_section("测试总结")
    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, result in results.items():
        status = f"{Colors.GREEN}通过{Colors.END}" if result else f"{Colors.RED}失败{Colors.END}"
        print(f"{name}: {status}")

    print(f"\n通过率: {passed}/{total} ({passed*100//total}%)")

    if passed == total:
        print(f"\n{Colors.GREEN}🎉 所有测试通过！改进方案验证成功。{Colors.END}")
    elif passed >= total * 0.7:
        print(f"\n{Colors.YELLOW}⚠ 大部分测试通过，但仍有改进空间。{Colors.END}")
    else:
        print(f"\n{Colors.RED}❌ 多项测试失败，请检查配置和实现。{Colors.END}")

    print("\n详细改进文档: docs/IMPROVEMENTS_2026-08-21.md")


if __name__ == "__main__":
    main()
