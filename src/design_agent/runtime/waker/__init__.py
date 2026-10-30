"""Wakers: how a runtime starts a new lead turn on a thread (`harness.protocols.Waker`).

`local` is an in-process run queue, for the CLI, the demo, the self-hosted API and Slack app.
"""

from __future__ import annotations

import re

_WAKE = re.compile(r'<wake key="([A-Za-z0-9:_.-]{1,120})">')


def wake_marker(key: str) -> str:
    """The line a wake-up's message ends with, so the woken run can confirm it started."""
    return f'<wake key="{key}">'


def wake_key(text: str) -> str | None:
    found = _WAKE.search(text or "")
    return found.group(1) if found else None
