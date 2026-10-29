"""The chat-model interface the agent loop calls.

A model takes messages and returns one `AIMessage`. `bind_tools` and `bind` return a `BoundModel`
carrying call options (tools, tool choice, cache control); the loop calls `ainvoke` on it.
Providers implement `_agenerate` (or `_generate` for a synchronous fake).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict

from design_agent.kit.messages import AIMessage, BaseMessage, HumanMessage, convert_to_messages

LanguageModelInput = str | Sequence[Any]


@dataclass
class ChatGeneration:
    message: AIMessage
    generation_info: dict[str, Any] | None = None


@dataclass
class ChatResult:
    generations: list[ChatGeneration] = field(default_factory=list)
    llm_output: dict[str, Any] | None = None


class CallbackManagerForLLMRun:
    """Placeholder in provider signatures; there are no callbacks."""


def to_messages(input_: LanguageModelInput) -> list[BaseMessage]:
    if isinstance(input_, str):
        return [HumanMessage(content=input_)]
    return convert_to_messages(input_)


class BaseChatModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow", protected_namespaces=())

    #: Shown in usage, logs and the capability registry.
    model_name: str = ""
    provider_name: str = ""
    #: Input tokens the model accepts, when known (conversation compaction reads it).
    context_window: int | None = None

    @property
    def _llm_type(self) -> str:
        return type(self).__name__

    def _get_ls_params(self, stop: list[str] | None = None, **_: Any) -> dict[str, Any]:
        return {"ls_provider": self.provider_name, "ls_model_name": self.model_name}

    # -- binding ------------------------------------------------------------------------------

    def bind(self, **kwargs: Any) -> BoundModel:
        return BoundModel(self, dict(kwargs))

    def bind_tools(
        self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any
    ) -> BoundModel:
        options = dict(kwargs)
        options["tools"] = list(tools)
        if tool_choice is not None:
            options["tool_choice"] = tool_choice
        return BoundModel(self, options)

    # -- calling ------------------------------------------------------------------------------

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return await asyncio.to_thread(self._generate, messages, stop, run_manager, **kwargs)

    async def ainvoke(
        self, input: LanguageModelInput, config: Any = None, **kwargs: Any
    ) -> AIMessage:
        result = await self._agenerate(to_messages(input), **kwargs)
        message = result.generations[0].message
        if self.model_name and "model_name" not in message.response_metadata:
            message.response_metadata["model_name"] = self.model_name
        return message

    def invoke(self, input: LanguageModelInput, config: Any = None, **kwargs: Any) -> AIMessage:
        result = self._generate(to_messages(input), **kwargs)
        return result.generations[0].message


class BoundModel:
    """A model with call options; `ainvoke` passes them to the provider."""

    def __init__(self, model: BaseChatModel, options: dict[str, Any]) -> None:
        self.bound = model
        self.kwargs = options

    def bind(self, **kwargs: Any) -> BoundModel:
        return BoundModel(self.bound, {**self.kwargs, **kwargs})

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> BoundModel:
        return self.bound.bind_tools(tools, **{**self.kwargs, **kwargs})

    async def ainvoke(
        self, input: LanguageModelInput, config: Any = None, **kwargs: Any
    ) -> AIMessage:
        return await self.bound.ainvoke(input, config, **{**self.kwargs, **kwargs})

    def invoke(self, input: LanguageModelInput, config: Any = None, **kwargs: Any) -> AIMessage:
        return self.bound.invoke(input, config, **{**self.kwargs, **kwargs})

    def __getattr__(self, name: str) -> Any:
        return getattr(self.bound, name)
