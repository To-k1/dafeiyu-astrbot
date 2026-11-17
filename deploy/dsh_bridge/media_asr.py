"""Optional public-video transcription through the existing Beijing DashScope route."""

from aiohttp import ClientError, ClientSession, ClientTimeout


MODEL = "qwen3-asr-flash"
URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"


async def transcribe_audio(audio, key, *, endpoint=URL):
    content = [{"audio": f"data:{audio['mime']};base64,{audio['data']}"}]
    body = {"model": MODEL, "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": {"result_format": "message"}}
    try:
        async with ClientSession(timeout=ClientTimeout(total=30), trust_env=False) as session:
            async with session.post(endpoint, json=body, headers={"Authorization": "Bearer " + key},
                                    allow_redirects=False) as response:
                result = await response.json(content_type=None)
                if response.status == 403 and result.get("code") == "AllocationQuota.FreeTierOnly":
                    return {"status": "free_quota_exhausted"}
                if response.status != 200:
                    return {"status": "unavailable"}
                raw = result["output"]["choices"][0]["message"]["content"]
                text = raw if isinstance(raw, str) else " ".join(
                    node.get("text", "") for node in raw if isinstance(node, dict))
                return {"status": "transcribed", "model": MODEL, "start": audio["start"], "end": audio["end"],
                        "text": text.strip()[:2500]} if text.strip() else {"status": "empty"}
    except (ClientError, OSError, TimeoutError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return {"status": "unavailable"}
