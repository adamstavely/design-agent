"""Agent middleware: hooks around a run, each model call and each tool call.

A middleware may define any of:

- `before_agent` / `after_agent`: once per run, at the start and the end;
- `before_model` / `after_model`: around each model step, outside the call itself;
- `wrap_model_call(request, handler)`: around the model call; it may change the request, retry,
  or answer without calling `handler`;
- `wrap_tool_call(request, handler)`: around each tool call.

Each has an `a`-prefixed async form, which the loop prefers. `before_*`/`after_*` hooks return a
state update (a dict) or None; an update may carry `"jump_to": "end"` when the hook is marked
`@hook_config(can_jump_to=["end"])`. Before-hooks run in list order, after-hooks in reverse, and
the first middleware's wrap is the outermost.

`state_schema` declares the state fields a middleware keeps; a field annotated with a reducer
(`Annotated[T, reducer]`) merges updates through it, anything else is replaced. `tools` lists the
tools a middleware adds to the agent.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Annotated, Any, Generic, Literal, NotRequired, TypedDict

from typing_extensions import TypeVar

from design_agent.kit.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from design_agent.kit.tools import BaseTool, StructuredTool, ToolRuntime
from design_agent.kit.types import Command

_LOGGER = logging.getLogger(__name__)

StateT = TypeVar("StateT", default=Any)
ContextT = TypeVar("ContextT", default=Any)
ResponseT = TypeVar("ResponseT", default=Any)


class PrivateStateAttr:
    """Marks a state field as internal to the agent (not part of its input or output)."""


class OmitFromSchema:
    """Marks a state field as left out of the agent's input or output schema."""

    def __init__(self, input: bool = True, output: bool = True) -> None:
        self.input = input
        self.output = output


class AgentState(TypedDict, Generic[ResponseT], total=False):
    messages: list[AnyMessage]
    jump_to: NotRequired[Annotated[str | None, PrivateStateAttr]]
    structured_response: NotRequired[Any]


HookT = TypeVar("HookT", bound=Callable[..., Any])


def hook_config(*, can_jump_to: Sequence[str] | None = None) -> Callable[[HookT], HookT]:
    """Declare where a before/after hook may jump (`"end"`, `"model"`, `"tools"`)."""

    def mark(func: HookT) -> HookT:
        func.__can_jump_to__ = list(can_jump_to or [])  # type: ignore[attr-defined]
        return func

    return mark


@dataclass
class ModelRequest(Generic[ContextT]):
    """One model call, as the wraps see it."""

    model: Any
    messages: list[BaseMessage]
    system_message: SystemMessage | None = None
    tool_choice: Any = None
    tools: list[Any] = field(default_factory=list)
    response_format: Any = None
    state: Any = field(default_factory=dict)
    runtime: Any = None
    model_settings: dict[str, Any] = field(default_factory=dict)

    @property
    def system_prompt(self) -> str | None:
        return self.system_message.text if self.system_message is not None else None

    def override(self, **changes: Any) -> ModelRequest[ContextT]:
        if "system_prompt" in changes:
            prompt = changes.pop("system_prompt")
            changes["system_message"] = (
                SystemMessage(content=prompt) if prompt is not None else None
            )
        return replace(self, **changes)


@dataclass
class ModelResponse(Generic[ResponseT]):
    """What a model call produced: its messages (normally one AI message)."""

    result: list[BaseMessage]
    structured_response: Any = None


@dataclass
class ToolCallRequest:
    """One tool call, as the wraps see it."""

    tool_call: ToolCall
    tool: BaseTool | None
    state: Any
    runtime: ToolRuntime

    def override(self, **changes: Any) -> ToolCallRequest:
        return replace(self, **changes)


@dataclass
class InterruptOnConfig:
    allowed_decisions: list[str] = field(default_factory=lambda: ["approve", "edit", "reject"])
    description: str | None = None


ModelCallResult = ModelResponse | AIMessage


class AgentMiddleware(Generic[StateT, ContextT, ResponseT]):
    state_schema: type | None = None
    tools: Sequence[Any] = ()

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    @property
    def name(self) -> str:
        return type(self).__name__

    # The hooks do nothing here; the loop runs only the ones a subclass defines (`overrides`).

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return None

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return None

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return None

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return None

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return None

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return None

    def after_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return None

    async def aafter_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return None

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(request)

    async def awrap_model_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        return await handler(request)

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        return await handler(request)


def overrides(middleware: AgentMiddleware[Any, Any, Any], hook: str) -> bool:
    """Whether `middleware`'s class defines `hook` itself rather than inheriting the no-op."""
    return getattr(type(middleware), hook, None) is not getattr(AgentMiddleware, hook, None)


# -- built-in middleware ------------------------------------------------------------------------


class Todo(TypedDict):
    content: str
    status: Literal["pending", "in_progress", "completed"]


class PlanningState(AgentState[Any]):
    todos: NotRequired[list[Todo]]


