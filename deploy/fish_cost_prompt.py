"""Conservative OpenAI-format input quote for the documented Flash 1017 template."""

from ._vendor.deepseek_v41_1017.encoding import encode_messages


IMAGE_TOKEN_LIMIT = 1024
# Leave room for the hosted service's header/metadata and special-token handling.
API_TOKEN_MARGIN = 2048
REQUEST_FIELDS = {
    "model", "messages", "tools", "tool_choice", "parallel_tool_calls", "n", "max_tokens",
    "stream", "stream_options", "thinking", "reasoning_effort", "temperature", "top_p",
    "stop", "frequency_penalty", "presence_penalty", "logprobs", "top_logprobs",
}
MESSAGE_FIELDS = {"role", "content", "reasoning_content", "tool_calls", "tool_call_id"}


def openai_input_quote(body):
    """Count rendered UTF-8 bytes to bound the byte-level tokenizer, plus image allowances.

    Other protocols and unmodeled API extensions keep the context-window reservation.
    The local encoder is the public model reference, not a hosted token-count API.
    """
    if body.keys() - REQUEST_FIELDS or body.get("tool_choice") not in (None, "auto", "none"):
        return None
    thinking = body.get("thinking") or {}
    if (not isinstance(thinking, dict) or thinking.keys() - {"type"}
            or thinking.get("type") not in (None, "enabled", "disabled")):
        return None
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    images = 0
    for message in messages:
        if (not isinstance(message, dict) or message.keys() - MESSAGE_FIELDS
                or message.get("role") not in {"system", "user", "assistant", "tool"}):
            return None
        content = message.get("content")
        if isinstance(content, list) and message["role"] == "user":
            for part in content:
                if not isinstance(part, dict):
                    return None
                if part.get("type") == "text" and part.keys() == {"type", "text"}:
                    if not isinstance(part["text"], str):
                        return None
                elif part.get("type") == "image_url" and part.keys() == {"type", "image_url"}:
                    image = part["image_url"]
                    if (not isinstance(image, dict) or image.keys() - {"url", "detail"}
                            or not isinstance(image.get("url"), str) or not image["url"].startswith("data:image/")):
                        return None
                    images += 1
                else:
                    return None
        elif content is not None and not isinstance(content, str):
            return None

    messages = [dict(message) for message in messages]
    if body.get("tools"):
        if messages[0]["role"] != "system":
            messages.insert(0, {"role": "system", "content": ""})
        messages[0]["tools"] = body["tools"]
    try:
        rendered, media = encode_messages(
            messages, thinking_mode="chat" if thinking.get("type") == "disabled" else "thinking",
            drop_thinking=False, reasoning_effort=body.get("reasoning_effort"), return_multi_modal_data=True,
        )
        if len(media["images"]) != images:
            return None
        return len(rendered.encode("utf-8")) + images * IMAGE_TOKEN_LIMIT + API_TOKEN_MARGIN
    except (AssertionError, IndexError, KeyError, ValueError, TypeError, AttributeError, RecursionError):
        return None
