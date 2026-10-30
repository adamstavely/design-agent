"""The deep-agent harness: an agent with file and shell tools over a backend, skills, `task`
subagents, conversation compaction and prompt caching, around the caller's own middleware.

Middleware order, outermost first: file tools, `task`, compaction, cut-off call repair, then the
caller's middleware, then the skills list and prompt caching. Subagents get the same around their
own middleware, without `task`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from design_agent.kit.agent import CompiledAgent, create_agent
from design_agent.kit.deep.backends import BackendProtocol, StateBackend
from design_agent.kit.deep.filesystem import FilesystemMiddleware
from design_agent.kit.deep.housekeeping import (
    AnthropicPromptCachingMiddleware,
    PatchToolCallsMiddleware,
    SummarizationMiddleware,
)
from design_agent.kit.deep.skills import SkillsMiddleware
from design_agent.kit.deep.subagents import GENERAL_PURPOSE_SUBAGENT, SubAgent, SubAgentMiddleware
from design_agent.kit.middleware import AgentMiddleware

DEFAULT_CONTEXT_WINDOW = 200_000


def _core(model: Any, backend: BackendProtocol) -> list[AgentMiddleware[Any, Any, Any]]:
    window = int(getattr(model, "context_window", None) or DEFAULT_CONTEXT_WINDOW)
    return [
        FilesystemMiddleware(backend=backend),
        SummarizationMiddleware(
            model=model,
            trigger_tokens=int(window * 0.85),
            keep_tokens=int(window * 0.1),
            backend=backend,
        ),
        PatchToolCallsMiddleware(),
    ]


def _tail(
    backend: BackendProtocol, skills: Sequence[str] | None
) -> list[AgentMiddleware[Any, Any, Any]]:
    tail: list[AgentMiddleware[Any, Any, Any]] = []
    if skills:
        tail.append(SkillsMiddleware(backend=backend, sources=list(skills)))
    tail.append(AnthropicPromptCachingMiddleware())
    return tail


def create_deep_agent(
    model: Any,
    tools: Sequence[Any] | None = None,
    *,
    system_prompt: str | None = None,
    middleware: Sequence[AgentMiddleware[Any, Any, Any]] = (),
    subagents: Sequence[SubAgent] | None = None,
    skills: list[str] | None = None,
    backend: BackendProtocol | None = None,
    interrupt_on: dict[str, Any] | None = None,  # noqa: ARG001 - no human-in-the-loop pauses
    checkpointer: Any = None,
    store: Any = None,
    name: str | None = None,
    **_: Any,
) -> CompiledAgent:
    if isinstance(model, str):
        from design_agent.kit.models import init_chat_model

        model = init_chat_model(model)
    backend = backend if backend is not None else StateBackend()
    base_tools = list(tools or [])
    specs = list(subagents or [])
    if not any(s["name"] == GENERAL_PURPOSE_SUBAGENT["name"] for s in specs):
        specs.insert(0, GENERAL_PURPOSE_SUBAGENT)
    compiled: dict[str, tuple[str, Any]] = {}
    for spec in specs:
        sub_model = spec.get("model") or model
        if isinstance(sub_model, str):
            from design_agent.kit.models import init_chat_model

            sub_model = init_chat_model(sub_model)
        compiled[spec["name"]] = (
            spec["description"],
            create_agent(
                sub_model,
                list(spec["tools"]) if "tools" in spec else base_tools,
                system_prompt=spec.get("system_prompt"),
                middleware=[
                    *_core(sub_model, backend),
                    *spec.get("middleware", []),
                    *_tail(backend, spec.get("skills")),
                ],
                store=store,
                name=spec["name"],
            ),
        )
    core = _core(model, backend)
    stack: list[AgentMiddleware[Any, Any, Any]] = [
        core[0],
        SubAgentMiddleware(subagents=compiled),
        *core[1:],
        *middleware,
        *_tail(backend, skills),
    ]
    return create_agent(
        model,
        base_tools,
        system_prompt=system_prompt,
        middleware=stack,
        checkpointer=checkpointer,
        store=store,
        name=name,
    )


__all__ = ["GENERAL_PURPOSE_SUBAGENT", "SubAgent", "create_deep_agent"]
