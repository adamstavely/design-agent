"""The agent loop: model, tools, middleware hooks, state, checkpoints.

One run (`ainvoke`) on a thread:

1. load the thread's saved state and add the input to it;
2. `before_agent` hooks;
3. repeat: `before_model` hooks, the model call through every `wrap_model_call`, `after_model`
   hooks, then every tool call the model made, in parallel, each through every `wrap_tool_call`;
   stop when the model answers without tool calls, a hook jumps to the end, or a tool says so;
4. `after_agent` hooks; save the state.

Every hook, model call and tool round is one step; `remaining_steps` in the state is the run's
`recursion_limit` minus the steps taken, and the run raises `GraphRecursionError` when none are
left. The run's config is in scope for everything under it (`config.get_config`).
"""

from __future__ import annotations

import asyncio
import logging
import typing
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from design_agent.kit.config import var_child_runnable_config
from design_agent.kit.errors import GraphRecursionError
from design_agent.kit.messages import (
    AIMessage,
    BaseMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    add_messages,
    convert_to_messages,
)
from design_agent.kit.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
    overrides,
)
from design_agent.kit.tools import BaseTool, StructuredTool, ToolInvocationError, ToolRuntime
from design_agent.kit.types import Command, Runtime

_LOGGER = logging.getLogger(__name__)

DEFAULT_RECURSION_LIMIT = 9_999

Reducer = Callable[[Any, Any], Any]


class RemainingStepsManager:
    """Marks the `remaining_steps` state field the loop fills in."""


def _reducers(middleware: Sequence[AgentMiddleware[Any, Any, Any]]) -> dict[str, Reducer]:
    found: dict[str, Reducer] = {"messages": add_messages}
    for item in middleware:
        schema = item.state_schema
        if schema is None:
            continue
        try:
            hints = typing.get_type_hints(schema, include_extras=True)
        except Exception as error:  # noqa: BLE001 - an unresolvable annotation has no reducer to find
            _LOGGER.debug("state schema %s not inspected: %s", schema, type(error).__name__)
            continue
        for key, hint in hints.items():
            while typing.get_origin(hint) in (typing.NotRequired, typing.Required):
                hint = typing.get_args(hint)[0]
            if typing.get_origin(hint) is typing.Annotated:
                for meta in reversed(hint.__metadata__):
                    if callable(meta) and not isinstance(meta, type) and key != "messages":
                        found[key] = meta
                        break
    return found


@dataclass
class StateSnapshot:
    values: dict[str, Any]
    config: dict[str, Any]
    next: tuple[str, ...] = ()


def _as_response(result: Any) -> ModelResponse[Any]:
    if isinstance(result, ModelResponse):
        return result
    if isinstance(result, AIMessage):
        return ModelResponse(result=[result])
    inner = getattr(result, "model_response", None)
    if isinstance(inner, ModelResponse):
        return inner
    raise TypeError(f"a model wrap returned {type(result).__name__}, not a ModelResponse")


def _hook(middleware: AgentMiddleware[Any, Any, Any], name: str) -> Callable[..., Any] | None:
    for hook in (f"a{name}", name):
        if overrides(middleware, hook):
            found: Callable[..., Any] = getattr(middleware, hook)
            return found
    return None


async def _call(func: Callable[..., Any], *args: Any) -> Any:
    result = func(*args)
    if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
        result = await result
    return result


class _Run:
    """The state of one run in progress."""

    def __init__(
        self, agent: CompiledAgent, state: dict[str, Any], config: dict[str, Any], context: Any
    ) -> None:
        self.agent = agent
        self.state = state
        self.config = config
        self.limit = int(config.get("recursion_limit") or DEFAULT_RECURSION_LIMIT)
        self.steps = 0
        self.runtime = Runtime(
            context=context, store=agent.store, stream_writer=lambda *_a, **_k: None, config=config
        )

    def view(self) -> dict[str, Any]:
        return {**self.state, "remaining_steps": self.limit - self.steps}

    def step(self) -> None:
        self.steps += 1
        if self.steps > self.limit:
            raise GraphRecursionError(
                f"Recursion limit of {self.limit} reached without hitting a stop condition."
            )

    def apply(self, update: Mapping[str, Any] | None) -> str | None:
        """Merge an update into the state; returns its `jump_to`, if any."""
        if not update:
            return None
        jump: str | None = None
        for key, value in update.items():
            if key == "jump_to":
                jump = value
                continue
            if key == "remaining_steps":
                continue
            reducer = self.agent.reducers.get(key)
            if key == "messages":
                value = convert_to_messages(value if isinstance(value, list) else [value])
                self.state["messages"] = add_messages(self.state.get("messages", []), value)
            elif reducer is not None:
                self.state[key] = reducer(self.state.get(key), value)
            else:
                self.state[key] = value
        return jump


