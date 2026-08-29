"""语音能力：ASR 语音输入（Paraformer）+ TTS 播报（Qwen-TTS REST）。

- ASR：前端 MediaRecorder 录音 → Web Audio 转 16k 单声道 WAV → 本端点识别
- TTS：文本轻清洗 → qwen3-tts-flash REST 合成（HTTPS，不依赖 websocket，
  适配 websocket 被限制的网络环境）→ 下载 OSS wav → 按 (voice+text) 磁盘缓存
- 音色为 Qwen-TTS 官方系统音色（风格化预设；合规：不内置明星音色，架构留授权插槽）
"""
import hashlib
import os
import pathlib
import re

import anyio
import certifi
import requests

# macOS venv 常缺系统根证书：SSL 指向 certifi 证书包
#（dashscope ASR 的 websocket/aiohttp 也读该环境变量）
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

# 兜底：进程内 OpenSSL 默认证书库可能已被先前的 TLS 调用缓存为空，
# 环境变量不再生效 → 给 ssl.create_default_context 注入 certifi cafile
import ssl as _ssl

if not getattr(_ssl.create_default_context, "_dm_patched", False):
    _orig_cdc = _ssl.create_default_context

    def _dm_cdc(*args, **kwargs):
        kwargs.setdefault("cafile", certifi.where())
        return _orig_cdc(*args, **kwargs)

    _dm_cdc._dm_patched = True
    _ssl.create_default_context = _dm_cdc

import fastapi
from fastapi import HTTPException, UploadFile, File
from fastapi.responses import JSONResponse, Response
from urllib.parse import urlparse

from docmind.deps import RequireUser
from docmind import config
from docmind.api_utils import server_error
from docmind.docs_api import _fetch_public

TTS_CACHE_DIR = os.path.join(config.PROJECT_ROOT, "data", "tts_cache")
TTS_URL = ("https://dashscope.aliyuncs.com/api/v1/services/aigc/"
           "multimodal-generation/generation")

# Qwen-TTS 官方系统音色（风格化预设）
VOICES = [
    {"id": "Cherry", "label": "Cherry · 活泼亲切女声（默认）"},
    {"id": "Serena", "label": "Serena · 温柔知性女声"},
    {"id": "Ethan", "label": "Ethan · 温暖叙事男声（说书味）"},
    {"id": "Chelsie", "label": "Chelsie · 甜美可爱女声"},
]

_MD_CLEAN = [
    (re.compile(r"```[\s\S]*?```"), " 代码块略。"),
    (re.compile(r"\[来源:[^\]\n]*\]"), ""),
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),
    (re.compile(r"[#*`>|]"), ""),
    (re.compile(r"!\[[^\]]*\]"), ""),
    (re.compile(r"\n+"), "。"),
]


def _clean_for_tts(text: str) -> str:
    for pat, rep in _MD_CLEAN:
        text = pat.sub(rep, text)
    return text.strip()[:500]   # 播报截断，控制成本与时长（qwen3-tts 上限 600 字符）


def _synthesize(text: str, voice: str) -> bytes:
    """Qwen-TTS 非流式 REST：返回完整音频 wav bytes"""
    r = requests.post(
        TTS_URL,
        headers={"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}",
                 "Content-Type": "application/json"},
        json={"model": "qwen3-tts-flash",
              "input": {"text": text, "voice": voice,
                        "language_type": "Chinese"}},
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"TTS HTTP {r.status_code}: {r.text[:200]}")
    url = (r.json().get("output") or {}).get("audio", {}).get("url")
    if not url:
        raise RuntimeError(f"TTS 无音频返回: {r.text[:200]}")
    # 音频 URL 来自上游报文（不可信输入）：先限制结果域名白名单，再走
    # _fetch_public 的 SSRF 加固抓取（公网 IP 校验+防 rebinding+禁重定向）
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host.endswith(".aliyuncs.com"):
        raise RuntimeError("TTS 返回的音频地址来源非预期，已拒绝下载")
    try:
        resp = _fetch_public(url)
    except (ValueError, HTTPException) as e:
        raise RuntimeError(f"音频下载被安全策略拒绝: {e}") from e
    return resp.content


def register_voice_routes(app) -> None:

    @app.get("/api/voice/voices", include_in_schema=False)
    async def _voices(request: fastapi.Request, _user: RequireUser):
        return JSONResponse(VOICES)

    @app.post("/api/voice/asr", include_in_schema=False)
    async def _asr(request: fastapi.Request, _user: RequireUser, file: UploadFile = File(...)):
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="空音频")
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="音频过大（上限 10MB）")
        import tempfile
        from http import HTTPStatus
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tf.write(data)
            tmp = tf.name
        try:
            import dashscope
            from dashscope.audio.asr import Recognition
            dashscope.api_key = config.DASHSCOPE_API_KEY
            rec = Recognition(model="paraformer-realtime-v2", format="wav",
                              sample_rate=16000, language_hints=["zh", "en"],
                              callback=None)
            result = rec.call(tmp)
            if result.status_code != HTTPStatus.OK:
                raise HTTPException(status_code=502,
                                    detail=f"识别失败: {getattr(result, 'message', '')}")
            sentences = result.get_sentence() or []
            text = "".join(s.get("text", "") for s in sentences).strip()
            return JSONResponse({"text": text})
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            # 上游 ASR SDK 报文不外泄（可能含 endpoint/凭证线索）
            raise server_error("语音识别", e, __name__)
        finally:
            os.unlink(tmp)

    @app.post("/api/voice/tts", include_in_schema=False)
    async def _tts(request: fastapi.Request, _user: RequireUser):
        body = await request.json()
        raw = str(body.get("text") or "")
        voice = str(body.get("voice") or "Cherry")
        text = _clean_for_tts(raw)
        if not text:
            raise HTTPException(status_code=400, detail="无可播报内容")
        os.makedirs(TTS_CACHE_DIR, exist_ok=True)
        key = hashlib.sha256(f"{voice}|{text}".encode("utf-8")).hexdigest()
        cached = os.path.join(TTS_CACHE_DIR, f"{key}.wav")
        if os.path.isfile(cached):
            return Response(pathlib.Path(cached).read_bytes(),
                            media_type="audio/wav")
        try:
            # 合成 + 音频下载是同步网络 IO（timeout 最长 60s），必须下放
            # 线程池——事件循环冻结期间全站无响应（QA 审查发现，与 OCR 同类）
            audio = await anyio.to_thread.run_sync(_synthesize, text, voice)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            # 上游 TTS SDK 报文不外泄（可能含 endpoint/凭证线索）
            raise server_error("语音合成", e, __name__)
        pathlib.Path(cached).write_bytes(audio)
        return Response(audio, media_type="audio/wav")
