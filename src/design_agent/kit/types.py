"""What tools and hooks return to steer a run, and what hooks see of it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Generic

from typing_extensions import TypeVar

T = TypeVar("T", default=Any)


@dataclass
class Command(Generic[T]):
    """A tool's (or hook's) result as a state update, optionally jumping (`goto="end"`)."""

    update: Mapping[str, Any] | None = None
    goto: str | None = None
    resume: Any = None
    graph: str | None = None

    PARENT = "__parent__"


@dataclass
class Runtime(Generic[T]):
    """A hook's view of the run: its context, store, and a stream writer."""

    context: T | None = None
    store: Any = None
    stream_writer: Any = None
    previous: Any = None
    config: dict[str, Any] = field(default_factory=dict)
