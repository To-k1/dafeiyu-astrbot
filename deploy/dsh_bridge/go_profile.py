"""OpenCode Go model configuration for an isolated, single-session DSH worker."""

import re


PROVIDER = "opencode-go"
MODEL = "deepseek-v4.1-flash"
BASE_URL = "https://opencode.ai/inference/go/openai/v1"
KEY_ENV = "FISHHOME_OPENCODE_KEY"
DEFAULT_REASONING = "max"


def render_go_patch(session_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        raise ValueError("Invalid OpenCode session ID")
    return [{"id": "llm-pi-ai", "config": {"providers": {PROVIDER: {
        "api": "openai-completions",
        "apiKeyEnv": KEY_ENV,
        "baseURL": BASE_URL,
        "headers": {"User-Agent": "FishHome-DSH/0.2", "x-opencode-session": session_id},
        "reasoning": DEFAULT_REASONING,
        "compat": {"thinkingFormat": "deepseek", "supportsReasoningEffort": True,
                   "requiresReasoningContentOnAssistantMessages": True},
        "models": [{"id": MODEL, "input": ["text", "image"],
                    "reasoningEfforts": {"high": "high", "max": "max"}}],
        "retryPolicy": {"mode": "normal", "maxRetries": 0},
    }}}}, {"id": "session-title-llm", "disabled": True}]


def render_file_go_patch(bucket, header_plugin, *, endpoint=None):
    if bucket not in {"autonomous", "interaction"}:
        raise ValueError("Unknown DSH file execution scope")
    patch = render_go_patch("temporary")
    config = patch[0]["config"]["providers"][PROVIDER]
    config["headers"].pop("x-opencode-session")
    config["baseURL"] = endpoint or f"http://127.0.0.1:6197/go/{bucket}/v1"
    patch.extend([{"id": "llm-deepseek", "disabled": True},
                  {"insert": [{"id": "fishhome-go-session-header", "name": str(header_plugin.resolve())}]}])
    return patch
