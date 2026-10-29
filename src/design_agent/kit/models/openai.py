"""OpenAI models through the official `openai` SDK: the Responses API, or Chat Completions.

Chat Completions also serves the providers with an OpenAI-compatible endpoint (Groq, Mistral,
Google's Gemini, a self-hosted server); `init_chat_model` picks their base URLs.
Content blocks are translated at this edge; images become data URLs. A tool result's images go in
the user turn after it, since a tool message carries text only.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any

import openai
from pydantic import PrivateAttr, SecretStr, model_validator

from design_agent.kit.errors import ContextOverflowError
from design_agent.kit.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from design_agent.kit.models.base import BaseChatModel, ChatGeneration, ChatResult
from design_agent.kit.tools import convert_to_openai_tool


def _data_url(block: Mapping[str, Any]) -> str | None:
    if block.get("type") == "image_url":
        image_url = block.get("image_url")
        return image_url.get("url") if isinstance(image_url, Mapping) else image_url
    source = block.get("source")
    if isinstance(source, Mapping):
        if source.get("type") == "base64":
            return f"data:{source.get('media_type')};base64,{source.get('data')}"
        return source.get("url")
    mime = block.get("mime_type") or "image/png"
    data = block.get("base64") or (
        block.get("data") if block.get("source_type") == "base64" else None
    )
    if data:
        return f"data:{mime};base64,{data}"
    return block.get("url")


def _split(content: Any) -> tuple[str, list[str]]:
    """Text and image URLs of a message's content."""
    if isinstance(content, str):
        return content, []
    texts: list[str] = []
    images: list[str] = []
    for block in content or []:
        if isinstance(block, str):
            texts.append(block)
        elif isinstance(block, Mapping):
            if block.get("type") == "text":
                texts.append(str(block.get("text", "")))
            elif block.get("type") in ("image", "image_url"):
                url = _data_url(block)
                if url:
                    images.append(url)
    return "".join(texts), images


def _tool_choice(choice: Any, responses: bool) -> Any:
    if choice is None:
        return None
    if isinstance(choice, Mapping):
        kind = choice.get("type")
        if kind in ("auto", "none"):
            return kind
        if kind == "any":
            return "required"
        if kind == "tool":
            name = choice.get("name")
            return (
                {"type": "function", "name": name}
                if responses
                else {"type": "function", "function": {"name": name}}
            )
        return dict(choice)
    if choice in ("auto", "none", "required"):
        return choice
    if choice == "any" or choice is True:
        return "required"
    return (
        {"type": "function", "name": choice}
        if responses
        else {"type": "function", "function": {"name": choice}}
    )


def to_chat_completions(messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pending_images: list[str] = []

    def user(text: str, images: list[str]) -> dict[str, Any]:
        if not images:
            return {"role": "user", "content": text}
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
        parts += [{"type": "image_url", "image_url": {"url": u}} for u in images]
        return {"role": "user", "content": parts}

    for message in messages:
        if not isinstance(message, ToolMessage) and pending_images:
            out.append(user("Images from the tool results above:", pending_images))
            pending_images = []
        text, images = _split(message.content)
        if isinstance(message, SystemMessage):
            out.append({"role": "system", "content": text})
        elif isinstance(message, AIMessage):
            entry: dict[str, Any] = {"role": "assistant", "content": text or None}
            if message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": c.get("id"),
                        "type": "function",
                        "function": {
                            "name": c.get("name"),
                            "arguments": json.dumps(c.get("args") or {}),
                        },
                    }
                    for c in message.tool_calls
                ]
            out.append(entry)
        elif isinstance(message, ToolMessage):
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": text or "(no output)",
                }
            )
            pending_images += images
        else:
            out.append(user(text, images))
    if pending_images:
        out.append(user("Images from the tool results above:", pending_images))
    return out


def to_responses(messages: Sequence[BaseMessage]) -> tuple[str | None, list[dict[str, Any]]]:
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    pending_images: list[str] = []

    def user(text: str, images: list[str]) -> dict[str, Any]:
        parts: list[dict[str, Any]] = [{"type": "input_text", "text": text}] if text else []
        parts += [{"type": "input_image", "image_url": u} for u in images]
        return {"role": "user", "content": parts}

    for message in messages:
        if not isinstance(message, ToolMessage) and pending_images:
            items.append(user("Images from the tool results above:", pending_images))
            pending_images = []
        text, images = _split(message.content)
        if isinstance(message, SystemMessage):
            instructions.append(text)
        elif isinstance(message, AIMessage):
            if text:
                items.append(
                    {"role": "assistant", "content": [{"type": "output_text", "text": text}]}
                )
            for call in message.tool_calls:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.get("id"),
                        "name": call.get("name"),
                        "arguments": json.dumps(call.get("args") or {}),
                    }
                )
        elif isinstance(message, ToolMessage):
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": text or "(no output)",
                }
            )
            pending_images += images
        else:
            items.append(user(text, images))
    if pending_images:
        items.append(user("Images from the tool results above:", pending_images))
    return ("\n\n".join(instructions) or None), items


def _usage(prompt: int, completion: int, cached: int) -> dict[str, Any]:
    return {
        "input_tokens": prompt,
        "output_tokens": completion,
        "total_tokens": prompt + completion,
        "input_token_details": {"cache_read": cached},
    }


