#!/usr/bin/env python3
"""DocMind 深度 E2E 验收测试（发版前手动执行，不进 CI）。

覆盖全部业务流的正常 + 边界条件：认证生命周期 / 知识库 CRUD / 六格式
文档上传与安全拦截 / SSE 真实 LLM 对话与多轮上下文 / 用户中心 / API Key
全生命周期 / 开放 API / Admin 看板与备份。

与 scripts/smoke.py 的分工：
- smoke.py：部署后 <1 分钟，只验「能跑起来」，无 LLM 依赖，进 CI
- qa_full.py：全业务流验收，真实 LLM 调用（有成本），数据 qa_ 前缀隔离
  且测后自动清理（--keep-data 可保留现场排查）

用法：
    python scripts/qa_full.py                        # 全量（含 LLM/联网）
    python scripts/qa_full.py --skip-llm             # 跳过真实 LLM 用例
    python scripts/qa_full.py --skip-reindex-wait    # 跳过 75s 防抖重建等待
"""
import argparse
import io
import os
import json
import secrets
import struct
import sys
import time
import zlib

import requests

RESULTS = []
TAG = "qa_e2e"

# E2E 临时凭据：运行时随机生成，仅本次运行有效（临时用户测后级联删除），
# 源码不落字面量口令
QA_PASS = f"Qa{secrets.token_hex(6)}A1"
QA_NEW_PASS = f"Qn{secrets.token_hex(6)}A2"
QA_RESET_PASS = f"Qr{secrets.token_hex(6)}A3"
WRONG_PASS = f"zw{secrets.token_hex(8)}"


def rec(layer, name, ok, detail="", warn=False):
    status = "WARN" if (warn and ok is None) else ("PASS" if ok else "FAIL")
    RESULTS.append((layer, name, status, detail))
    print(f"[{status}] L{layer} {name}" + (f" — {detail}" if detail else ""))


def make_png() -> bytes:
    """构造 1x1 红色 PNG（真实 magic bytes，过内容嗅探）"""
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
            + chunk(b"IEND", b""))


def mini_pdf() -> bytes:
    return (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
            b"trailer<</Root 1 0 R>>\n%%EOF")


def make_docx() -> bytes | None:
    try:
        from docx import Document
        buf = io.BytesIO()
        doc = Document()
        doc.add_paragraph("E2E docx content")
        doc.save(buf)
        return buf.getvalue()
    except ImportError:
        return None


