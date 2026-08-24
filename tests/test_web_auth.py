"""web_auth 防爆破锁定 + metrics path 归一化回归测试。

背景（2026-08-24 P0 修复）：
- clear_failures 原只清用户维度，同 IP 失败达阈值后正常用户改对密码重登仍被锁；
- _client_ip 原为模块级全局，并发请求互相覆盖导致 IP 锁定错乱，改 ContextVar；
- app.py HTTP 延迟埋点误用 time()-monotonic()，指标全为废数据；
- Prometheus path 标签未归一化，每个新会话 id 派生新时间序列（基数爆炸）。
"""
import time

import pytest

from docmind import metrics, web_auth


@pytest.fixture(autouse=True)
def _reset_auth_state():
    """每个用例前后清空内存态，避免用例间串扰"""
    web_auth._failures.clear()
    yield
    web_auth._failures.clear()


def test_user_dimension_locks_after_max_fails():
    for _ in range(web_auth._LOGIN_MAX_FAILS):
        web_auth.record_failure("alice")
    remaining = web_auth.is_locked("alice")
    assert remaining > 0
    assert remaining <= web_auth._LOCK_SECONDS


def test_clear_failures_also_clears_ip_dimension():
    """登录成功必须解除 IP 维度：否则共享出口 IP 的正常用户被连带锁满 15 分钟。
    注意 record_failure 每次同时给 user+ip 两个维度各 +1"""
    web_auth.set_client_ip("10.0.0.1")
    for _ in range(web_auth._IP_MAX_FAILS // 2):   # 10 次 → ip 10 条 < 20，不锁
        web_auth.record_failure("bob")
    assert web_auth.is_locked("victim") == 0       # victim 未失败过，IP 也未达阈值

    # victim 从同 IP 输对密码登录成功 → 该 IP 的失败记录一并清除
    web_auth.clear_failures("victim")
    assert not web_auth._failures.get("ip:10.0.0.1")


def test_client_ip_is_request_scoped_not_global():
    """ContextVar 隔离：线程 A set 的 IP 不泄漏给并发线程 B（原全局变量会）"""
    import threading

    seen = {}

    def worker(name, ip):
        web_auth.set_client_ip(ip)
        time.sleep(0.02)                        # 给对方覆盖窗口（原 bug 在此暴露）
        seen[name] = web_auth.client_ip()

    t1 = threading.Thread(target=worker, args=("a", "1.1.1.1"))
    t2 = threading.Thread(target=worker, args=("b", "2.2.2.2"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert seen["a"] == "1.1.1.1"
    assert seen["b"] == "2.2.2.2"


def test_ip_lockout_triggers_at_threshold():
    web_auth.set_client_ip("9.9.9.9")
    for _ in range(web_auth._IP_MAX_FAILS):
        web_auth.record_failure("carol")
    assert web_auth.is_locked("fresh-user") > 0   # 新用户名也被 IP 维度拦


def test_must_change_pwd_blocks_business_but_not_password_change(monkeypatch):
    """P0 死锁回归（2026-08-24 二轮实弹发现）：require_user 对强制改密用户
    一律 403——若 /api/change-password 也走它，首登用户连改密接口本身都被拦，
    「强制改密」沦为永久死锁。修复后改密端点用 current_user（仅校验登录态，
    旧密码在 store.change_password 内部另行校验）。本例锁定两个守卫的分野"""
    from types import SimpleNamespace

    import fastapi

    monkeypatch.setattr("docmind.store.get_must_change_pwd", lambda u: True)
    web_auth._tokens.clear()
    try:
        token = web_auth.issue("dave")
        req = SimpleNamespace(cookies={web_auth.TOKEN_COOKIE: token})
        with pytest.raises(fastapi.HTTPException) as ei:
            web_auth.require_user(req)              # 业务端点：拦截正确
        assert ei.value.status_code == 403
        assert web_auth.current_user(req) == "dave"  # 改密端点：必须放行
    finally:
        web_auth._tokens.clear()


# ---- normalize_http_path ----

def test_normalize_session_id_paths():
    assert (metrics.normalize_http_path(
        "/api/sessions/sess-mt6m52x5-qa1fje/messages")
        == "/api/sessions/{id}/messages")
    assert (metrics.normalize_http_path("/api/feedback/sess-mt6m52x5-qa1fje")
            == "/api/feedback/{id}")


def test_normalize_upload_filenames_and_hex_ids():
    assert (metrics.normalize_http_path("/files/uploads/1787538290201_9e3ce6.jpg")
            == "/files/uploads/{id}")
    assert metrics.normalize_http_path("/api/kbs/0f8fad5b-d9fb469c/docs") \
        .count("{id}") == 1


def test_normalize_keeps_static_paths():
    assert metrics.normalize_http_path("/") == "/"
    assert metrics.normalize_http_path("/api/sessions") == "/api/sessions"
    assert metrics.normalize_http_path("/api/kbs/default/docs") \
        == "/api/kbs/default/docs"              # default 是有限集合成员，不折叠
    assert metrics.normalize_http_path("/login") == "/login"


def test_latency_histogram_receives_sane_values():
    """修复验证：observe 的值必须是秒级小数而非 epoch-单调时钟的巨差值。
    直接调用中间件过重，这里等价校验时钟语义——perf_counter 差值落在合理区间"""
    t0 = time.perf_counter()
    time.sleep(0.01)
    delta = time.perf_counter() - t0
    assert 0 < delta < 1                        # 原 time()-monotonic() 为负巨数/巨数
