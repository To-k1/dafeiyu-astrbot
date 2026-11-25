"""Settings and explicit connection probes for the console's managed providers."""

import asyncio
import base64
import io
import json
import os
import re
import subprocess
import tempfile
import wave
from uuid import uuid4

from aiohttp import ClientError, ClientSession, ClientTimeout

from deploy.dsh_bridge.go_profile import BASE_URL, MODEL as DSH_GO_MODEL
from deploy.dsh_bridge.go_relay import subscription_receipt
from deploy.dsh_bridge.media_asr import MODEL as ASR_MODEL, transcribe_audio
from plugin.astrbot_plugin_face_convert.speech_asr import asr_endpoint
from deploy.fish_cost import MODEL as DEEPSEEK_MODEL
from plugin.astrbot_plugin_bili_activity.model_route import GO_PROVIDER, is_go_model


FACE_PATH = "data/plugins/astrbot_plugin_face_convert/config.json"
KEY_MODES = {"auto", "shared", "separate"}


def provider_key(provider):
    key = provider.get("api_key", "")
    if isinstance(key, list):
        key = next((item for item in key if isinstance(item, str) and item.strip()), "")
    return key.strip() if isinstance(key, str) else ""


def tts_config(cfg):
    providers = [p for p in cfg.get("provider", []) if p.get("type") == "dashscope_tts"]
    selected_id = (cfg.get("provider_tts_settings") or {}).get("provider_id")
    for provider in providers:
        if provider.get("enable") and provider.get("id") == selected_id:
            return provider
    return next((p for p in providers if p.get("enable")), providers[0] if providers else None)


def asr_key(face, cfg, *, environment=""):
    mode = face.get("asr_key_mode", "auto")
    if mode == "shared":
        return provider_key(tts_config(cfg) or {})
    key = provider_key({"api_key": face.get("asr_api_key")})
    if key or mode == "separate":
        return key
    return environment or next((provider_key(p) for p in cfg.get("provider", [])
                                if (p.get("type") in {"dashscope", "dashscope_tts"} or p.get("provider") == "dashscope")
                                and provider_key(p)), "")


def status(cfg, face, credentials, free_asr):
    tts = tts_config(cfg) or {}
    go = next((p for p in cfg.get("provider", []) if p.get("id") == GO_PROVIDER), {})
    return {
        "tts_provider": tts.get("id"), "tts_model": tts.get("model", ""),
        "tts_voice": tts.get("dashscope_tts_voice", ""), "tts_key_saved": bool(provider_key(tts)),
        "asr_model": face.get("asr_model") or ASR_MODEL, "asr_mode": face.get("asr_key_mode", "auto"),
        "asr_key_saved": bool(provider_key({"api_key": face.get("asr_api_key")})),
        "asr_ready": bool(asr_key(face, cfg, environment=os.environ.get("DASHSCOPE_API_KEY", "").strip())),
        "dsh_asr_model": face.get("dsh_asr_model") or ASR_MODEL, "dsh_asr_enabled": free_asr,
        "dsh_asr_ready": bool(asr_key(face, cfg)),
        "asr_workspace": face.get("asr_workspace", ""), "asr_language": face.get("asr_language", ""),
        "dsh_asr_language": face.get("dsh_asr_language", ""), "asr_keep_dialect": face.get("asr_keep_dialect") is True,
        "deepseek_model": DEEPSEEK_MODEL, "deepseek_key_saved": bool(credentials.get("upstream_key")),
        "go_model": go.get("model") or DSH_GO_MODEL, "go_provider_ready": bool(go.get("enable")),
        "go_key_saved": bool(credentials.get("opencode_key")), "dsh_go_model": DSH_GO_MODEL,
    }