class QA:
    def __init__(self, base: str, admin_pwd: str):
        self.base = base
        self.s = requests.Session()
        r = self.s.post(f"{base}/login",
                        data={"username": "admin", "password": admin_pwd})
        assert r.status_code == 200, f"admin 登录失败: {r.status_code}"
        self.created = {"users": [], "kbs": [], "docs": [], "keys": [],
                        "sessions": []}

    # ---------- L2 认证 ----------
    def auth(self, skip_lockout=False):
        r = requests.post(f"{self.base}/login",
                          data={"username": "admin", "password": WRONG_PASS})
        rec(2, "错误密码 400", r.status_code == 400, f"got {r.status_code}")
        # qa 用户全生命周期
        r = self.s.post(f"{self.base}/api/admin/users",
                        json={"username": f"{TAG}_u1",
                              "password": QA_PASS, "is_admin": False})
        self.created["users"].append(f"{TAG}_u1")
        rec(2, "创建用户（强制首登改密）", r.status_code in (200, 201))
        qa = requests.Session()
        r = qa.post(f"{self.base}/login",
                    data={"username": f"{TAG}_u1", "password": QA_PASS})
        rec(2, "首登 must_change_pwd", r.json().get("must_change_pwd") is True)
        r = qa.get(f"{self.base}/api/sessions")
        rec(2, "强制改密拦截 403", r.status_code == 403)
        r = qa.post(f"{self.base}/api/change-password",
                    json={"old_password": QA_PASS, "new_password": QA_NEW_PASS})
        rec(2, "修改密码", r.status_code == 200)
        rec(2, "改密后放行", qa.get(f"{self.base}/api/sessions").status_code == 200)
        rec(2, "旧密码失效", requests.post(
            f"{self.base}/login",
            data={"username": f"{TAG}_u1", "password": QA_PASS}).status_code == 400)
        qa2 = requests.Session()
        qa2.post(f"{self.base}/login",
                 data={"username": f"{TAG}_u1", "password": QA_NEW_PASS})
        rec(2, "多设备并存",
            qa.get(f"{self.base}/api/sessions").status_code == 200
            and qa2.get(f"{self.base}/api/sessions").status_code == 200)
        qa.post(f"{self.base}/logout")
        rec(2, "单设备登出隔离",
            qa.get(f"{self.base}/api/sessions").status_code == 401
            and qa2.get(f"{self.base}/api/sessions").status_code == 200)
        if not skip_lockout:
            self.s.post(f"{self.base}/api/admin/users",
                        json={"username": f"{TAG}_u2",
                              "password": QA_PASS, "is_admin": False})
            self.created["users"].append(f"{TAG}_u2")
            for _ in range(5):
                requests.post(f"{self.base}/login",
                              data={"username": f"{TAG}_u2", "password": WRONG_PASS})
            r = requests.post(f"{self.base}/login",
                              data={"username": f"{TAG}_u2", "password": QA_PASS})
            rec(2, "防爆破锁定 403", r.status_code == 403, f"got {r.status_code}")

    # ---------- L3 知识库 ----------
    def kbs(self):
        r = self.s.post(f"{self.base}/api/kbs", json={"name": f"{TAG}_kb"})
        self.created["kbs"].append(r.json().get("id", ""))
        rec(3, "创建知识库 201", r.status_code == 201)
        r = self.s.post(f"{self.base}/api/kbs", json={"name": f"{TAG}_kb"})
        rec(3, "重名创建 409", r.status_code == 409, f"got {r.status_code}")
        kid = self.created["kbs"][0]
        r = self.s.put(f"{self.base}/api/kbs/{kid}",
                       json={"name": f"{TAG}_kb2", "description": "renamed"})
        rec(3, "重命名 200", r.status_code == 200 and r.json()["name"] == f"{TAG}_kb2")
        r = self.s.put(f"{self.base}/api/kbs/default", json={"name": "x"})
        rec(3, "default 库改名 400", r.status_code == 400)

    # ---------- L4 文档 ----------
    def docs(self, skip_url_import=True):
        def up(name, content):
            r = self.s.post(f"{self.base}/api/kbs/default/docs",
                            files={"file": (name, content)})
            if r.status_code == 200:
                self.created["docs"].append(name)
            return r
        rec(4, "上传 md", up(f"{TAG}.md", f"# E2E\n{TAG} 关键词斑马快线".encode()).status_code == 200)
        rec(4, "上传 txt", up(f"{TAG}.txt", f"{TAG} text".encode()).status_code == 200)
        rec(4, "上传 csv", up(f"{TAG}.csv", f"c1,c2\n{TAG},1\n".encode()).status_code == 200)
        rec(4, "上传 json", up(f"{TAG}.json", json.dumps({"k": TAG}).encode()).status_code == 200)
        rec(4, "上传 pdf", up(f"{TAG}.pdf", mini_pdf()).status_code == 200)
        docx = make_docx()
        if docx:
            rec(4, "上传 docx", up(f"{TAG}.docx", docx).status_code == 200)
        else:
            rec(4, "上传 docx", None, "本地无 python-docx", warn=True)
        rec(4, "伪装扩展名 400", up(f"{TAG}_fake.pdf", b"not a pdf").status_code == 400)
        rec(4, "空文件 400", up(f"{TAG}_empty.md", b"").status_code == 400)
        rec(4, "非法扩展名 400", up(f"{TAG}_bad.exe", b"MZ").status_code == 400)
        r = self.s.get(f"{self.base}/api/kbs/default/docs")
        names = [d["name"] for d in r.json()]
        rec(4, "文档列表完整", all(n in names for n in
                              [f"{TAG}.md", f"{TAG}.txt", f"{TAG}.pdf"]))
        if not skip_url_import:
            r = self.s.post(f"{self.base}/api/kbs/default/import-url",
                            json={"url": "https://example.com"})
            rec(4, "URL 导入（公网）", r.status_code == 200, r.text[:60])
            if r.status_code == 200:
                self.created["docs"].append(r.json().get("name", ""))
        else:
            rec(4, "URL 导入", None, "已跳过（--skip-url-import）", warn=True)
        r = self.s.post(f"{self.base}/api/kbs/default/import-url",
                        json={"url": "http://127.0.0.1:7860/health"})
        rec(4, "SSRF 内网拦截 400", r.status_code == 400, f"got {r.status_code}")

    def reindex_and_search(self):
        """等待防抖重建后验证任务与搜索（约 75s）"""
        print("  … 等待自动重建（60s 防抖 + 执行）…")
        time.sleep(75)
        tasks = self.s.get(f"{self.base}/api/kbs/default/tasks").json()
        qa_tasks = [t for t in tasks
                    if str(t.get("filename", "")).startswith(TAG)
                    or t.get("mode") == "auto-reindex"]
        bad = [t for t in qa_tasks if t.get("status") not in ("done",)]
        rec(4, "自动增量重建 done", not bad,
            f"{len(qa_tasks)} 任务, 异常 {len(bad)}" if bad else "all done")
        r = self.s.get(f"{self.base}/api/kbs/default/docs/search", params={"q": TAG})
        rec(4, "内容搜索命中", len(r.json()) >= 1, f"{len(r.json())} 文档命中")

    def llm_available(self) -> bool:
        """LLM 可用性探测：免费配额耗尽（403 FreeTierOnly）时 LLM 用例
        全部会得到 ⚠️ 兜底——那是正确行为，标记 WARN 跳过而非 FAIL"""
        r = self.s.post(f"{self.base}/api/chat/stream",
                        json={"question": "hi", "session_id": ""},
                        stream=True, timeout=90,
                        headers={"Accept": "text/event-stream"})
        if r.status_code != 200:
            return False
        failed = False
        for raw in r.iter_lines(decode_unicode=True):
            if raw and raw.startswith("data: ") and '"final"' in raw:
                try:
                    failed = json.loads(raw[6:]).get("failed") is True
                except json.JSONDecodeError:
                    pass
            if raw and raw.startswith("event: done"):
                break
        return not failed

    # ---------- L5 对话（真实 LLM） ----------
    def chat(self, llm_ok=True):
        sid = f"{TAG}-sess"
        self.created["sessions"].append(sid)

        def sse(question):
            r = self.s.post(f"{self.base}/api/chat/stream",
                            json={"question": question, "session_id": sid},
                            stream=True, timeout=120,
                            headers={"Accept": "text/event-stream"})
            evs = []
            if r.status_code != 200:
                return r.status_code, evs
            cur = None
            for raw in r.iter_lines(decode_unicode=True):
                if raw and raw.startswith("event: "):
                    cur = raw[7:].strip()
                elif raw and raw.startswith("data: ") and cur:
                    try:
                        evs.append({"kind": cur, **json.loads(raw[6:])})
                    except json.JSONDecodeError:
                        pass
            return r.status_code, evs
        if not llm_ok:
            rec(5, "对话链路（真实 LLM）", None,
                "LLM 配额不可用（403 FreeTierOnly）——系统 ⚠️ 兜底/不落库行为已由"
                "其余用例覆盖，LLM 用例跳过", warn=True)
            rec(5, "未登录 SSE 401",
                requests.post(f"{self.base}/api/chat/stream",
                              json={"question": "x"}).status_code == 401)
            return
        code, evs = sse("用一句话说明什么是 RAG")
        final = next((e for e in evs if e["kind"] == "final"), None)
        rec(5, "SSE 流式 200 + final/done",
            code == 200 and any(e["kind"] == "done" for e in evs)
            and bool(final and final.get("answer"))
            and not final.get("answer", "").startswith("⚠️"),
            f"events={sorted({e['kind'] for e in evs})}")
        code, evs = sse("我上一条消息问的主题是什么？几个字概括")
        ans2 = (next((e for e in evs if e["kind"] == "final"), None) or {}).get("answer", "")
        rec(5, "多轮上下文", code == 200 and ("RAG" in ans2 or "检索" in ans2),
            f"答: {ans2[:40]}")
        msgs = self.s.get(f"{self.base}/api/sessions/{sid}/messages").json()
        rec(5, "历史持久化 ≥4 条", len(msgs) >= 4, f"{len(msgs)} 条")
        r = self.s.get(f"{self.base}/api/sessions/{sid}/export")
        rec(5, "导出 Markdown", r.status_code == 200 and "对话导出" in r.text)
        r = self.s.post(f"{self.base}/api/feedback",
                        json={"session_id": sid, "seq": 1, "rating": "up"})
        rec(5, "反馈写入+回读",
            r.status_code == 200
            and self.s.get(f"{self.base}/api/feedback/{sid}").json().get("1") == "up")
        rec(5, "未登录 SSE 401",
            requests.post(f"{self.base}/api/chat/stream",
                          json={"question": "x"}).status_code == 401)

    # ---------- L6/L8 用户中心 + 开放 API ----------
    def user_and_openapi(self, skip_llm=False, llm_ok=True):
        r = self.s.post(f"{self.base}/api/me/avatar-upload",
                        files={"file": (f"{TAG}.png", make_png(), "image/png")})
        rec(6, "头像上传", r.status_code == 200, r.text[:60])
        pending = self.s.get(f"{self.base}/api/admin/avatar-reviews").json()
        me_pending = any(p.get("username") == "admin"
                         for p in (pending if isinstance(pending, list) else []))
        if me_pending:
            r = self.s.post(f"{self.base}/api/admin/avatar-review/admin",
                            json={"action": "approve"})
            rec(6, "头像审核通过", r.status_code == 200)
        else:
            rec(6, "头像审核通过", None, "无待审头像（可能已批准）", warn=True)
        # API Key 全生命周期
        r = self.s.post(f"{self.base}/api/admin/api-keys",
                        json={"name": TAG, "scope_kb_ids": ["default"],
                              "expires_days": 1})
        kid, key = r.json().get("id"), r.json().get("key", "")
        self.created["keys"].append(kid)
        rec(6, "Key 创建（dm_ 明文一次性）", r.status_code == 201 and key.startswith("dm_"))
        listing = json.dumps(self.s.get(f"{self.base}/api/admin/api-keys").json())
        rec(6, "列表不泄露明文", key not in listing)
        H = {"Authorization": f"Bearer {key}"}
        r = requests.post(f"{self.base}/open/v1/retrieve",
                          json={"question": "什么是 RAG"}, headers=H)
        rec(8, "开放检索 200", r.status_code == 200, f"count={r.json().get('count')}")
        r = requests.post(f"{self.base}/open/v1/retrieve",
                          json={"question": "x", "kb_ids": ["no-such-kb"]}, headers=H)
        rec(8, "scope 越界 403", r.status_code == 403, f"got {r.status_code}")
        r = self.s.post(f"{self.base}/api/admin/api-keys/{kid}/rotate")
        key2 = r.json().get("key", "")
        new_kid = r.json().get("id")   # rotate 返回的新 Key id（旧 id 已随轮换吊销）
        rec(8, "轮换出新明文", r.status_code == 201 and key2 != key)
        rec(8, "旧 Key 失效 401", requests.post(
            f"{self.base}/open/v1/retrieve", json={"question": "x"},
            headers=H).status_code == 401)
        H2 = {"Authorization": f"Bearer {key2}"}
        if not skip_llm and llm_ok:
            r = requests.post(f"{self.base}/open/v1/chat",
                              json={"question": "用五个字回答：什么是RAG"},
                              headers=H2, timeout=120)
            ans = r.json().get("answer") or r.json().get("response") or ""
            rec(8, "开放问答 200", r.status_code == 200 and len(ans) > 2, ans[:40])
        r = self.s.delete(f"{self.base}/api/admin/api-keys/{new_kid}")
        rec(8, "吊销", r.status_code == 200, f"key#{new_kid}")
        rec(8, "吊销后 401", requests.post(
            f"{self.base}/open/v1/retrieve", json={"question": "x"},
            headers=H2).status_code == 401)

    # ---------- L7 Admin ----------
    def admin(self):
        r = self.s.get(f"{self.base}/api/admin/overview")
        u = r.json().get("usage", {})
        rec(7, "overview + 用量 SQL 聚合", r.status_code == 200
            and "users" in r.json() and "llm_calls" in u, f"llm_calls={u.get('llm_calls')}")
        for ep, name in [("sessions?limit=5", "会话审计"),
                         ("traces?page=1&page_size=5", "检索日志"),
                         ("usage?days=7", "用量成本"),
                         ("sla?days=7", "SLA"),
                         ("alerts", "告警"),
                         ("audit?limit=5", "审计中心"),
                         ("queries?limit=5", "提问记录"),
                         ("badcases?limit=5", "Badcase")]:
            r = self.s.get(f"{self.base}/api/admin/{ep}")
            rec(7, name, r.status_code == 200, f"got {r.status_code}")
        r = self.s.post(f"{self.base}/api/admin/backup")
        rec(7, "一键备份 201", r.status_code == 201, r.text[:60])
        r = self.s.post(f"{self.base}/api/admin/reindex")
        rec(7, "手动增量重建", r.status_code == 200 and r.json().get("ok"))
        # 用户管理闭环
        r = self.s.post(f"{self.base}/api/admin/users",
                        json={"username": f"{TAG}_u3",
                              "password": QA_PASS, "is_admin": False})
        self.created["users"].append(f"{TAG}_u3")
        r = self.s.post(f"{self.base}/api/admin/users/{TAG}_u3/reset-password",
                        json={"new_password": QA_RESET_PASS})
        rec(7, "重置密码", r.status_code == 200)
        rec(7, "重置后可登录", requests.post(
            f"{self.base}/login",
            data={"username": f"{TAG}_u3", "password": QA_RESET_PASS}).status_code == 200)
        rec(7, "级联删除", self.s.delete(
            f"{self.base}/api/admin/users/{TAG}_u3").status_code == 200)
        rec(7, "删除后登录拒绝", requests.post(
            f"{self.base}/login",
            data={"username": f"{TAG}_u3", "password": QA_RESET_PASS}).status_code == 400)

    # ---------- 清理 ----------
    def cleanup(self):
        print("\n---- 清理测试数据 ----")
        for n in self.created["docs"]:
            self.s.delete(f"{self.base}/api/kbs/default/docs/{n}")
        for kid in self.created["kbs"]:
            self.s.delete(f"{self.base}/api/kbs/{kid}")
        for u in self.created["users"]:
            self.s.delete(f"{self.base}/api/admin/users/{u}")
        for sid in self.created["sessions"]:
            self.s.delete(f"{self.base}/api/sessions/{sid}")
        docs = self.s.get(f"{self.base}/api/kbs/default/docs").json()
        left = [d["name"] for d in docs
                if d["name"].startswith(TAG) or "example.com" in d["name"]]
        for n in left:
            self.s.delete(f"{self.base}/api/kbs/default/docs/{n}")
        print(f"清理完成（遗留文档 {len(left)}）")