TODO_TOOL_DESCRIPTION = """Keep a short task list for this job and update it as you work.

Use it for work with three or more real steps: mark one item `in_progress` before you start it and `completed` as soon as it is done, add items you discover, and drop ones that no longer apply. Skip it for small or conversational requests. Each call replaces the whole list. The list is for tracking; your answer to the person goes in a message after your last update."""

TODO_SYSTEM_PROMPT = """## Task list (`write_todos`)

For a job with several steps, keep a task list with `write_todos` and keep it current: one item in progress at a time unless steps run in parallel, each marked complete as soon as it is done. Never call it twice in parallel. Skip it for quick requests. When the work is done, write the result itself in a message after your last update."""


class TodoListMiddleware(AgentMiddleware[PlanningState, Any, Any]):
    """The `write_todos` tool and its system prompt note."""

    state_schema = PlanningState

    def __init__(
        self,
        *,
        system_prompt: str = TODO_SYSTEM_PROMPT,
        tool_description: str = TODO_TOOL_DESCRIPTION,
    ) -> None:
        super().__init__()
        self.system_prompt = system_prompt

        async def write_todos(todos: list[Todo], runtime: ToolRuntime) -> Command[Any]:
            return Command(
                update={
                    "todos": todos,
                    "messages": [
                        ToolMessage(
                            content=f"Updated todo list to {json.dumps(todos)}",
                            tool_call_id=str(runtime.tool_call_id or ""),
                            name="write_todos",
                        )
                    ],
                }
            )

        self.tools = [
            StructuredTool.from_function(
                coroutine=write_todos, name="write_todos", description=tool_description
            )
        ]

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(append_system(request, self.system_prompt))


def append_system(request: ModelRequest[Any], text: str) -> ModelRequest[Any]:
    """`request` with `text` appended to its system prompt as a new block."""
    if not text:
        return request
    current = request.system_message
    if current is None:
        return request.override(system_message=SystemMessage(content=text))
    blocks = list(current.content_blocks)
    blocks.append({"type": "text", "text": f"\n\n{text}"})
    return request.override(system_message=SystemMessage(content=blocks))


def calculate_delay(
    retry_number: int,
    *,
    backoff_factor: float,
    initial_delay: float,
    max_delay: float,
    jitter: bool,
) -> float:
    delay = (
        initial_delay if backoff_factor == 0.0 else initial_delay * (backoff_factor**retry_number)
    )
    delay = min(delay, max_delay)
    if jitter and delay > 0:
        delay = max(0.0, delay + delay * random.uniform(-0.25, 0.25))  # noqa: S311 - retry jitter, not security
    return delay


class ModelRetryMiddleware(AgentMiddleware[Any, Any, Any]):
    """Retry a failed model call `max_retries` times when `retry_on(error)` says so."""

    def __init__(
        self,
        *,
        max_retries: int = 2,
        retry_on: Callable[[Exception], bool] | tuple[type[Exception], ...] = (Exception,),
        on_failure: Literal["error", "continue"] | Callable[[Exception], str] = "continue",
        backoff_factor: float = 2.0,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        jitter: bool = True,
    ) -> None:
        super().__init__()
        self.max_retries = max_retries
        self.retry_on = retry_on
        self.on_failure = on_failure
        self.backoff_factor = backoff_factor
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.jitter = jitter

    def _retryable(self, error: Exception) -> bool:
        if isinstance(self.retry_on, tuple):
            return isinstance(error, self.retry_on)
        return bool(self.retry_on(error))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        from design_agent.kit.errors import GraphBubbleUp

        for attempt in range(self.max_retries + 1):
            try:
                return await handler(request)
            except GraphBubbleUp:
                raise
            except Exception as error:
                if attempt < self.max_retries and self._retryable(error):
                    await asyncio.sleep(
                        calculate_delay(
                            attempt,
                            backoff_factor=self.backoff_factor,
                            initial_delay=self.initial_delay,
                            max_delay=self.max_delay,
                            jitter=self.jitter,
                        )
                    )
                    continue
                if self.on_failure == "error":
                    raise
                text = (
                    self.on_failure(error)
                    if callable(self.on_failure)
                    else f"Model call failed after {attempt + 1} attempts with {type(error).__name__}"
                )
                return ModelResponse(result=[AIMessage(content=text)])
        raise AssertionError("unreachable")


# -- tool disclosure ----------------------------------------------------------------------------

#: Provider-native tool search tools, by provider.
SERVER_TOOL_SEARCH_TOOLS: dict[str, dict[str, Any]] = {
    "anthropic": {"type": "tool_search_tool_bm25_20251119", "name": "tool_search_tool_bm25"},
    "openai": {"type": "tool_search"},
}


def model_provider(model: Any) -> str | None:
    provider = getattr(model, "provider_name", None) or getattr(
        getattr(model, "bound", None), "provider_name", None
    )
    return str(provider) if provider else None


def _deferred(tool: Any, names: set[str]) -> bool:
    if isinstance(tool, BaseTool):
        return tool.name in names or bool((tool.extras or {}).get("defer_loading"))
    return False


