"""_fetch_public SSRF 加固单测（红队⑦项）：
pin-IP 直连 / HTTPS 双解析一致性 / 重定向拦截 / 内网拒绝。

通过劫持 socket.getaddrinfo 与 requests.get 验证「校验与连接同源」语义，
不发真实网络请求。"""
import socket

import pytest
import requests as _requests

from docmind.docs_api import _fetch_public


class _FakeResp:
    def __init__(self, status=200):
        self.status_code = status


@pytest.fixture
def net(monkeypatch):
    """劫持 DNS 与 HTTP：dns 序列逐次出队模拟 rebinding 轮换；
    calls 记录 requests.get 实际收到的参数"""
    state = {"dns": [], "get": None, "resp": _FakeResp()}

    def fake_getaddrinfo(host, *a, **k):
        ip = state["dns"].pop(0) if state["dns"] else "93.184.216.34"
        return [(2, 1, 6, "", (ip, 0))]

    def fake_get(url, timeout=None, headers=None, allow_redirects=True, **kw):
        state["get"] = {"url": url, "headers": dict(headers or {}),
                        "allow_redirects": allow_redirects}
        if isinstance(state["resp"], Exception):
            raise state["resp"]
        return state["resp"]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(_requests, "get", fake_get)
    return state


def test_literal_private_ip_rejected():
    """字面量内网 IP 直接 400（数值地址本地解析，无需 fixture）"""
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _fetch_public("http://10.0.0.5/x")


def test_http_pinning_and_host_header(net):
    net["dns"] = ["93.184.216.34", "93.184.216.34"]
    resp = _fetch_public("http://example.com/page?a=1")
    assert resp.status_code == 200
    g = net["get"]
    assert g["url"] == "http://93.184.216.34/page?a=1"   # 连的是校验过的 IP
    assert g["headers"]["Host"] == "example.com"         # Host 保留原域名
    assert g["allow_redirects"] is False                 # 禁跟随重定向


def test_https_consistent_dns_allowed(net):
    net["dns"] = ["93.184.216.34", "93.184.216.34"]
    _fetch_public("https://example.com/doc")
    assert net["get"]["url"] == "https://example.com/doc"  # 域名建连保 TLS/SNI


def test_https_rebinding_rotation_rejected(net):
    """HTTPS 双解析不一致（攻击者 TTL=1 轮换 A 记录）→ 拒绝"""
    net["dns"] = ["93.184.216.34", "10.0.0.5"]
    with pytest.raises(ValueError, match="rebinding"):
        _fetch_public("https://evil.example.com/doc")


def test_redirect_response_rejected(net):
    """跟随重定向会重新解析新主机绕过全部校验 → 3xx 直接报错"""
    net["resp"] = _FakeResp(302)
    with pytest.raises(ValueError, match="重定向"):
        _fetch_public("http://example.com/redirect-me")


def test_localhost_hostname_rejected():
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _fetch_public("http://localhost:7861/api")


def test_private_resolved_ip_rejected(net):
    """域名解析出私网地址（DNS rebinding 经典载荷）→ 400"""
    net["dns"] = ["169.254.169.254"]
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _fetch_public("http://metadata.attacker.example/")