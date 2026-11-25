"""DashScope ASR adapters. Both routes return original text and provider receipts."""

import re
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout


MODEL = "qwen3-asr-flash"
NATIVE_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
URL = "https://dashscope.aliyuncs.com" + NATIVE_PATH


def audio_family(model):
    return bool(re.fullmatch(r"qwen-audio-3\.[01]-asr-flash(?:-\d{4}-\d{2}-\d{2})?", model))


def asr_endpoint(model, workspace="", endpoint=None):
    if endpoint is not None:
        return endpoint
    if audio_family(model):
        if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", workspace):
            raise ValueError("Qwen-Audio ASR 需要填写北京百炼业务空间 ID")
        return f"https://{workspace}.cn-beijing.maas.aliyuncs.com" + NATIVE_PATH
    return URL


def asr_payload(model, data_uri, language="", keep_dialect=False):
    if language and not re.fullmatch(r"[a-z]{2,8}", language):
        raise ValueError("请填写语种代码，例如 zh、en、ja，或留空自动检测")
    if audio_family(model):
        mime = urlsplit(data_uri).path.split(";", 1)[0]
        formats = {"audio/wav": "wav", "audio/mpeg": "mp3", "audio/opus": "opus", "audio/flac": "flac"}
        if mime not in formats:
            raise ValueError("Qwen-Audio ASR 音频格式需要 WAV、MP3、Opus 或 FLAC")
        content = {"type": "input_audio", "input_audio": {"data": data_uri}}
        params = {"format": formats[mime]}
        if language:
            params["language_hints"] = [language]
        if keep_dialect and model.startswith("qwen-audio-3.1-"):
            params["keep_dialect"] = True
    else:
        if not re.fullmatch(r"qwen3-asr-flash(?:-\d{4}-\d{2}-\d{2})?", model):
            raise ValueError("当前支持 Qwen3 ASR Flash 或 Qwen-Audio 3.0/3.1 ASR Flash 同步接口")
        content = {"audio": data_uri}
        params = {"result_format": "message"}
        if language:
            params["asr_options"] = {"language": language}
    return {"model": model, "input": {"messages": [{"role": "user", "content": [content]}]}, "parameters": params}


def unpack_result(model, result):
    output = result["output"]
    if audio_family(model):
        text = output["text"]
        rows = output.get("sentences") or ([output["sentence"]] if isinstance(output.get("sentence"), dict) else [])
        sentences = [{key: item[key] for key in ("begin_time", "end_time", "text") if key in item}
                     for item in rows if isinstance(item, dict)]
        annotation = output.get("language")
    else:
        message = output["choices"][0]["message"]
        raw = message["content"]
        text = raw if isinstance(raw, str) else " ".join(
            part.get("text", "") for part in raw if isinstance(part, dict))
        sentences = []
        annotation = next((part["language"] for part in message.get("annotations") or []
                           if isinstance(part, dict) and part.get("language")), None)
    usage = result.get("usage")
    return {"status": "transcribed" if text.strip() else "empty", "text": text.strip(), "model": model,
            "language": annotation, "sentences": sentences, "request_id": result.get("request_id"),
            "usage": usage if isinstance(usage, dict) else {}}


async def recognize(data_uri, key, *, model=MODEL, workspace="", language="", keep_dialect=False,
                    endpoint=None, timeout=45):
    route = asr_endpoint(model, workspace, endpoint)
    body = asr_payload(model, data_uri, language, keep_dialect)
    try:
        async with ClientSession(timeout=ClientTimeout(total=timeout), trust_env=False) as session:
            async with session.post(route, json=body, allow_redirects=False, headers={
                "Authorization": "Bearer " + key, "X-DashScope-SSE": "disable",
            }) as response:
                result = await response.json(content_type=None)
                if response.status == 403 and result.get("code") == "AllocationQuota.FreeTierOnly":
                    return {"status": "free_quota_exhausted"}
                if (audio_family(model) and response.status == 400 and result.get("code") == "CLIENT_ERROR"
                        and result.get("message") == "ASR_RESPONSE_HAVE_NO_WORDS"):
                    return {"status": "empty", "model": model, "request_id": result.get("request_id")}
                if response.status in (401, 403, 404, 429) or 400 <= response.status < 500:
                    return {"status": "rejected", "http_status": response.status, "code": result.get("code")}
                if response.status != 200:
                    return {"status": "unconfirmed", "http_status": response.status}
                return unpack_result(model, result)
    except (ClientError, OSError, TimeoutError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        # The provider may have processed the request before the response was lost.
        return {"status": "unconfirmed"}
