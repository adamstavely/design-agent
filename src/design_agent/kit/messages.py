"""Chat messages: what the agent loop, the models and the middleware pass between them.

`content` is a string or a list of content blocks in the Anthropic shape (`{"type": "text", ...}`,
`{"type": "image", ...}`, `{"type": "thinking", ...}`); provider adapters translate at the edge.
An AI message's tool calls live in `tool_calls` as `{"name", "args", "id", "type": "tool_call"}`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

#: `RemoveMessage(id=REMOVE_ALL_MESSAGES)` in a messages update clears the history before it.
REMOVE_ALL_MESSAGES = "__remove_all__"


class ToolCall(TypedDict, total=False):
    name: str
    args: dict[str, Any]
    id: str | None
    type: Literal["tool_call"]


class TextAccessor(str):
    """A message's text: a string, also callable (`message.text()`) for older call sites."""

    __slots__ = ()

    def __call__(self) -> str:
        return str(self)


def text_of(content: Any) -> str:
    """The text blocks of `content`, joined."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, Mapping) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


class BaseMessage(BaseModel):
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    content: str | list[Any] = ""
    additional_kwargs: dict[str, Any] = Field(default_factory=dict)
    response_metadata: dict[str, Any] = Field(default_factory=dict)
    name: str | None = None
    id: str | None = None
    type: str = "base"

    def __init__(self, content: str | list[Any] | None = None, **kwargs: Any) -> None:
        if content is None:
            content = kwargs.pop("content", "")
        super().__init__(content=content, **kwargs)

    @property
    def text(self) -> TextAccessor:
        return TextAccessor(text_of(self.content))

    @property
    def content_blocks(self) -> list[dict[str, Any]]:
        if isinstance(self.content, str):
            return [{"type": "text", "text": self.content}] if self.content else []
        return [
            b if isinstance(b, dict) else {"type": "text", "text": str(b)} for b in self.content
        ]

    def pretty_repr(self) -> str:
        return f"{self.type}: {self.text}"


class HumanMessage(BaseMessage):
    type: Literal["human"] = "human"

    def __init__(self, content: str | list[Any] | None = None, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)


class SystemMessage(BaseMessage):
    type: Literal["system"] = "system"

    def __init__(self, content: str | list[Any] | None = None, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)


class AIMessage(BaseMessage):
    type: Literal["ai"] = "ai"
    tool_calls: list[ToolCall] = Field(default_factory=list)
    invalid_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    usage_metadata: dict[str, Any] | None = None

    def __init__(self, content: str | list[Any] | None = None, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)

    def model_post_init(self, _context: Any) -> None:
        for call in self.tool_calls:
            call.setdefault("type", "tool_call")
            if not call.get("id"):
                call["id"] = f"call_{uuid.uuid4().hex[:24]}"
            call.setdefault("args", {})


class AIMessageChunk(AIMessage):
    """A streamed piece of an AI message (kept for call sites that test for it)."""


class ToolMessage(BaseMessage):
    type: Literal["tool"] = "tool"
    tool_call_id: str = ""
    status: Literal["success", "error"] = "success"
    artifact: Any = None

    def __init__(self, content: str | list[Any] | None = None, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)


class RemoveMessage(BaseMessage):
    """In a messages update: delete the message with this id (or all, `REMOVE_ALL_MESSAGES`)."""

    type: Literal["remove"] = "remove"

    def __init__(self, id: str, **kwargs: Any) -> None:
        super().__init__(content="", id=id, **kwargs)


AnyMessage = BaseMessage

_BY_TYPE: dict[str, type[BaseMessage]] = {
    "human": HumanMessage,
    "user": HumanMessage,
    "ai": AIMessage,
    "assistant": AIMessage,
    "system": SystemMessage,
    "tool": ToolMessage,
    "remove": RemoveMessage,
}


def convert_to_messages(items: Iterable[Any]) -> list[BaseMessage]:
    """Messages from messages, `(role, text)` pairs, strings or role dicts."""
    out: list[BaseMessage] = []
    for item in items:
        if isinstance(item, BaseMessage):
            out.append(item)
        elif isinstance(item, str):
            out.append(HumanMessage(content=item))
        elif isinstance(item, tuple) and len(item) == 2:
            out.append(_BY_TYPE[str(item[0])](content=item[1]))
        elif isinstance(item, Mapping):
            data = dict(item)
            role = str(data.pop("role", data.pop("type", "human")))
            out.append(_BY_TYPE[role](**data))
        else:
            raise TypeError(f"cannot convert {type(item).__name__} to a message")
    return out


def message_to_dict(message: BaseMessage) -> dict[str, Any]:
    return {"type": message.type, "data": message.model_dump(mode="json")}


def messages_to_dict(messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
    return [message_to_dict(m) for m in messages]


def messages_from_dict(items: Sequence[Mapping[str, Any]]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for item in items:
        data = dict(item.get("data") or {})
        kind = str(item.get("type") or data.get("type") or "human")
        data.pop("type", None)
        cls = _BY_TYPE[kind]
        if cls is RemoveMessage:
            out.append(RemoveMessage(id=str(data.get("id"))))
        else:
            out.append(cls(**data))
    return out


def count_tokens_approximately(
    messages: Iterable[Any],
    *,
    chars_per_token: float = 4.0,
    extra_tokens_per_message: float = 3.0,
    tokens_per_image: int = 85,
    tools: Sequence[Any] | None = None,
    **_: Any,
) -> int:
    """About four characters a token; images a fixed cost each."""
    total = 0.0
    for message in convert_to_messages(messages):
        chars = 0
        if isinstance(message.content, str):
            chars += len(message.content)
        else:
            for block in message.content:
                if isinstance(block, Mapping) and block.get("type") in {"image", "image_url"}:
                    total += tokens_per_image
                else:
                    chars += (
                        len(json.dumps(block, default=str))
                        if not isinstance(block, str)
                        else len(block)
                    )
        if isinstance(message, AIMessage) and message.tool_calls:
            chars += len(json.dumps(message.tool_calls, default=str))
        if isinstance(message, ToolMessage):
            chars += len(message.tool_call_id)
        chars += len(message.type) + len(message.name or "")
        total += chars / chars_per_token + extra_tokens_per_message
    for tool in tools or []:
        schema = tool if isinstance(tool, Mapping) else getattr(tool, "tool_schema", lambda: {})()
        total += len(json.dumps(schema, default=str)) / chars_per_token
    return int(total + 0.999)


def add_messages(
    left: Sequence[BaseMessage], right: Sequence[BaseMessage] | BaseMessage
) -> list[BaseMessage]:
    """The messages reducer: append, replace by id, delete by `RemoveMessage`."""
    incoming = [right] if isinstance(right, BaseMessage) else list(right)
    merged = list(left)
    for message in incoming:
        if message.id is None and not isinstance(message, RemoveMessage):
            message.id = str(uuid.uuid4())
    for message in incoming:
        if isinstance(message, RemoveMessage):
            if message.id == REMOVE_ALL_MESSAGES:
                merged = []
            else:
                merged = [m for m in merged if m.id != message.id]
            continue
        for index, existing in enumerate(merged):
            if existing.id == message.id:
                merged[index] = message
                break
        else:
            merged.append(message)
    return merged