def validate_speech(body, cfg, face):
    fields = {"tts_model", "tts_voice", "tts_key", "asr_model", "dsh_asr_model", "asr_mode", "asr_key",
              "asr_workspace", "asr_language", "dsh_asr_language", "asr_keep_dialect"}
    if not body or body.keys() - fields:
        raise ValueError("语音设置包含无效字段")
    tts = tts_config(cfg)
    if "asr_keep_dialect" in body:
        if type(body["asr_keep_dialect"]) is not bool:
            raise ValueError("请选择是否保留方言")
        face["asr_keep_dialect"] = body["asr_keep_dialect"]
    for name in body.keys() - {"asr_keep_dialect"}:
        value = body[name]
        if not isinstance(value, str) or len(value) > 250 or any(ord(c) < 32 for c in value):
            raise ValueError("模型、音色或 Key 格式无效")
        body[name] = value.strip()
    for name in ("tts_model", "asr_model", "dsh_asr_model"):
        if name in body and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", body[name]):
            raise ValueError("请填写有效的模型 ID")
    for name in ("tts_key", "asr_key"):
        if body.get(name) and not re.fullmatch(r"[A-Za-z0-9_-]{12,250}", body[name]):
            raise ValueError("请填写有效的百炼 Key")
    tts_change = any(name.startswith("tts_") and (name != "tts_key" or body[name]) for name in body)
    if tts_change and tts is None:
        raise ValueError("请先在 AstrBot 添加百炼 TTS 提供商")
    for name, field in (("tts_model", "model"), ("tts_voice", "dashscope_tts_voice"), ("tts_key", "api_key")):
        if name in body and (name == "tts_voice" or body[name]):
            tts[field] = body[name]
    for name in ("asr_model", "dsh_asr_model"):
        if name in body:
            face[name] = body[name]
    for name in ("asr_workspace", "asr_language", "dsh_asr_language"):
        if name in body:
            pattern = r"[A-Za-z0-9-]{1,80}" if name == "asr_workspace" else r"[a-z]{2,8}"
            if body[name] and not re.fullmatch(pattern, body[name]):
                raise ValueError("请检查百炼业务空间或语种代码")
            face[name] = body[name]
    if body.keys() & {"asr_model", "dsh_asr_model", "asr_workspace"}:
        for name in ("asr_model", "dsh_asr_model"):
            asr_endpoint(face.get(name) or ASR_MODEL, face.get("asr_workspace", ""))
    if body.get("asr_key"):
        face["asr_api_key"] = body["asr_key"]
    if "asr_mode" in body:
        mode = body["asr_mode"]
        if mode not in KEY_MODES:
            raise ValueError("请选择听写 Key 的使用方式")
        face["asr_key_mode"] = mode
        if mode != "auto" and not asr_key(face, cfg):
            raise ValueError("请先保存所选方式需要的百炼 Key")