class ChatOpenAI(BaseChatModel):
    model: str = "gpt-5.5"
    max_completion_tokens: int | None = None
    timeout: float | None = None
    max_retries: int = 0
    base_url: str | None = None
    api_key: SecretStr | None = None
    use_responses_api: bool = True
    reasoning: dict[str, Any] | None = None
    temperature: float | None = None
    streaming: bool = False
    provider_name: str = "openai"
    _client: Any = PrivateAttr(default=None)

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, values: Any) -> Any:
        if isinstance(values, dict):
            if "model_name" in values and "model" not in values:
                values["model"] = values["model_name"]
            if "max_tokens" in values:
                values.setdefault("max_completion_tokens", values.pop("max_tokens"))
            if "reasoning_effort" in values:
                values.setdefault("reasoning", {"effort": values.pop("reasoning_effort")})
        return values

    def model_post_init(self, _context: Any) -> None:
        self.model_name = self.model

    def client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            kwargs: dict[str, Any] = {"max_retries": self.max_retries}
            if self.api_key is not None:
                kwargs["api_key"] = self.api_key.get_secret_value()
            if self.base_url:
                kwargs["base_url"] = self.base_url
            if self.timeout is not None:
                kwargs["timeout"] = self.timeout
            self._client = openai.AsyncOpenAI(**kwargs)
        client: openai.AsyncOpenAI = self._client
        return client

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            if self.use_responses_api:
                message = await self._responses(messages, **kwargs)
            else:
                message = await self._chat(messages, stop, **kwargs)
        except openai.BadRequestError as error:
            if "context_length_exceeded" in str(error) or "maximum context length" in str(error):
                raise ContextOverflowError(str(error)) from error
            raise
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _tools(self, tools: Sequence[Any] | None, *, responses: bool) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for tool in tools or []:
            if isinstance(tool, Mapping) and tool.get("type") not in (None, "function"):
                if responses:
                    out.append(dict(tool))  # a server tool (tool_search, web_search)
                continue
            spec = convert_to_openai_tool(tool)
            if responses:
                fn = spec["function"]
                entry = {
                    "type": "function",
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters") or {},
                }
                extras = getattr(tool, "extras", None) or {}
                if extras.get("defer_loading"):
                    entry["defer_loading"] = True
                out.append(entry)
            else:
                out.append(spec)
        return out

    async def _chat(
        self, messages: list[BaseMessage], stop: list[str] | None, **kwargs: Any
    ) -> AIMessage:
        payload: dict[str, Any] = {"model": self.model, "messages": to_chat_completions(messages)}
        tools = self._tools(kwargs.pop("tools", None), responses=False)
        if tools:
            payload["tools"] = tools
            choice = _tool_choice(kwargs.pop("tool_choice", None), False)
            if choice is not None:
                payload["tool_choice"] = choice
        if self.max_completion_tokens:
            payload["max_completion_tokens"] = self.max_completion_tokens
        if self.reasoning and self.reasoning.get("effort"):
            payload["reasoning_effort"] = self.reasoning["effort"]
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if stop:
            payload["stop"] = stop
        response = await self.client().chat.completions.create(**payload)
        choice_ = response.choices[0].message
        calls = []
        for call in choice_.tool_calls or []:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(
                {"name": call.function.name, "args": args, "id": call.id, "type": "tool_call"}
            )
        usage = response.usage
        cached = 0
        if usage is not None and getattr(usage, "prompt_tokens_details", None) is not None:
            cached = int(usage.prompt_tokens_details.cached_tokens or 0)
        return AIMessage(
            content=choice_.content or "",
            tool_calls=calls,
            id=response.id,
            response_metadata={
                "model_name": response.model,
                "model_provider": self.provider_name,
                "finish_reason": response.choices[0].finish_reason,
            },
            usage_metadata=_usage(usage.prompt_tokens, usage.completion_tokens, cached)
            if usage
            else None,
        )

    async def _responses(self, messages: list[BaseMessage], **kwargs: Any) -> AIMessage:
        instructions, items = to_responses(messages)
        payload: dict[str, Any] = {"model": self.model, "input": items, "store": False}
        if instructions:
            payload["instructions"] = instructions
        tools = self._tools(kwargs.pop("tools", None), responses=True)
        if tools:
            payload["tools"] = tools
            choice = _tool_choice(kwargs.pop("tool_choice", None), True)
            if choice is not None:
                payload["tool_choice"] = choice
        if self.max_completion_tokens:
            payload["max_output_tokens"] = self.max_completion_tokens
        if self.reasoning:
            payload["reasoning"] = dict(self.reasoning)
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        response = await self.client().responses.create(**payload)
        calls = []
        for item in response.output:
            if getattr(item, "type", None) == "function_call":
                try:
                    args = json.loads(item.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls.append(
                    {"name": item.name, "args": args, "id": item.call_id, "type": "tool_call"}
                )
        usage = response.usage
        cached = 0
        if usage is not None and getattr(usage, "input_tokens_details", None) is not None:
            cached = int(usage.input_tokens_details.cached_tokens or 0)
        return AIMessage(
            content=response.output_text or "",
            tool_calls=calls,
            id=response.id,
            response_metadata={
                "model_name": response.model,
                "model_provider": self.provider_name,
                "status": response.status,
            },
            usage_metadata=_usage(usage.input_tokens, usage.output_tokens, cached)
            if usage
            else None,
        )


#: OpenAI-compatible endpoints for providers addressed as `provider:model`, and their key variable.
COMPATIBLE: dict[str, tuple[str, str]] = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "mistralai": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "google_genai": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GOOGLE_API_KEY"),
    "google": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GOOGLE_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
}


def compatible_model(provider: str, model: str, **kwargs: Any) -> ChatOpenAI:
    """A Chat Completions model on a provider's OpenAI-compatible endpoint."""
    base_url, key_env = COMPATIBLE[provider]
    kwargs.setdefault("base_url", base_url)
    if "api_key" not in kwargs and os.environ.get(key_env):
        kwargs["api_key"] = os.environ[key_env]
    chat = ChatOpenAI(model=model, use_responses_api=False, **kwargs)
    chat.provider_name = provider
    return chat
