"""Delegation: the `task` tool runs a declared subagent on one brief and returns its final message.

A subagent is its own agent (its model, prompt, tools and middleware) sharing the lead's backend,
so it works on the same files. It starts from the lead's state without the conversation: the brief
is its only message. What it adds to the shared state comes back with its report.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, NotRequired, TypedDict

from design_agent.kit.messages import AIMessage, HumanMessage, ToolMessage
from design_agent.kit.middleware import AgentMiddleware
from design_agent.kit.tools import StructuredTool, ToolRuntime
from design_agent.kit.types import Command


class SubAgent(TypedDict):
    name: str
    description: str
    system_prompt: NotRequired[str]
    model: NotRequired[Any]
    tools: NotRequired[Sequence[Any]]
    middleware: NotRequired[Sequence[AgentMiddleware[Any, Any, Any]]]
    skills: NotRequired[list[str]]
    interrupt_on: NotRequired[dict[str, Any]]


GENERAL_PURPOSE_SUBAGENT: SubAgent = {
    "name": "general-purpose",
    "description": (
        "A general helper for research, multi-step work or anything that would fill your own "
        "context: give it a complete brief and it returns one report."
    ),
    "system_prompt": (
        "You are a helper the lead agent delegated one task to. The task description is your whole "
        "brief: do it with the tools you have, then reply with exactly what it asks you to return. "
        "Your final message is all the lead sees."
    ),
}

#: State the lead keeps to itself: never handed to a subagent, never taken back from one.
EXCLUDED_STATE_KEYS = frozenset(
    {
        "messages",
        "todos",
        "structured_response",
        "skills_metadata",
        "memory_contents",
        "remaining_steps",
        "jump_to",
    }
)

TASK_DESCRIPTION = """Hand one self-contained task to a helper agent, which works in the same files and returns one report.

Write `description` as a complete brief: the helper sees nothing else of this conversation. Pick `subagent_type` from:
{agents}

Launch independent tasks in one message to run them in parallel. The report comes back to you, not the user: pass on what matters."""


def report_text(message: Any) -> str:
    if isinstance(message, AIMessage):
        return message.text
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


class SubAgentMiddleware(AgentMiddleware[Any, Any, Any]):
    """Adds the `task` tool over compiled subagents."""

    def __init__(
        self,
        *,
        subagents: dict[str, tuple[str, Any]],
        description: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__()
        self.subagents = subagents
        listing = "\n".join(f"- {name}: {desc}" for name, (desc, _) in subagents.items())
        agents = self.subagents

        async def task(
            description: str, subagent_type: str, runtime: ToolRuntime
        ) -> Command[Any] | str:
            entry = agents.get(subagent_type)
            if entry is None:
                return f"Unknown subagent_type {subagent_type!r}; use one of: {', '.join(agents)}."
            graph = entry[1]
            inherited = {
                k: v for k, v in dict(runtime.state or {}).items() if k not in EXCLUDED_STATE_KEYS
            }
            result = await graph.ainvoke(
                {**inherited, "messages": [HumanMessage(content=description)]}, runtime.config
            )
            update = {k: v for k, v in result.items() if k not in EXCLUDED_STATE_KEYS}
            final = result.get("messages", [])[-1] if result.get("messages") else None
            update["messages"] = [
                ToolMessage(
                    content=report_text(final),
                    tool_call_id=str(runtime.tool_call_id or ""),
                    name="task",
                )
            ]
            return Command(update=update)

        text = (
            TASK_DESCRIPTION.format(agents=listing) if description is None else description(listing)
        )
        self.tools = [StructuredTool.from_function(coroutine=task, name="task", description=text)]