class CompiledAgent:
    """A runnable agent: `ainvoke`, `astream`, `aget_state`."""

    def __init__(
        self,
        *,
        model: Any,
        tools: Sequence[Any],
        system_prompt: str | SystemMessage | None,
        middleware: Sequence[AgentMiddleware[Any, Any, Any]],
        checkpointer: Any = None,
        store: Any = None,
        name: str | None = None,
    ) -> None:
        self.model = model
        self.middleware = list(middleware)
        names = [m.name for m in self.middleware]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"Please remove duplicate middleware instances: {duplicates}")
        registered: list[Any] = [*[t for m in self.middleware for t in (m.tools or [])], *tools]
        self.tools: list[Any] = []
        self.tools_by_name: dict[str, BaseTool] = {}
        for tool in registered:
            if callable(tool) and not isinstance(tool, (BaseTool, Mapping)):
                tool = StructuredTool.from_function(func=tool)
            if isinstance(tool, BaseTool):
                if tool.name in self.tools_by_name:
                    continue
                self.tools_by_name[tool.name] = tool
            self.tools.append(tool)
        if isinstance(system_prompt, str):
            self.system_message: SystemMessage | None = (
                SystemMessage(content=system_prompt) if system_prompt else None
            )
        else:
            self.system_message = system_prompt
        self.checkpointer = checkpointer
        self.store = store
        self.name = name or "agent"
        self.reducers = _reducers(self.middleware)

    # -- public interface ----------------------------------------------------------------------

    async def ainvoke(
        self,
        input: Mapping[str, Any] | Command[Any] | None,
        config: Mapping[str, Any] | None = None,
        *,
        context: Any = None,
        **_: Any,
    ) -> dict[str, Any]:
        state: dict[str, Any] = {}
        async for _node, _update, final in self._run(input, config, context):
            state = final
        return state

    async def astream(
        self,
        input: Mapping[str, Any] | None,
        config: Mapping[str, Any] | None = None,
        *,
        stream_mode: str = "updates",
        context: Any = None,
        **_: Any,
    ) -> AsyncIterator[Any]:
        async for node, update, _final in self._run(input, config, context):
            if update:
                yield {node: update}

    def invoke(
        self,
        input: Mapping[str, Any] | None,
        config: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return asyncio.run(self.ainvoke(input, config, **kwargs))

    async def aget_state(self, config: Mapping[str, Any]) -> StateSnapshot:
        thread_id = (config.get("configurable") or {}).get("thread_id")
        values = (
            await self.checkpointer.aget(str(thread_id))
            if self.checkpointer is not None and thread_id
            else None
        )
        return StateSnapshot(values=values or {}, config=dict(config))

    def with_config(self, config: Mapping[str, Any] | None = None, **kwargs: Any) -> CompiledAgent:
        return self

    # -- the loop ------------------------------------------------------------------------------

    def _config(self, config: Mapping[str, Any] | None) -> dict[str, Any]:
        parent = var_child_runnable_config.get() or {}
        out: dict[str, Any] = {k: v for k, v in parent.items() if k not in ("callbacks",)}
        out.update(dict(config or {}))
        out["configurable"] = {
            **(parent.get("configurable") or {}),
            **dict((config or {}).get("configurable") or {}),
        }
        metadata = dict(out.get("metadata") or {})
        metadata["run_id"] = str(uuid.uuid4())
        out["metadata"] = metadata
        return out

    async def _run(
        self, input_: Any, config: Mapping[str, Any] | None, context: Any
    ) -> AsyncIterator[tuple[str, dict[str, Any] | None, dict[str, Any]]]:
        run_config = self._config(config)
        thread_id = run_config["configurable"].get("thread_id")
        saved = None
        if self.checkpointer is not None and thread_id:
            saved = await self.checkpointer.aget(str(thread_id))
        run = _Run(self, dict(saved or {"messages": []}), run_config, context)
        run.state.setdefault("messages", [])
        update = input_.update if isinstance(input_, Command) else input_
        run.apply(dict(update or {}))
        token = var_child_runnable_config.set(run_config)
        try:
            async for item in self._loop(run):
                yield item
                await self._save(run, thread_id)
        finally:
            var_child_runnable_config.reset(token)
        await self._save(run, thread_id)
        yield "__end__", None, run.state

    async def _save(self, run: _Run, thread_id: Any) -> None:
        if self.checkpointer is not None and thread_id:
            await self.checkpointer.aput(str(thread_id), run.state)

    async def _hooks(
        self, run: _Run, name: str, *, reverse: bool = False
    ) -> AsyncIterator[tuple[str, dict[str, Any] | None, str | None]]:
        order = reversed(self.middleware) if reverse else self.middleware
        for middleware in order:
            hook = _hook(middleware, name)
            if hook is None:
                continue
            run.step()
            update = await _call(hook, run.view(), run.runtime)
            if isinstance(update, Command):
                update = dict(update.update or {})
            jump = run.apply(update)
            yield f"{middleware.name}.{name}", update, jump
            if jump == "end":
                return

    async def _loop(
        self, run: _Run
    ) -> AsyncIterator[tuple[str, dict[str, Any] | None, dict[str, Any]]]:
        ended = False
        async for node, update, jump in self._hooks(run, "before_agent"):
            yield node, update, run.state
            if jump == "end":
                ended = True
        while not ended:
            jumped = False
            async for node, update, jump in self._hooks(run, "before_model"):
                yield node, update, run.state
                if jump == "end":
                    ended = jumped = True
            if ended:
                break
            run.step()
            response = await self._model_step(run)
            update = {"messages": list(response.result)}
            run.apply(update)
            yield "model", update, run.state
            async for node, hook_update, jump in self._hooks(run, "after_model", reverse=True):
                yield node, hook_update, run.state
                if jump == "end":
                    ended = jumped = True
                elif jump == "model":
                    jumped = True
            if ended:
                break
            if jumped:
                continue
            last = next(
                (m for m in reversed(run.state["messages"]) if isinstance(m, AIMessage)), None
            )
            if last is None or not last.tool_calls:
                break
            answered = {m.tool_call_id for m in run.state["messages"] if isinstance(m, ToolMessage)}
            calls = [c for c in last.tool_calls if c.get("id") not in answered]
            if not calls:
                break
            run.step()
            tool_update, stop = await self._tool_step(run, calls)
            yield "tools", tool_update, run.state
            if stop:
                break
        async for node, update, _jump in self._hooks(run, "after_agent", reverse=True):
            yield node, update, run.state

    # -- model ---------------------------------------------------------------------------------

    async def _model_step(self, run: _Run) -> ModelResponse[Any]:
        request = ModelRequest(
            model=self.model,
            messages=list(run.state["messages"]),
            system_message=self.system_message,
            tools=list(self.tools),
            state=run.view(),
            runtime=run.runtime,
        )

        async def base(request: ModelRequest[Any]) -> ModelResponse[Any]:
            messages: list[BaseMessage] = list(request.messages)
            if request.system_message is not None:
                messages = [request.system_message, *messages]
            options = dict(request.model_settings or {})
            if request.tools:
                bound = request.model.bind_tools(
                    request.tools, tool_choice=request.tool_choice, **options
                )
            else:
                bound = request.model.bind(**options) if options else request.model
            reply = await bound.ainvoke(messages)
            if not isinstance(reply, AIMessage):
                reply = AIMessage(content=getattr(reply, "content", str(reply)))
            if reply.name is None:
                reply.name = self.name if self.name != "agent" else None
            return ModelResponse(result=[reply])

        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]] = base
        for middleware in reversed(self.middleware):
            wrap = _hook(middleware, "wrap_model_call")
            if wrap is None:
                continue
            handler = _wrap_model(wrap, handler)
        return _as_response(await handler(request))

    # -- tools ---------------------------------------------------------------------------------

    async def _tool_step(
        self, run: _Run, calls: Sequence[Mapping[str, Any]]
    ) -> tuple[dict[str, Any], bool]:
        results = await asyncio.gather(*(self._one_tool(run, call) for call in calls))
        update: dict[str, Any] = {"messages": []}
        stop = False
        for call, result in zip(calls, results, strict=True):
            # A tool's own answer carries the tool's name, however the tool built it.
            for message in (
                result.update.get("messages", [])
                if isinstance(result, Command) and result.update
                else [result]
            ):
                if (
                    isinstance(message, ToolMessage)
                    and message.name is None
                    and message.tool_call_id == call.get("id")
                ):
                    message.name = str(call.get("name"))
            if isinstance(result, Command):
                for key, value in dict(result.update or {}).items():
                    if key == "messages":
                        update["messages"].extend(value if isinstance(value, list) else [value])
                    else:
                        run.apply({key: value})
                        update[key] = value
                stop = stop or result.goto in ("end", "__end__")
            else:
                update["messages"].append(result)
            tool = self.tools_by_name.get(str(call.get("name")))
            stop = stop or bool(tool is not None and tool.return_direct)
        run.apply({"messages": update["messages"]})
        return update, stop

    async def _one_tool(self, run: _Run, call: Mapping[str, Any]) -> ToolMessage | Command[Any]:
        tool = self.tools_by_name.get(str(call.get("name")))
        runtime = ToolRuntime(
            state=run.view(),
            context=run.runtime.context,
            config=run.config,
            stream_writer=run.runtime.stream_writer,
            tool_call_id=str(call.get("id") or ""),
            store=self.store,
            tools=list(self.tools),
        )
        request = ToolCallRequest(
            tool_call=ToolCall(**call),  # type: ignore[typeddict-item]
            tool=tool,
            state=run.view(),
            runtime=runtime,
        )

        async def base(request: ToolCallRequest) -> ToolMessage | Command[Any]:
            call_ = request.tool_call
            name = str(call_.get("name"))
            tool_ = request.tool if request.tool is not None else self.tools_by_name.get(name)
            if tool_ is None:
                known = ", ".join(sorted(self.tools_by_name))
                return ToolMessage(
                    content=f"Error: {name} is not a valid tool, try one of [{known}].",
                    tool_call_id=str(call_.get("id") or ""),
                    name=name,
                    status="error",
                )
            try:
                result = await tool_.ainvoke(
                    {**call_, "type": "tool_call", "runtime": request.runtime}
                )
            except ToolInvocationError as error:
                return ToolMessage(
                    content=f"Error invoking tool '{name}' with kwargs {call_.get('args')} with error:\n {error}\n Please fix the error and try again.",
                    tool_call_id=str(call_.get("id") or ""),
                    name=name,
                    status="error",
                )
            if not isinstance(result, ToolMessage | Command):
                result = tool_.to_message(result, call_)
            return result  # type: ignore[no-any-return]

        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]] = base
        for middleware in reversed(self.middleware):
            wrap = _hook(middleware, "wrap_tool_call")
            if wrap is None:
                continue
            handler = _wrap_tool(wrap, handler)
        return await handler(request)


