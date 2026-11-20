"""The two model routes served by the FishHome console."""

GO_PROVIDER = "opencode-go/deepseek-v4.1-flash"
GO_MODEL = "deepseek-v4.1-flash"
GO_BASES = {"https://opencode.ai/zen/go/v1", "https://opencode.ai/inference/go/openai/v1"}
OFFICIAL_AUTONOMOUS = "fishhome-autonomous/deepseek-flash"
OFFICIAL_INTERACTION_BASE = "http://127.0.0.1:6197/openai/interaction/v1"


def is_go_config(config):
    return (config.get("model") == GO_MODEL
            and config.get("type") == "openai_chat_completion"
            and str(config.get("api_base", "")).rstrip("/") in GO_BASES)


def is_go_provider(provider):
    return provider is not None and is_go_config(provider.provider_config)


def is_interaction_provider(provider):
    return (is_go_provider(provider) or
            (provider is not None and provider.get_model() == "deepseek-flash"
             and str(provider.provider_config.get("api_base", "")).rstrip("/") == OFFICIAL_INTERACTION_BASE))
