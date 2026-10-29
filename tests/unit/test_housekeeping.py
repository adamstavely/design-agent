"""The housekeeping run sweeps and ends before any model call."""

from __future__ import annotations

from types import SimpleNamespace

from design_agent.kit.messages import HumanMessage
from design_agent.middleware.housekeeping import MARK, HousekeepingMiddleware


async def test_the_housekeeping_run_ends_in_code() -> None:
    swept: list[object] = []

    class Services:
        async def sweep(self, store: object) -> int:
            swept.append(store)
            return 2

    middleware = HousekeepingMiddleware(Services())  # type: ignore[arg-type]
    runtime = SimpleNamespace(store=object())
    update = await middleware.abefore_agent({"messages": [HumanMessage(MARK)]}, runtime)  # type: ignore[arg-type]
    assert update is not None and update["jump_to"] == "end" and swept
    assert "2 background item(s) swept" in update["messages"][0].content
    assert (
        await middleware.abefore_agent({"messages": [HumanMessage("Make a card.")]}, runtime)
        is None
    )  # type: ignore[arg-type]
