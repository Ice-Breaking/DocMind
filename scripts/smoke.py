#!/usr/bin/env python3
"""DocMind 部署冒烟测试（<1 分钟，无真实 LLM 依赖）。

定位：部署后第一时间验证「系统能跑起来且版本正确」——单测覆盖不了
的部署形态问题（QA 实测：doc_dir 跨环境路径 / anyio 版本不兼容 /
镜像版本漂移均在此层暴露，单测全绿）。

用法：
    python scripts/smoke.py                          # 默认打本机 7860 + nginx 80
    python scripts/smoke.py --base http://host:7860  # 指定后端
    python scripts/smoke.py --skip-version-check     # 跨机验证时跳过指纹比对

退出码：全部 PASS → 0；任一 FAIL → 1（可直接接 CI gate）。
"""
import argparse
import glob
import hashlib
import os
import sys
import time

import requests

RESULTS = []


def rec(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def warn(name, detail):
    RESULTS.append((name, None, detail))
    print(f"[SKIP] {name} — {detail}")


def workspace_fingerprint() -> str | None:
    """工作区源码指纹（与 Dockerfile 构建指纹同算法），用于漂移检测"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = sorted(glob.glob(os.path.join(root, "docmind", "**", "*.py"), recursive=True)
                   + glob.glob(os.path.join(root, "mcp_servers", "**", "*.py"),
                               recursive=True))
    if not files:
        return None
    h = hashlib.sha256()
    for f in files:
        h.update(open(f, "rb").read())
    return h.hexdigest()[:12]


def read_admin_password(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.getenv("ADMIN_PASSWORD")
    if env:
        return env
    env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            ".env")
    if os.path.isfile(env_file):
        for line in open(env_file, encoding="utf-8"):
            if line.startswith("ADMIN_PASSWORD="):
                return line.split("=", 1)[1].strip()
    return ""


def wait_healthy(base: str, timeout_s: int = 120) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = requests.get(f"{base}/health", timeout=5)
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(3)
    return False


def sse_events(sess: requests.Session, url: str, question: str,
               timeout_s: float = 45) -> tuple[int, list[str], int]:
    """发起 SSE 请求，收集事件种类（内容无关——无 LLM Key 时服务端
    也会产出 error/final 事件，冒烟只验证链路通）"""
    t0 = time.time()
    try:
        r = sess.post(url, json={"question": question, "session_id": ""},
                      stream=True, timeout=timeout_s,
                      headers={"Accept": "text/event-stream"})
    except requests.RequestException:
        return -1, [], int((time.time() - t0) * 1000)
    if r.status_code != 200:
        return r.status_code, [], 0
    kinds, cur = [], None
    for raw in r.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        if raw.startswith("event: "):
            cur = raw[7:].strip()
        elif raw.startswith("data: ") and cur:
            if cur not in kinds:
                kinds.append(cur)
            if cur == "done":
                break
    return r.status_code, kinds, int((time.time() - t0) * 1000)


def main() -> int:
    ap = argparse.ArgumentParser(description="DocMind 部署冒烟测试")
    ap.add_argument("--base", default="http://127.0.0.1:7860", help="后端直连地址")
    ap.add_argument("--nginx", default="http://localhost",
                    help="nginx 入口（空串跳过 nginx 检查）")
    ap.add_argument("--admin-password", default=None,
                    help="admin 密码（缺省读 ADMIN_PASSWORD 环境变量或 .env）")
    ap.add_argument("--skip-version-check", action="store_true",
                    help="跳过容器/工作区指纹比对（跨机验证部署时使用）")
    ap.add_argument("--health-timeout", type=int, default=120,
                    help="等待服务就绪的秒数")
    args = ap.parse_args()

    print(f"== DocMind 冒烟: base={args.base} nginx={args.nginx or '(跳过)'} ==")

    # 0) 服务就绪等待（部署后立即跑的场景）
    if not wait_healthy(args.base, args.health_timeout):
        rec("服务就绪", False, f"{args.base}/health 在 {args.health_timeout}s 内未就绪")
        return summarize()
    rec("服务就绪", True)

    # 1) 健康检查 + 版本字段
    r = requests.get(f"{args.base}/health", timeout=10)
    body = r.json() if r.status_code == 200 else {}
    rec("/health 200 + healthy", r.status_code == 200
        and body.get("status") == "healthy", f"got {r.status_code} {body.get('status')}")
    version = body.get("version")
    rec("/health 暴露 version 指纹", bool(version), f"version={version}")

    # 2) 版本漂移检测：容器指纹 vs 工作区指纹
    if args.skip_version_check:
        warn("版本指纹比对", "已跳过（--skip-version-check）")
    elif version and workspace_fingerprint():
        local = workspace_fingerprint()
        rec("容器指纹 == 工作区指纹（无部署漂移）", version == local,
            f"container={version} workspace={local}")
    else:
        warn("版本指纹比对", f"无法比对（container={version}）")

    # 3) nginx 入口
    if args.nginx:
        try:
            r = requests.get(f"{args.nginx}/", timeout=10)
            rec("nginx SPA 入口 200", r.status_code == 200, f"got {r.status_code}")
            # 静态资源可达（从 index.html 解析首个模块脚本）
            asset_ok, asset_detail = False, ""
            for line in r.text.split("\n"):
                if 'src="/assets/' in line:
                    src = line.split('src="')[1].split('"')[0]
                    ar = requests.get(f"{args.nginx}{src}", timeout=10)
                    asset_ok, asset_detail = ar.status_code == 200, src
                    break
            rec("前端静态资源可达", asset_ok, asset_detail)
            r = requests.get(f"{args.nginx}/health", timeout=10)
            rec("nginx API 反代透传", r.status_code == 200, f"got {r.status_code}")
        except requests.RequestException as e:
            rec("nginx 入口可达", False, str(e)[:80])
    else:
        warn("nginx 检查", "已跳过（--nginx 为空）")

    # 4) 认证
    pwd = read_admin_password(args.admin_password)
    if not pwd:
        warn("登录检查", "未提供 admin 密码（--admin-password / ADMIN_PASSWORD / .env）")
    else:
        s = requests.Session()
        r = s.post(f"{args.base}/login",
                   data={"username": "admin", "password": pwd}, timeout=10)
        rec("admin 登录", r.status_code == 200, f"got {r.status_code}")
        r = s.get(f"{args.base}/api/me", timeout=10)
        rec("/api/me 会话有效", r.status_code == 200 and r.json().get("user") == "admin")
        r = requests.get(f"{args.base}/api/sessions", timeout=10)
        rec("未登录访问 401", r.status_code == 401, f"got {r.status_code}")
        r = s.get(f"{args.base}/api/kbs", timeout=10)
        rec("知识库列表", r.status_code == 200, f"{len(r.json())} 个库")

        # 5) SSE 链路（内容无关：无 LLM Key 时也产出 error/final 事件）
        code, kinds, ms = sse_events(s, f"{args.base}/api/chat/stream", "hi")
        rec("SSE 直连后端（事件到达）", code == 200 and "final" in kinds and "done" in kinds,
            f"http={code} events={kinds} {ms}ms")
        if args.nginx:
            # cookie 按域隔离：nginx 入口需独立登录（127.0.0.1 与
            # localhost 的 cookie 互不携带——QA 实测踩过）
            s_nginx = requests.Session()
            s_nginx.post(f"{args.nginx}/login",
                         data={"username": "admin", "password": pwd}, timeout=10)
            code, kinds, ms = sse_events(
                s_nginx, f"{args.nginx}/api/chat/stream", "hi")
            rec("SSE 经 nginx（不被缓冲）",
                code == 200 and "final" in kinds and "done" in kinds,
                f"http={code} events={kinds} {ms}ms")

    # 6) 开放 API 鉴权边界
    r = requests.post(f"{args.base}/open/v1/retrieve", json={"question": "x"}, timeout=10)
    rec("开放 API 无凭证 401", r.status_code == 401, f"got {r.status_code}")

    # 7) metrics 非 5xx（未配 token 时本机 200，其他来源 404——均合法）
    try:
        r = requests.get(f"{args.base}/metrics", timeout=10)
        rec("/metrics 非 5xx", r.status_code < 500, f"got {r.status_code}")
    except requests.RequestException as e:
        rec("/metrics 非 5xx", False, str(e)[:80])

    return summarize()


def summarize() -> int:
    fails = [r for r in RESULTS if r[1] is False]
    print(f"\n==== 冒烟结果: {len(RESULTS)} 项, {len(fails)} FAIL ====")
    for name, ok, d in fails:
        print(f"  FAIL {name}: {d}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