class ProviderToolSearchMiddleware(AgentMiddleware[Any, Any, Any]):
    """Defer tools behind the provider's own tool search (Anthropic, OpenAI)."""

    def __init__(self, *, searchable_tools: Sequence[str | BaseTool] | None = None) -> None:
        super().__init__()
        self.searchable_tool_names = {
            t if isinstance(t, str) else t.name for t in searchable_tools or []
        }

    def _prepare_request(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        tools = request.tools
        if not any(_deferred(t, self.searchable_tool_names) for t in tools):
            return request
        provider = model_provider(request.model)
        if provider not in SERVER_TOOL_SEARCH_TOOLS:
            raise ValueError(f"provider tool search needs anthropic or openai, not {provider!r}")
        marked = []
        for tool in tools:
            if isinstance(tool, BaseTool) and tool.name in self.searchable_tool_names:
                tool = tool.model_copy(
                    update={"extras": {**(tool.extras or {}), "defer_loading": True}}
                )
            marked.append(tool)
        return request.override(tools=[*marked, dict(SERVER_TOOL_SEARCH_TOOLS[provider])])

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(self._prepare_request(request))


@dataclass
class SelectionRequest:
    available_tools: list[BaseTool]
    system_message: str
    last_user_message: HumanMessage
    model: Any
    valid_tool_names: list[str]


SELECTOR_PROMPT = "Pick the tools most relevant to the user's latest request."


class LLMToolSelectorMiddleware(AgentMiddleware[Any, Any, Any]):
    """A small model picks the tools each call is offered (at most `max_tools`)."""

    def __init__(
        self,
        *,
        model: Any = None,
        system_prompt: str = SELECTOR_PROMPT,
        max_tools: int | None = None,
        always_include: list[str] | None = None,
        max_retries: int = 1,
    ) -> None:
        super().__init__()
        self.model = model
        self.system_prompt = system_prompt
        self.max_tools = max_tools
        self.always_include = always_include or []
        self.max_retries = max_retries

    def _prepare_selection_request(self, request: ModelRequest[Any]) -> SelectionRequest | None:
        base = [t for t in request.tools if isinstance(t, BaseTool)]
        available = [t for t in base if t.name not in self.always_include]
        if not available:
            return None
        prompt = self.system_prompt
        if self.max_tools is not None:
            prompt += f"\nList the most relevant first; only the first {self.max_tools} are used."
        last = next((m for m in reversed(request.messages) if isinstance(m, HumanMessage)), None)
        if last is None:
            return None
        return SelectionRequest(
            available, prompt, last, self.model or request.model, [t.name for t in available]
        )

    def _process_selection_response(
        self,
        response: dict[str, Any],
        available_tools: list[BaseTool],
        valid_tool_names: list[str],
        request: ModelRequest[Any],
        *,
        apply_max_tools: bool = True,
    ) -> ModelRequest[Any]:
        chosen: list[str] = []
        limit = self.max_tools if apply_max_tools else None
        for name in response.get("tools", []):
            if (
                name in valid_tool_names
                and name not in chosen
                and (limit is None or len(chosen) < limit)
            ):
                chosen.append(name)
        selected = [t for t in available_tools if t.name in chosen]
        selected += [
            t for t in request.tools if isinstance(t, BaseTool) and t.name in self.always_include
        ]
        provider_tools = [t for t in request.tools if not isinstance(t, BaseTool)]
        return request.override(tools=[*selected, *provider_tools])

    async def _select(self, selection: SelectionRequest) -> dict[str, Any]:
        listing = "\n".join(f"- {t.name}: {t.description}" for t in selection.available_tools)
        schema = {
            "name": "select_tools",
            "description": "Choose the tools to use, most relevant first.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "tools": {
                        "type": "array",
                        "items": {"type": "string", "enum": selection.valid_tool_names},
                    }
                },
                "required": ["tools"],
            },
        }
        bound = selection.model.bind_tools([schema], tool_choice="select_tools")
        messages = [
            SystemMessage(content=f"{selection.system_message}\n\nTools:\n{listing}"),
            HumanMessage(content=selection.last_user_message.content),
        ]
        last: Any = None
        for _ in range(self.max_retries + 1):
            reply = await bound.ainvoke(messages)
            for call in reply.tool_calls:
                if isinstance(call.get("args", {}).get("tools"), list):
                    return {"tools": call["args"]["tools"]}
            last = reply
        raise ValueError(f"the tool selector gave no selection ({type(last).__name__})")

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        selection = self._prepare_selection_request(request)
        if selection is None:
            return await handler(request)
        response = await self._select(selection)
        return await handler(
            self._process_selection_response(
                response, selection.available_tools, selection.valid_tool_names, request
            )
        )


__all__ = [
    "AgentMiddleware",
    "AgentState",
    "InterruptOnConfig",
    "LLMToolSelectorMiddleware",
    "ModelRequest",
    "ModelResponse",
    "ModelRetryMiddleware",
    "OmitFromSchema",
    "PrivateStateAttr",
    "ProviderToolSearchMiddleware",
    "TodoListMiddleware",
    "ToolCallRequest",
    "append_system",
    "hook_config",
]
