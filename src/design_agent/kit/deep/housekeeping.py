"""Middleware every deep agent carries: answering cut-off tool calls, compacting a long
conversation, and Anthropic prompt caching."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from design_agent.kit.config import var_child_runnable_config
from design_agent.kit.messages import (
    REMOVE_ALL_MESSAGES,
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
    count_tokens_approximately,
)
from design_agent.kit.middleware import AgentMiddleware, ModelRequest, ModelResponse

_LOGGER = logging.getLogger(__name__)


class PatchToolCallsMiddleware(AgentMiddleware[Any, Any, Any]):
    """A tool call left without a result (a stopped run) gets one, so the history stays valid."""

    async def abefore_agent(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        messages: list[BaseMessage] = list(state.get("messages") or [])
        answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
        patched: list[BaseMessage] = []
        changed = False
        for message in messages:
            patched.append(message)
            if isinstance(message, AIMessage):
                for call in message.tool_calls:
                    if call.get("id") not in answered:
                        changed = True
                        patched.append(
                            ToolMessage(
                                content=f"Tool call {call.get('name')} with id {call.get('id')} was cancelled: another message came in before it finished.",
                                tool_call_id=str(call.get("id")),
                                name=str(call.get("name")),
                                status="error",
                            )
                        )
        if not changed:
            return None
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *patched]}


SUMMARY_PROMPT = """Summarize the conversation below for yourself, so you can carry on the work without it. Keep: the request and every decision, constraint and preference the person gave; what is done, with exact file paths, commands and results; what failed and why; what is in progress and the next steps. Be specific and complete; skip pleasantries.

{conversation}"""


def _safe_cut(messages: list[BaseMessage], index: int) -> int:
    """Move a cut point back so a tool result never loses the call it answers."""
    while 0 < index < len(messages) and isinstance(messages[index], ToolMessage):
        index -= 1
    return index


class SummarizationMiddleware(AgentMiddleware[Any, Any, Any]):
    """When the conversation passes `trigger_tokens`, older turns become one summary message."""

    def __init__(
        self, *, model: Any, trigger_tokens: int, keep_tokens: int, backend: Any = None
    ) -> None:
        super().__init__()
        self.model = model
        self.trigger_tokens = trigger_tokens
        self.keep_tokens = keep_tokens
        self.backend = backend

    async def abefore_model(self, state: dict[str, Any], runtime: Any) -> dict[str, Any] | None:
        messages: list[BaseMessage] = list(state.get("messages") or [])
        if count_tokens_approximately(messages) <= self.trigger_tokens:
            return None
        kept = 0
        index = len(messages)
        while index > 0:
            size = count_tokens_approximately([messages[index - 1]])
            if kept + size > self.keep_tokens and index < len(messages):
                break
            kept += size
            index -= 1
        cut = _safe_cut(messages, index)
        if cut <= 1:
            return None
        evicted, rest = messages[:cut], messages[cut:]
        transcript = "\n\n".join(
            f"[{m.type}] {m.text}"
            + (f" tool_calls={m.tool_calls}" if isinstance(m, AIMessage) and m.tool_calls else "")
            for m in evicted
        )
        reply = await self.model.ainvoke(
            [
                SystemMessage(content="You write working summaries."),
                HumanMessage(content=SUMMARY_PROMPT.format(conversation=transcript)),
            ]
        )
        saved = await self._offload(transcript)
        note = f"\n\nThe full earlier conversation is saved at {saved}." if saved else ""
        summary = HumanMessage(
            content=f"[Summary of the earlier conversation]\n\n{reply.text}{note}"
        )
        _LOGGER.info("[summarize] %d messages became a summary", len(evicted))
        return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), summary, *rest]}

    async def _offload(self, transcript: str) -> str | None:
        if self.backend is None:
            return None
        config = var_child_runnable_config.get() or {}
        thread = str((config.get("configurable") or {}).get("thread_id") or uuid.uuid4().hex[:8])
        path = f"/workspace/.renders/conversation-history/{thread}-{uuid.uuid4().hex[:6]}.md"
        try:
            result = await self.backend.aupload_files([(path, transcript.encode("utf-8"))])
        except Exception:  # noqa: BLE001 - the summary stands without the saved copy
            return None
        return path if result and result[0].error is None else None


class AnthropicPromptCachingMiddleware(AgentMiddleware[Any, Any, Any]):
    """Ask Anthropic models to cache the request prefix (`cache_control` on the call)."""

    def __init__(self, *, ttl: str = "5m") -> None:
        super().__init__()
        self.ttl = ttl

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        provider = getattr(request.model, "provider_name", None) or getattr(
            getattr(request.model, "bound", None), "provider_name", None
        )
        if provider != "anthropic":
            return await handler(request)
        settings = {
            **request.model_settings,
            "cache_control": {"type": "ephemeral", "ttl": self.ttl},
        }
        return await handler(request.override(model_settings=settings))
