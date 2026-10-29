"""The housekeeping run: sweep, then end in code before any model call.

A run whose whole prompt is `<housekeeping>` (the self-hosted process sends one on a timer) is
recognized here, first in the list, in `before_agent`. It sweeps the ledger
(`BackgroundServices.sweep`): helpers, watches and pipelines a restart killed are published as
interrupted, and finished work whose notice a restart swallowed is published again, so each
thread's lead hears of it on its next turn. Then it ends the run with a one-line summary that no
channel receives. A person who types the marker
gets the same: a sweep and no model call, which is harmless.
"""

from __future__ import annotations

import logging
from typing import Any

from design_agent.kit.messages import AIMessage
from design_agent.kit.middleware import AgentMiddleware, AgentState, hook_config
from design_agent.kit.types import Runtime
from design_agent.middleware.background import BackgroundServices
from design_agent.middleware.quiet import latest_human

_LOGGER = logging.getLogger(__name__)

MARK = "<housekeeping>"


class HousekeepingMiddleware(AgentMiddleware[AgentState[Any], Any, Any]):
    """Recognize the housekeeping run and do it in code (module doc)."""

    def __init__(self, services: BackgroundServices | None) -> None:
        super().__init__()
        self._services = services

    @property
    def name(self) -> str:
        return "DesignAgentHousekeeping"

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(
        self, state: AgentState[Any], runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        if latest_human(list(state.get("messages") or [])).strip() != MARK:
            return None
        swept = 0
        store = getattr(runtime, "store", None)
        if self._services is not None and store is not None:
            try:
                swept = await self._services.sweep(store)
            except Exception as error:  # noqa: BLE001 - the next pass tries again
                _LOGGER.warning("[housekeeping] the sweep failed: %s", type(error).__name__)
        summary = f"Housekeeping: {swept} background item(s) swept."
        return {"messages": [AIMessage(content=summary)], "jump_to": "end"}
