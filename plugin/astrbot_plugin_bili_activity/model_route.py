"""The two model routes served by the FishHome console."""

import re

GO_PROVIDER = "opencode-go/deepseek-v4.1-flash"
GO_MODEL = "deepseek-v4.1-flash"
GO_BASES = {"https://opencode.ai/zen/go/v1", "https://opencode.ai/inference/go/openai/v1"}
OFFICIAL_AUTONOMOUS = "fishhome-autonomous/deepseek-flash"
OFFICIAL_INTERACTION_BASE = "http://127.0.0.1:6197/openai/interaction/v1"


def is_go_model(model):
    return isinstance(model, str) and bool(re.fullmatch(r"deepseek-v4[._a-z0-9-]{1,64}", model))


def is_go_config(config):
    return (is_go_model(config.get("model"))
            and config.get("type") == "openai_chat_completion"
            and str(config.get("api_base", "")).rstrip("/") in GO_BASES)


def is_go_provider(provider):
    return provider is not None and is_go_config(provider.provider_config)


def is_interaction_provider(provider):
    return (is_go_provider(provider) or
            (provider is not None and provider.get_model() == "deepseek-flash"
             and str(provider.provider_config.get("api_base", "")).rstrip("/") == OFFICIAL_INTERACTION_BASE))
