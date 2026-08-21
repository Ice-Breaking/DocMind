#!/usr/bin/env python3
"""带认证的测试脚本

使用方式：
    python scripts/test_improvements_with_auth.py [username] [password]

默认使用 admin/admin123
"""
import json
import sys
import time
from typing import Any

import requests

BASE_URL = "http://127.0.0.1:7860"
USERNAME = sys.argv[1] if len(sys.argv) > 1 else "admin"
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else "admin123"
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


def login() -> requests.Session:
    """登录并返回会话"""
    session = requests.Session()
    try:
        resp = session.post(
            f"{BASE_URL}/login",
            data={"username": USERNAME, "password": PASSWORD},
            timeout=10
        )
        if resp.status_code == 200:
            print_pass(f"登录成功：{USERNAME}")
            return session
        else:
            print_fail(f"登录失败：{resp.status_code}")
            return None
    except Exception as e:
        print_fail(f"登录异常：{e}")
        return None


def stream_chat(session: requests.Session, question: str) -> dict[str, Any]:
    """发送流式聊天请求"""
    url = f"{BASE_URL}/api/chat/stream"
    data = {"question": question, "session_id": SESSION_ID}

    start_time = time.time()
    final_answer = ""
    events = []

    try:
        resp = session.post(url, json=data, stream=True, timeout=90)
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


def test_version_comparison(session: requests.Session):
    """测试：版本号比较"""
    print_test("版本号比较：3.9 vs 3.11")

    result = stream_chat(session, "Python 3.9 和 3.11 哪个版本更新？")

    if not result["success"]:
        print_fail(f"请求失败: {result.get('error', 'Unknown')}")
        return False

    answer = result["answer"].lower()

    if "3.11" in answer and any(kw in answer for kw in ["更新", "新", "大", ">"]):
        print_pass("✓ 正确识别 3.11 > 3.9")

        if any(kw in answer for kw in ["版本号", "按位", "次版本", "比较"]):
            print_pass("✓ 包含版本号规则解释")
        else:
            print(f"{Colors.YELLOW}⚠ 未明确解释版本号规则{Colors.END}")
    else:
        print_fail("✗ 未正确识别版本大小")
        print(f"回答前200字: {result['answer'][:200]}")
        return False

    print(f"响应时间: {result['elapsed']:.1f}s")
    return True


def test_timeliness(session: requests.Session):
    """测试：时效性"""
    print_test("时效性：2026年事件")

    result = stream_chat(session, "2026年夏季奥运会在哪里举办？")

    if not result["success"]:
        print_fail(f"请求失败: {result.get('error')}")
        return False

    web_search_called = any(
        e.get("kind") == "step" and "web_search" in e.get("text", "")
        for e in result["events"]
    )

    if web_search_called:
        print_pass("✓ 调用了联网搜索")
    else:
        print_fail("✗ 未调用联网搜索")
        return False

    answer = result["answer"]
    if any(kw in answer for kw in ["知识截止", "训练", "联网", "搜索", "最新"]):
        print_pass("✓ 包含时效性声明")
    else:
        print(f"{Colors.YELLOW}⚠ 缺少时效性声明{Colors.END}")

    print(f"响应时间: {result['elapsed']:.1f}s")
    return True


def test_slang(session: requests.Session):
    """测试：俚语理解"""
    print_test("俚语：钓鱼黑话")

    result = stream_chat(session, "钓鱼中的'上岸报户拐老板'是什么意思？")

    if not result["success"]:
        print_fail(f"请求失败: {result.get('error')}")
        return False

    answer = result["answer"].lower()
    keywords = ["钓", "鱼", "渔获", "黑坑", "赚", "老板"]
    matched = sum(1 for kw in keywords if kw in answer)

    if matched >= 3:
        print_pass(f"✓ 正确理解黑话（{matched}/6 关键词）")
    else:
        print_fail(f"✗ 理解不足（仅{matched}/6）")
        print(f"回答: {result['answer'][:200]}")
        return False

    print(f"响应时间: {result['elapsed']:.1f}s")
    return True


def test_web_search_speed(session: requests.Session):
    """测试：搜索速度+缓存"""
    print_test("联网搜索速度与缓存")

    question = "RAG 是什么意思？"

    print("第一次查询...")
    result1 = stream_chat(session, question)

    if not result1["success"]:
        print_fail(f"第一次失败: {result1.get('error')}")
        return False

    elapsed1 = result1["elapsed"]
    print(f"第一次: {elapsed1:.1f}s", end="")

    if elapsed1 < 8:
        print(f" {Colors.GREEN}(快){Colors.END}")
    elif elapsed1 < 15:
        print(f" {Colors.YELLOW}(正常){Colors.END}")
    else:
        print(f" {Colors.RED}(慢){Colors.END}")

    print("\n第二次查询（测试缓存）...")
    time.sleep(1)
    result2 = stream_chat(session, question)

    if not result2["success"]:
        print_fail(f"第二次失败: {result2.get('error')}")
        return False

    elapsed2 = result2["elapsed"]
    cache_hit = any(e.get("kind") == "cache" for e in result2["events"])

    if cache_hit:
        print_pass(f"✓ 命中语义缓存（{elapsed2:.1f}s）")
    elif elapsed2 < 3:
        print_pass(f"✓ 第二次快速响应（{elapsed2:.1f}s）")
    else:
        print(f"{Colors.YELLOW}⚠ 未明显加速（{elapsed1:.1f}s → {elapsed2:.1f}s）{Colors.END}")

    return True