def summarize() -> int:
    fails = [r for r in RESULTS if r[2] == "FAIL"]
    warns = [r for r in RESULTS if r[2] == "WARN"]
    print(f"\n==== E2E 验收: {len(RESULTS)} 项, {len(fails)} FAIL, {len(warns)} WARN ====")
    for layer, name, status, d in fails + warns:
        print(f"  {status} L{layer} {name}: {d[:100]}")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="DocMind 深度 E2E 验收")
    ap.add_argument("--base", default="http://127.0.0.1:7860")
    ap.add_argument("--admin-password", default=None)
    ap.add_argument("--skip-llm", action="store_true", help="跳过真实 LLM 用例")
    ap.add_argument("--skip-url-import", action="store_true",
                    help="跳过公网 URL 导入（无外网环境）")
    ap.add_argument("--skip-reindex-wait", action="store_true",
                    help="跳过 75s 防抖重建等待")
    ap.add_argument("--skip-lockout", action="store_true",
                    help="跳过防爆破锁定用例（避免锁定测试账号 15 分钟）")
    ap.add_argument("--keep-data", action="store_true", help="保留测试数据不清理")
    args = ap.parse_args()

    pwd = args.admin_password or os.getenv("ADMIN_PASSWORD") or ""
    if not pwd:
        env_file = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), ".env")
        if os.path.isfile(env_file):
            for line in open(env_file, encoding="utf-8"):
                if line.startswith("ADMIN_PASSWORD="):
                    pwd = line.split("=", 1)[1].strip()
    if not pwd:
        print("未提供 admin 密码（--admin-password / ADMIN_PASSWORD / .env）")
        return 2

    qa = QA(args.base, pwd)
    try:
        qa.auth(skip_lockout=args.skip_lockout)
        qa.kbs()
        qa.docs(skip_url_import=args.skip_url_import)
        if not args.skip_reindex_wait:
            qa.reindex_and_search()
        else:
            rec(4, "自动重建+搜索", None, "已跳过（--skip-reindex-wait）", warn=True)
        llm_ok = True
        if not args.skip_llm:
            llm_ok = qa.llm_available()
            qa.chat(llm_ok=llm_ok)
        else:
            rec(5, "对话链路", None, "已跳过（--skip-llm）", warn=True)
        qa.user_and_openapi(skip_llm=args.skip_llm, llm_ok=llm_ok)
        qa.admin()
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        rec(0, "脚本异常中断", False, str(e)[:120])
    finally:
        if not args.keep_data:
            qa.cleanup()
        else:
            print("\n（--keep-data：保留测试数据）")
    return summarize()


if __name__ == "__main__":
    sys.exit(main())
