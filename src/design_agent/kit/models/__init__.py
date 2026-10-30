"""Chat models: the interface, the Anthropic and OpenAI adapters, and `init_chat_model`."""

from __future__ import annotations

from typing import Any

from design_agent.kit.models.base import (
    BaseChatModel,
    BoundModel,
    CallbackManagerForLLMRun,
    ChatGeneration,
    ChatResult,
    LanguageModelInput,
)


def init_chat_model(spec: str, **kwargs: Any) -> BaseChatModel:
    """A model from `provider:model` (`anthropic:`, `openai:`, or an OpenAI-compatible provider)."""
    provider, _, model = spec.partition(":")
    if not model:
        raise ValueError(f"name a provider and a model, as provider:model (got {spec!r})")
    if provider == "anthropic":
        from design_agent.kit.models.anthropic import ChatAnthropic

        return ChatAnthropic(model=model, **kwargs)
    if provider == "openai":
        from design_agent.kit.models.openai import ChatOpenAI

        return ChatOpenAI(model=model, **kwargs)
    from design_agent.kit.models.openai import COMPATIBLE, ChatOpenAI, compatible_model

    if provider in COMPATIBLE:
        return compatible_model(provider, model, **kwargs)
    if kwargs.get("base_url"):
        chat = ChatOpenAI(model=model, use_responses_api=False, **kwargs)
        chat.provider_name = provider
        return chat
    raise ValueError(
        f"unknown model provider {provider!r}: use anthropic, openai, "
        f"{', '.join(sorted(COMPATIBLE))}, or set a base URL for an OpenAI-compatible endpoint"
    )


__all__ = [
    "BaseChatModel",
    "BoundModel",
    "CallbackManagerForLLMRun",
    "ChatGeneration",
    "ChatResult",
    "LanguageModelInput",
    "init_chat_model",
]
