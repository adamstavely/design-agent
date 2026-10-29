"""Claude through the official `anthropic` SDK.

Every call streams (`messages.stream` + `get_final_message`), so a long generation never meets the
non-streaming request ceiling and the stall timeout measures silence, not length. Messages are
translated at this edge: system messages become `system`, tool results become `tool_result`
blocks in a user turn, and an AI message's tool calls become its `tool_use` blocks. Content the
model returned (thinking with its signature, server tool blocks) goes back as it came.

A `cache_control` call option becomes the request's top-level `cache_control`, which caches the
last cacheable block; `effort` goes in `output_config`. `betas` or `context_management` send the
request through the beta endpoint.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping, Sequence
from typing import Any

import anthropic
from pydantic import PrivateAttr, SecretStr, model_validator

from design_agent.kit.errors import ContextOverflowError
from design_agent.kit.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from design_agent.kit.models.base import BaseChatModel, ChatGeneration, ChatResult
from design_agent.kit.tools import to_anthropic_tool

DEFAULT_MAX_TOKENS = 64_000
_DATA_URL = re.compile(r"^data:(?P<mime>[\w/+.-]+);base64,(?P<data>.*)$", re.S)
_TEXT_KEYS = {"type", "text", "cache_control", "citations"}
_PASS_THROUGH = {
    "thinking",
    "redacted_thinking",
    "server_tool_use",
    "web_search_tool_result",
    "web_fetch_tool_result",
    "code_execution_tool_result",
    "tool_search_tool_result",
    "document",
    "search_result",
    "container_upload",
}


def _image_source(block: Mapping[str, Any]) -> dict[str, Any] | None:
    source = block.get("source")
    if isinstance(source, Mapping):
        return dict(source)
    mime = block.get("mime_type") or block.get("media_type") or "image/png"
    if block.get("base64"):
        return {"type": "base64", "media_type": mime, "data": block["base64"]}
    if block.get("source_type") == "base64" and block.get("data"):
        return {"type": "base64", "media_type": mime, "data": block["data"]}
    url = block.get("url")
    if block.get("type") == "image_url":
        image_url = block.get("image_url")
        url = image_url.get("url") if isinstance(image_url, Mapping) else image_url
    if isinstance(url, str):
        match = _DATA_URL.match(url)
        if match:
            return {"type": "base64", "media_type": match["mime"], "data": match["data"]}
        return {"type": "url", "url": url}
    return None


def _block(block: Any) -> dict[str, Any] | None:
    """One content block in the shape the Messages API takes; None drops it."""
    if isinstance(block, str):
        return {"type": "text", "text": block} if block else None
    if not isinstance(block, Mapping):
        return None
    kind = block.get("type")
    if kind == "text":
        if not block.get("text"):
            return None
        return {k: v for k, v in block.items() if k in _TEXT_KEYS and v is not None}
    if kind in ("image", "image_url"):
        source = _image_source(block)
        if source is None:
            return None
        out: dict[str, Any] = {"type": "image", "source": source}
        if block.get("cache_control"):
            out["cache_control"] = block["cache_control"]
        return out
    if kind == "file":
        mime = block.get("mime_type") or "application/pdf"
        data = block.get("base64") or block.get("data")
        if data and mime == "application/pdf":
            return {
                "type": "document",
                "source": {"type": "base64", "media_type": mime, "data": data},
            }
        if data and mime.startswith("text/"):
            text = base64.b64decode(data).decode("utf-8", errors="replace")
            return {"type": "text", "text": text}
        return None
    if kind in _PASS_THROUGH or kind == "tool_use" or kind == "tool_result":
        return dict(block)
    return None


def _blocks(content: Any) -> list[dict[str, Any]]:
    items = [content] if isinstance(content, str) else list(content or [])
    return [b for b in (_block(i) for i in items) if b is not None]


def _assistant_content(message: AIMessage) -> list[dict[str, Any]]:
    blocks = [b for b in _blocks(message.content) if b.get("type") != "tool_use"]
    # Thinking leads an assistant turn.
    thinking = [b for b in blocks if b.get("type") in ("thinking", "redacted_thinking")]
    rest = [b for b in blocks if b.get("type") not in ("thinking", "redacted_thinking")]
    calls = [
        {
            "type": "tool_use",
            "id": call.get("id"),
            "name": call.get("name"),
            "input": dict(call.get("args") or {}),
        }
        for call in message.tool_calls
    ]
    return [*thinking, *rest, *calls]


def _tool_result(message: ToolMessage) -> dict[str, Any]:
    content = _blocks(message.content) if not isinstance(message.content, str) else message.content
    if isinstance(content, list):
        content = [
            b for b in content if b.get("type") in ("text", "image", "document", "search_result")
        ]
    out: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": message.tool_call_id,
        "content": content,
    }
    if message.status == "error":
        out["is_error"] = True
    return out


def to_anthropic(
    messages: Sequence[BaseMessage],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """`(system, messages)` for the Messages API, with same-role turns merged."""
    system: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []

    def push(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": list(blocks)})

    for message in messages:
        if isinstance(message, SystemMessage):
            system.extend(b for b in _blocks(message.content) if b.get("type") == "text")
        elif isinstance(message, AIMessage):
            push("assistant", _assistant_content(message))
        elif isinstance(message, ToolMessage):
            push("user", [_tool_result(message)])
        else:
            push("user", _blocks(message.content))
    # Tool results lead their user turn.
    for turn in turns:
        if turn["role"] == "user":
            results = [b for b in turn["content"] if b.get("type") == "tool_result"]
            if results:
                turn["content"] = [
                    *results,
                    *[b for b in turn["content"] if b.get("type") != "tool_result"],
                ]
    return system, turns


def _tool_choice(choice: Any) -> dict[str, Any] | None:
    if choice is None:
        return None
    if isinstance(choice, Mapping):
        return dict(choice)
    if choice in ("auto", "any", "none"):
        return {"type": choice}
    if choice is True or choice == "required":
        return {"type": "any"}
    return {"type": "tool", "name": str(choice)}


def from_anthropic(response: Any, provider: str = "anthropic") -> AIMessage:
    blocks = [
        b.model_dump(mode="json", exclude_none=True) if hasattr(b, "model_dump") else dict(b)
        for b in response.content
    ]
    calls = [
        {"name": b["name"], "args": b.get("input") or {}, "id": b["id"], "type": "tool_call"}
        for b in blocks
        if b.get("type") == "tool_use"
    ]
    simple = all(b.get("type") == "text" and not b.get("citations") for b in blocks)
    content: str | list[Any] = "".join(b["text"] for b in blocks) if simple else blocks
    usage = response.usage.model_dump(mode="json") if response.usage is not None else {}
    read = int(usage.get("cache_read_input_tokens") or 0)
    created = int(usage.get("cache_creation_input_tokens") or 0)
    split = usage.get("cache_creation") or {}
    details: dict[str, int] = {"cache_read": read, "cache_creation": created}
    for key in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"):
        if split.get(key):
            details[key] = int(split[key])
    total_in = int(usage.get("input_tokens") or 0) + read + created
    out_tokens = int(usage.get("output_tokens") or 0)
    metadata: dict[str, Any] = {
        "model_name": response.model,
        "model_provider": provider,
        "stop_reason": response.stop_reason,
        "id": response.id,
    }
    managed = getattr(response, "context_management", None)
    if managed is not None:
        metadata["context_management"] = (
            managed.model_dump(mode="json") if hasattr(managed, "model_dump") else managed
        )
    return AIMessage(
        content=content,
        tool_calls=calls,
        id=response.id,
        response_metadata=metadata,
        usage_metadata={
            "input_tokens": total_in,
            "output_tokens": out_tokens,
            "total_tokens": total_in + out_tokens,
            "input_token_details": details,
        },
    )


class ChatAnthropic(BaseChatModel):
    model: str = "claude-opus-5-5"
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout: float | None = None
    max_retries: int = 0
    base_url: str | None = None
    api_key: SecretStr | None = None
    betas: list[str] | None = None
    context_management: dict[str, Any] | None = None
    effort: str | None = None
    thinking: dict[str, Any] | None = None
    temperature: float | None = None
    streaming: bool = True
    default_headers: dict[str, str] | None = None
    provider_name: str = "anthropic"
    _client: Any = PrivateAttr(default=None)

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, values: Any) -> Any:
        if isinstance(values, dict):
            if "model_name" in values and "model" not in values:
                values["model"] = values["model_name"]
            if "max_tokens_to_sample" in values:
                values.setdefault("max_tokens", values.pop("max_tokens_to_sample"))
        return values

    def model_post_init(self, _context: Any) -> None:
        self.model_name = self.model

    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            kwargs: dict[str, Any] = {"max_retries": self.max_retries}
            if self.api_key is not None:
                kwargs["api_key"] = self.api_key.get_secret_value()
            if self.base_url:
                kwargs["base_url"] = self.base_url
            if self.timeout is not None:
                kwargs["timeout"] = self.timeout
            if self.default_headers:
                kwargs["default_headers"] = self.default_headers
            self._client = anthropic.AsyncAnthropic(**kwargs)
        client: anthropic.AsyncAnthropic = self._client
        return client

    def _get_request_payload(
        self, input_: Sequence[BaseMessage], *, stop: list[str] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        system, messages = to_anthropic(input_)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": kwargs.pop("max_tokens", None) or self.max_tokens,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        tools = kwargs.pop("tools", None)
        if tools:
            payload["tools"] = [to_anthropic_tool(t) for t in tools]
        choice = _tool_choice(kwargs.pop("tool_choice", None))
        if choice is not None and (tools or choice.get("type") == "none"):
            payload["tool_choice"] = choice
        control = kwargs.pop("cache_control", None)
        if isinstance(control, Mapping):
            payload["cache_control"] = dict(control)
        if stop:
            payload["stop_sequences"] = stop
        if self.thinking is not None:
            payload["thinking"] = dict(self.thinking)
        effort = kwargs.pop("effort", None) or self.effort
        if effort is not None:
            payload["output_config"] = {**payload.get("output_config", {}), "effort": effort}
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.context_management is not None:
            payload["context_management"] = self.context_management
        betas = list(self.betas or [])
        if betas:
            payload["betas"] = betas
        for key in ("metadata", "output_config", "service_tier", "container", "mcp_servers"):
            if key in kwargs:
                payload[key] = kwargs.pop(key)
        return payload

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        client = self.client()
        uses_beta = "betas" in payload or "context_management" in payload
        response: Any
        try:
            if uses_beta:
                async with client.beta.messages.stream(**payload) as stream:
                    response = await stream.get_final_message()
            else:
                async with client.messages.stream(**payload) as stream:
                    response = await stream.get_final_message()
        except anthropic.BadRequestError as error:
            if "prompt is too long" in str(error).lower():
                raise ContextOverflowError(str(error)) from error
            raise
        return ChatResult(generations=[ChatGeneration(message=from_anthropic(response))])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        import asyncio

        return asyncio.run(self._agenerate(messages, stop, run_manager, **kwargs))


__all__ = ["ChatAnthropic", "HumanMessage", "from_anthropic", "to_anthropic"]