def test_long_answer(session: requests.Session):
    """测试：长回答不截断"""
    print_test("长回答防截断")

    result = stream_chat(
        session,
        "详细介绍 RAG 技术，包括文档切片、向量检索、混合检索、重排序等各环节"
    )

    if not result["success"]:
        print_fail(f"请求失败: {result.get('error')}")
        return False

    answer = result["answer"]
    length = len(answer)

    print(f"回答长度: {length} 字符")

    if length > 500:
        print_pass("✓ 回答较完整")
    elif length < 200:
        print_fail("✗ 回答过短，可能截断")
        return False
    else:
        print(f"{Colors.YELLOW}⚠ 长度适中（{length}字）{Colors.END}")

    if answer.rstrip().endswith(("。", "！", "？", ".", "!", "?")):
        print_pass("✓ 完整句子结尾")
    else:
        print_fail(f"✗ 可能截断，结尾: ...{answer[-30:]}")
        return False

    print(f"响应时间: {result['elapsed']:.1f}s")
    return True


def test_ambiguity(session: requests.Session):
    """测试：歧义消解"""
    print_test("多轮指代消解")

    result1 = stream_chat(session, "什么是 Docker？")
    if not result1["success"]:
        print_fail("上下文建立失败")
        return False

    print_pass("上下文已建立")

    time.sleep(1)
    result2 = stream_chat(session, "它的主要优势是什么？")

    if not result2["success"]:
        print_fail(f"追问失败: {result2.get('error')}")
        return False

    answer = result2["answer"].lower()
    keywords = ["docker", "容器", "镜像", "隔离", "轻量"]
    matched = sum(1 for kw in keywords if kw in answer)

    if matched >= 2:
        print_pass(f"✓ 正确理解指代'它'（{matched}/5 关键词）")
    else:
        print_fail(f"✗ 指代理解失败（仅{matched}/5）")
        return False

    rewrite = any(e.get("kind") == "rewrite" for e in result2["events"])
    if rewrite:
        print_pass("✓ 检测到查询改写")
    else:
        print(f"{Colors.YELLOW}⚠ 未检测到改写（可能直接理解）{Colors.END}")

    print(f"响应时间: {result2['elapsed']:.1f}s")
    return True


def main():
    print(f"\n{Colors.BLUE}{'=' * 60}")
    print("DocMind 质量改进验证测试（带认证）")
    print(f"{'=' * 60}{Colors.END}")
    print(f"目标: {BASE_URL}")
    print(f"用户: {USERNAME}")
    print(f"会话: {SESSION_ID}")

    # 登录
    print_section("登录")
    session = login()
    if not session:
        print("\n请检查：")
        print("1. 服务是否运行：python -m docmind.app")
        print("2. 用户名密码是否正确")
        return

    # 测试列表
    tests = [
        ("问题1: 长回答防截断", test_long_answer),
        ("问题3: 联网搜索速度+缓存", test_web_search_speed),
        ("问题4: 术语/俚语理解", test_slang),
        ("问题5: 时效性数据", test_timeliness),
        ("问题6: 歧义消解-版本号", test_version_comparison),
        ("问题6: 歧义消解-多轮指代", test_ambiguity),
    ]

    results = {}

    for name, test_func in tests:
        print_section(name)
        try:
            results[name] = test_func(session)
        except Exception as e:
            print_fail(f"测试异常: {e}")
            import traceback
            traceback.print_exc()
            results[name] = False

        time.sleep(2)

    # 总结
    print_section("测试总结")
    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, result in results.items():
        icon = "✓" if result else "✗"
        color = Colors.GREEN if result else Colors.RED
        print(f"{color}{icon} {name}{Colors.END}")

    rate = passed * 100 // total if total > 0 else 0
    print(f"\n通过率: {passed}/{total} ({rate}%)")

    if passed == total:
        print(f"\n{Colors.GREEN}🎉 所有测试通过！{Colors.END}")
    elif rate >= 70:
        print(f"\n{Colors.YELLOW}⚠ 大部分通过，仍有改进空间{Colors.END}")
    else:
        print(f"\n{Colors.RED}❌ 多项失败，请检查配置{Colors.END}")

    print("\n配置检查清单：")
    print("□ SERPER_API_KEY 或其他搜索引擎已配置")
    print("□ MAX_OUTPUT_TOKENS=2000")
    print("□ docs/glossary.md 已更新")
    print("\n详细文档: docs/IMPROVEMENTS_2026-08-21.md")


if __name__ == "__main__":
    main()
