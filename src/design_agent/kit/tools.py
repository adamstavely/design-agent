"""Tools: a name, a description, a JSON schema for the arguments, and a function to run.

`StructuredTool.from_function` (and `@tool`) reads the schema from the function's signature and the
description from its docstring. A parameter annotated `ToolRuntime` (or named `runtime`) is not
part of the schema: the agent loop passes the run's state, config, store and the call's id there.

A tool returns a string, content blocks, a `ToolMessage`, or a `Command` whose update carries the
`ToolMessage` answering the call plus any state changes.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import textwrap
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, get_type_hints, overload

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model
from typing_extensions import TypeVar

from design_agent.kit.messages import ToolCall, ToolMessage

ContextT = TypeVar("ContextT", default=Any)
StateT = TypeVar("StateT", default=Any)


@dataclass
class ToolRuntime(Generic[ContextT, StateT]):
    """What a tool sees of the run calling it."""

    state: Any = None
    context: Any = None
    config: dict[str, Any] = field(default_factory=dict)
    stream_writer: Callable[..., Any] = lambda *_args, **_kwargs: None
    tool_call_id: str | None = None
    store: Any = None
    tools: list[Any] = field(default_factory=list)


class ToolException(Exception):
    """Raised by a tool to return its message to the model as an error result."""


class ToolInvocationError(ValueError):
    """The model's arguments did not match the tool's schema."""


def _strip_titles(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {
            k: _strip_titles(v)
            for k, v in schema.items()
            if not (k == "title" and isinstance(v, str))
        }
    if isinstance(schema, list):
        return [_strip_titles(v) for v in schema]
    return schema


def _is_runtime_param(name: str, annotation: Any) -> bool:
    if annotation is ToolRuntime or name == "runtime":
        return True
    return isinstance(annotation, str) and annotation.split("[")[0].endswith("ToolRuntime")


def _schema_model(func: Callable[..., Any], name: str) -> tuple[type[BaseModel], str | None]:
    """A pydantic model of `func`'s arguments, and the parameter that takes the runtime."""
    signature = inspect.signature(func)
    try:
        hints = get_type_hints(func, include_extras=True)
    except Exception:  # noqa: BLE001 - unresolvable forward refs fall back to raw annotations
        hints = {}
    fields: dict[str, Any] = {}
    runtime_param: str | None = None
    for param in signature.parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        annotation = hints.get(param.name, param.annotation)
        if _is_runtime_param(param.name, annotation):
            runtime_param = param.name
            continue
        if annotation is inspect.Parameter.empty:
            annotation = Any
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[param.name] = (annotation, default)
    model = create_model(name, __config__=ConfigDict(arbitrary_types_allowed=True), **fields)
    return model, runtime_param


class BaseTool(BaseModel):
    """A tool the model can call."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str
    description: str = ""
    args_schema: Any = None
    return_direct: bool = False
    response_format: Literal["content", "content_and_artifact"] = "content"
    metadata: dict[str, Any] | None = None
    extras: dict[str, Any] | None = None
    tags: list[str] | None = None

    # -- schema --------------------------------------------------------------------------------

    def input_schema(self) -> dict[str, Any]:
        schema = self.args_schema
        if schema is None:
            return {"type": "object", "properties": {}}
        if isinstance(schema, dict):
            return dict(schema)
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            stripped: dict[str, Any] = _strip_titles(schema.model_json_schema())
            return stripped
        raise TypeError(f"tool {self.name}: unsupported args_schema {type(schema).__name__}")

    @property
    def args(self) -> dict[str, Any]:
        return dict(self.input_schema().get("properties") or {})

    def tool_schema(self) -> dict[str, Any]:
        """The Anthropic tool definition."""
        out: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema(),
        }
        if self.extras:
            out.update(self.extras)
        return out

    # -- running -------------------------------------------------------------------------------

    def _validate(self, args: Mapping[str, Any]) -> dict[str, Any]:
        schema = self.args_schema
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            try:
                parsed = schema.model_validate(dict(args))
            except ValidationError as error:
                raise ToolInvocationError(str(error)) from error
            return {k: getattr(parsed, k) for k in type(parsed).model_fields}
        return dict(args)

    async def _arun(self, args: dict[str, Any], runtime: ToolRuntime | None) -> Any:
        raise NotImplementedError

    def _run(self, args: dict[str, Any], runtime: ToolRuntime | None) -> Any:
        return asyncio.run(self._arun(args, runtime))

    async def ainvoke(self, input: Any, config: Any = None, **_: Any) -> Any:
        """Run with a dict of arguments, or with a tool call (then the result is a message)."""
        call: ToolCall | None = None
        runtime: ToolRuntime | None = None
        if isinstance(input, Mapping) and input.get("type") == "tool_call":
            call = input  # type: ignore[assignment]
            args = dict(input.get("args") or {})
            runtime = input.get("runtime")
        else:
            args = dict(input or {})
            runtime = args.pop("runtime", None)
        result = await self._arun(self._validate(args), runtime)
        if call is None:
            return result[0] if self.response_format == "content_and_artifact" else result
        return self.to_message(result, call)

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return asyncio.run(self.ainvoke(input, config, **kwargs))

    def to_message(self, result: Any, call: Mapping[str, Any]) -> Any:
        """A tool's return value as the message answering `call` (a `Command` passes through)."""
        from design_agent.kit.types import Command

        if isinstance(result, (ToolMessage, Command)):
            return result
        artifact = None
        if self.response_format == "content_and_artifact" and isinstance(result, tuple):
            result, artifact = result
        return ToolMessage(
            content=content_of(result),
            tool_call_id=str(call.get("id") or ""),
            name=self.name,
            artifact=artifact,
        )


