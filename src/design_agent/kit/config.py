"""The run config in scope: `{"configurable": {"thread_id": ...}, ...}`.

The agent loop sets it around a run, so code anywhere under the run (a tool, a backend, a
middleware) reads the thread with `get_config()`. Background work sets its own copy.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

RunnableConfig = dict[str, Any]

var_child_runnable_config: ContextVar[RunnableConfig | None] = ContextVar(
    "design_agent_run_config", default=None
)


def get_config() -> RunnableConfig:
    """The config of the run in progress. Raises `RuntimeError` outside one."""
    config = var_child_runnable_config.get()
    if config is None:
        raise RuntimeError("Called get_config outside of a run")
    return config


def ensure_config(config: RunnableConfig | None = None) -> RunnableConfig:
    base = dict(var_child_runnable_config.get() or {})
    base.update(config or {})
    base.setdefault("configurable", {})
    return base