def _wrap_model(
    wrap: Callable[..., Any], inner: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]]
) -> Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]]:
    async def handler(request: ModelRequest[Any]) -> ModelResponse[Any]:
        async def next_(req: ModelRequest[Any]) -> ModelResponse[Any]:
            return _as_response(await inner(req))

        return _as_response(await _call(wrap, request, next_))

    return handler


def _wrap_tool(
    wrap: Callable[..., Any], inner: Callable[[ToolCallRequest], Awaitable[Any]]
) -> Callable[[ToolCallRequest], Awaitable[Any]]:
    async def handler(request: ToolCallRequest) -> Any:
        return await _call(wrap, request, inner)

    return handler


def create_agent(
    model: Any,
    tools: Sequence[Any] | None = None,
    *,
    system_prompt: str | SystemMessage | None = None,
    middleware: Sequence[AgentMiddleware[Any, Any, Any]] = (),
    checkpointer: Any = None,
    store: Any = None,
    name: str | None = None,
    **_: Any,
) -> CompiledAgent:
    if isinstance(model, str):
        from design_agent.kit.models import init_chat_model

        model = init_chat_model(model)
    return CompiledAgent(
        model=model,
        tools=list(tools or []),
        system_prompt=system_prompt,
        middleware=middleware,
        checkpointer=checkpointer,
        store=store,
        name=name,
    )