def content_of(result: Any) -> str | list[Any]:
    if isinstance(result, str):
        return result
    if isinstance(result, list) and all(isinstance(b, dict) and "type" in b for b in result):
        return result
    if result is None:
        return ""
    try:
        return json.dumps(result, default=str, ensure_ascii=False)
    except TypeError:
        return str(result)


class StructuredTool(BaseTool):
    func: Callable[..., Any] | None = None
    coroutine: Callable[..., Awaitable[Any]] | None = None
    runtime_param: str | None = None

    @classmethod
    def from_function(
        cls,
        func: Callable[..., Any] | None = None,
        coroutine: Callable[..., Awaitable[Any]] | None = None,
        name: str | None = None,
        description: str | None = None,
        args_schema: Any = None,
        **kwargs: Any,
    ) -> StructuredTool:
        source = coroutine or func
        if source is None:
            raise ValueError("from_function needs a func or a coroutine")
        tool_name = name or source.__name__
        model, runtime_param = _schema_model(source, tool_name)
        doc = (
            description
            if description is not None
            else textwrap.dedent(source.__doc__ or "").strip()
        )
        return cls(
            name=tool_name,
            description=doc,
            args_schema=args_schema if args_schema is not None else model,
            func=func,
            coroutine=coroutine,
            runtime_param=runtime_param,
            **kwargs,
        )

    def model_post_init(self, _context: Any) -> None:
        if self.runtime_param is None:
            source = self.coroutine or self.func
            if source is not None:
                params = inspect.signature(source).parameters
                if "runtime" in params:
                    self.runtime_param = "runtime"

    def _call_args(self, args: dict[str, Any], runtime: ToolRuntime | None) -> dict[str, Any]:
        source = self.coroutine or self.func
        params = inspect.signature(source).parameters if source is not None else {}
        accepts_kwargs = any(p.kind is p.VAR_KEYWORD for p in params.values())
        call = {k: v for k, v in args.items() if k in params or accepts_kwargs}
        if self.runtime_param is not None and self.runtime_param in params:
            call[self.runtime_param] = runtime if runtime is not None else ToolRuntime()
        return call

    async def _arun(self, args: dict[str, Any], runtime: ToolRuntime | None) -> Any:
        call = self._call_args(args, runtime)
        if self.coroutine is not None:
            return await self.coroutine(**call)
        if self.func is None:
            raise NotImplementedError(f"tool {self.name} has no function")
        return await asyncio.to_thread(functools.partial(self.func, **call))


@overload
def tool(name_or_func: Callable[..., Any]) -> StructuredTool: ...


@overload
def tool(
    name_or_func: str | None = None,
    *,
    description: str | None = None,
    args_schema: Any = None,
    response_format: Literal["content", "content_and_artifact"] = "content",
    **kwargs: Any,
) -> Callable[[Callable[..., Any]], StructuredTool]: ...


def tool(
    name_or_func: Any = None,
    *,
    description: str | None = None,
    args_schema: Any = None,
    response_format: Literal["content", "content_and_artifact"] = "content",
    **kwargs: Any,
) -> Any:
    """`@tool`, `@tool("name")` or `@tool(description=...)` over a function or coroutine."""

    def build(func: Callable[..., Any], name: str | None) -> StructuredTool:
        is_async = inspect.iscoroutinefunction(func)
        return StructuredTool.from_function(
            func=None if is_async else func,
            coroutine=func if is_async else None,
            name=name or func.__name__,
            description=description,
            args_schema=args_schema,
            response_format=response_format,
            **kwargs,
        )

    if callable(name_or_func) and not isinstance(name_or_func, str):
        return build(name_or_func, None)

    def decorator(func: Callable[..., Any]) -> StructuredTool:
        return build(func, name_or_func if isinstance(name_or_func, str) else None)

    return decorator


def convert_to_openai_tool(tool_: Any) -> dict[str, Any]:
    """The OpenAI function-tool definition of a tool, a callable or an Anthropic schema dict."""
    if isinstance(tool_, Mapping):
        if tool_.get("type") == "function":
            return dict(tool_)
        return {
            "type": "function",
            "function": {
                "name": tool_.get("name"),
                "description": tool_.get("description", ""),
                "parameters": tool_.get("input_schema") or tool_.get("parameters") or {},
            },
        }
    if not isinstance(tool_, BaseTool):
        tool_ = StructuredTool.from_function(func=tool_)
    return {
        "type": "function",
        "function": {
            "name": tool_.name,
            "description": tool_.description,
            "parameters": tool_.input_schema(),
        },
    }


def to_anthropic_tool(tool_: Any) -> dict[str, Any]:
    if isinstance(tool_, BaseTool):
        return tool_.tool_schema()
    if isinstance(tool_, Mapping):
        if tool_.get("type") == "function" and "function" in tool_:
            fn = tool_["function"]
            return {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        return dict(tool_)
    return StructuredTool.from_function(func=tool_).tool_schema()


__all__ = [
    "BaseTool",
    "Field",
    "StructuredTool",
    "ToolException",
    "ToolInvocationError",
    "ToolRuntime",
    "content_of",
    "convert_to_openai_tool",
    "to_anthropic_tool",
    "tool",
]
