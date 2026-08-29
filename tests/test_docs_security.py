"""docs_api 安全回归：URL 导入 SSRF 防护（2026-08-24 P1）+ 落盘边界防穿越。"""
import socket

import pytest
from fastapi import HTTPException

from docmind.docs_api import _assert_public_host, _kb_docs_dir, _safe_doc_path


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:7860/metrics",          # 本机管理接口
    "http://10.0.0.9/internal",
    "http://192.168.1.1/router",
    "http://172.16.5.4/",
    "http://169.254.169.254/latest/meta-data",  # 云元数据端点
    "http://localhost:9000/",
    "http://nas.local/share",
])
def test_private_targets_rejected(url):
    with pytest.raises(HTTPException) as ei:
        _assert_public_host(url)
    assert ei.value.status_code == 400


def test_dns_resolving_to_loopback_rejected(monkeypatch):
    """公网域名解析出环回地址（rebinding 常见形态）同样拒绝"""
    def fake(host, port, *a, **kw):     # noqa: ARG001
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with pytest.raises(HTTPException):
        _assert_public_host("http://evil.example.com/")


def test_public_host_passes(monkeypatch):
    """正常公网域名放行（离线环境 mock 解析，不打真实 DNS）"""
    def fake(host, port, *a, **kw):     # noqa: ARG001
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    _assert_public_host("http://example.com/doc")


def test_unresolvable_host_rejected(monkeypatch):
    def boom(host, port, *a, **kw):     # noqa: ARG001
        raise socket.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(HTTPException) as ei:
        _assert_public_host("http://no-such-host.invalid/")
    assert ei.value.status_code == 400


# ---------------- 落盘边界：_safe_doc_path / _kb_docs_dir ----------------

@pytest.mark.parametrize("bad", [
    "../evil.md",
    "sub/../../evil.md",
    "..\\evil.md",        # Windows 风格分隔符在 POSIX 下也拒绝
    "..",
    ".",
    "",
])
def test_safe_doc_path_rejects_traversal(tmp_path, bad):
    with pytest.raises(HTTPException):
        _safe_doc_path(str(tmp_path), bad)


def test_safe_doc_path_stays_inside_base(tmp_path):
    p = str(_safe_doc_path(str(tmp_path), "正常文档.md"))
    assert p.startswith(str(tmp_path.resolve()))
    assert p.endswith("正常文档.md")
    # 已有同名文件时 realpath 前缀校验同样通过（覆盖写场景）
    (tmp_path / "exists.md").write_text("x", encoding="utf-8")
    assert str(_safe_doc_path(str(tmp_path), "exists.md")).endswith("exists.md")


def test_kb_docs_dir_rejects_traversal():
    for bad in ("../x", "a/b", "..\\x", "..", ".", ""):
        with pytest.raises(HTTPException):
            _kb_docs_dir(bad)


def test_kb_docs_dir_accepts_uuid_like():
    d = _kb_docs_dir("c9bf9e57-1685-4c89-bafb-ff5af830be8a")
    assert "kb_docs" in d and d.endswith("c9bf9e57-1685-4c89-bafb-ff5af830be8a")