async def go_models(key):
    if not key:
        raise ValueError("请先保存 OpenCode Go Key")
    try:
        async with ClientSession(timeout=ClientTimeout(total=15), trust_env=False) as client:
            async with client.get(BASE_URL + "/models", headers={"Authorization": "Bearer " + key},
                                  allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError(f"OpenCode Go 模型目录返回 HTTP {response.status}")
                result = await response.json()
                return sorted({row["id"] for row in result.get("data", [])
                               if isinstance(row, dict) and is_go_model(row.get("id"))})
    except (ClientError, TimeoutError, AttributeError, TypeError):
        raise ValueError("暂时无法读取 OpenCode Go 模型目录") from None


async def chat_probe(provider, key, model):
    if not key:
        raise ValueError("请先保存对应的 API Key")
    body = {"model": model, "messages": [{"role": "user", "content": "Reply with OK."}],
            "max_tokens": 32, "thinking": {"type": "disabled"}}
    headers = {"Authorization": "Bearer " + key}
    if provider == "go":
        base = BASE_URL
        headers["x-opencode-session"] = uuid4().hex
        headers["User-Agent"] = "FishHome-DSH/0.2"
        body.update(stream=True, stream_options={"include_usage": True})
    else:
        base = "http://127.0.0.1:6197/openai/interaction/v1"
    try:
        async with ClientSession(timeout=ClientTimeout(total=30), trust_env=False) as client:
            async with client.post(base + "/chat/completions", headers=headers, json=body,
                                   allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError(f"模型请求返回 HTTP {response.status}")
                if provider == "go":
                    events = [json.loads(line[6:]) for line in (await response.text()).splitlines()
                              if line.startswith("data: ") and line[6:] != "[DONE]"]
                    usage = next((event["usage"] for event in reversed(events) if event.get("usage")), {})
                    subscription_receipt(usage)
                    texts = [choice.get("delta", {}).get("content") for event in events
                             for choice in event.get("choices", [])]
                else:
                    data = await response.json()
                    texts = [choice.get("message", {}).get("content") for choice in data.get("choices", [])]
    except (ClientError, OSError, TimeoutError, json.JSONDecodeError, AttributeError):
        raise ValueError("模型请求未收到可读取的响应") from None
    if not any(isinstance(text, str) and text.strip() for text in texts):
        raise ValueError("模型响应没有返回文本")
    return f"{model} 已返回测试文本" + ("，已确认 Go 订阅回执。" if provider == "go" else "。")


async def asr_probe(key, model, **options):
    if not key:
        raise ValueError("请先保存听写所用的百炼 Key")
    data = io.BytesIO()
    with wave.open(data, "wb") as sample:
        sample.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        sample.writeframes(bytes(32000))
    result = await transcribe_audio({"mime": "audio/wav", "data": base64.b64encode(data.getvalue()).decode(),
                                     "start": 0, "end": 1}, key, model=model, **options)
    if result["status"] == "free_quota_exhausted":
        raise ValueError("百炼免费听写额度已用完")
    if result["status"] == "empty":
        return f"{model} 已处理 1 秒静音样本，未检测到语音，连接正常。"
    if result["status"] != "transcribed":
        details = []
        if type(result.get("http_status")) is int:
            details.append(f"HTTP {result['http_status']}")
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", str(result.get("code") or "")):
            details.append(result["code"])
        suffix = "（" + "，".join(details) + "）" if details else ""
        raise ValueError("未收到成功的百炼听写回执" + suffix)
    return f"{model} 已接受 1 秒静音样本，模型连接正常。"


TTS_SCRIPT = """
import asyncio, json, sys
from pathlib import Path
from astrbot.core.provider.sources.dashscope_tts import ProviderDashscopeTTSAPI

config = json.load(sys.stdin)
provider = ProviderDashscopeTTSAPI(config, {})
audio = Path(asyncio.run(provider.get_audio('语音连接测试。')))
assert audio.stat().st_size > 0
"""


def tts_probe(python, provider):
    key = provider_key(provider)
    if not key or not provider.get("model"):
        raise ValueError("请先保存百炼 TTS 的 Key 和模型")
    try:
        with tempfile.TemporaryDirectory(prefix="fishhome-voice-test-") as folder:
            result = subprocess.run([str(python), "-B", "-c", TTS_SCRIPT],
                                    input=json.dumps({**provider, "api_key": key}).encode(),
                                    cwd=folder, env={**os.environ, "ASTRBOT_ROOT": folder},
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=35, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise ValueError("百炼 TTS 测试超时") from None
    if result.returncode:
        raise ValueError("百炼 TTS 未生成音频，请核对 Key、模型和音色")
    return f"{provider['model']} 已合成测试音频。"


async def probe(manager, provider, cfg, face):
    credentials = manager.creds.values
    state = status(cfg, face, credentials, manager.options.get("dsh_asr_free_only_confirmed") is True)
    if provider == "tts":
        return await asyncio.to_thread(tts_probe, manager.python, tts_config(cfg) or {})
    if provider in {"asr", "dsh_asr"}:
        dsh = provider == "dsh_asr"
        if dsh and not state["dsh_asr_enabled"]:
            raise ValueError("请先确认百炼用完即停并启用 DSH 听写")
        key = asr_key(face, cfg, environment="" if dsh else os.environ.get("DASHSCOPE_API_KEY", "").strip())
        options = {}
        if workspace := state["asr_workspace"]:
            options["workspace"] = workspace
        if language := state["dsh_asr_language" if dsh else "asr_language"]:
            options["language"] = language
        return await asr_probe(key, state[provider + "_model"], **options)
    if provider == "go":
        return await chat_probe("go", credentials.get("opencode_key"), state["go_model"])
    if provider == "deepseek" and credentials.get("upstream_key"):
        return await chat_probe("deepseek", credentials.get("interaction"), DEEPSEEK_MODEL)
    raise ValueError("请先保存对应的 API Key")
