"""Errors the agent loop and the model adapters raise."""

from __future__ import annotations


class GraphBubbleUp(Exception):
    """Raised to stop a run from inside it; error boundaries let it pass."""


class GraphInterrupt(GraphBubbleUp):
    """A run paused for a person's input."""


class GraphRecursionError(RecursionError):
    """A run reached its step limit."""


class ModelInvalidRequestError(ValueError):
    """The provider rejected the request as malformed (HTTP 400)."""


class ContextOverflowError(ModelInvalidRequestError):
    """The request is longer than the model's context window."""
