"""The agent loop's contract: hook order, jumps, reducers, tool results, persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, NotRequired

from design_agent.kit.agent import create_agent
from design_agent.kit.checkpoint import AsyncSqliteSaver
from design_agent.kit.messages import AIMessage, HumanMessage, ToolMessage
from design_agent.kit.middleware import AgentMiddleware, AgentState, hook_config
from design_agent.kit.store import AsyncSqliteStore
from design_agent.kit.tools import ToolRuntime, tool
from design_agent.kit.types import Command
from design_agent.testing.scripted_model import ScriptedChatModel, reply, tool_call


def _union(left: list[str] | None, right: list[str]) -> list[str]:
    return sorted({*(left or []), *right})


class _Seen(AgentState[Any]):
    seen: NotRequired[Annotated[list[str], _union]]


class Recorder(AgentMiddleware[_Seen, Any, Any]):
    state_schema = _Seen

    def __init__(self, name: str, log: list[str]) -> None:
        super().__init__()
        self._name, self._log = name, log

    @property
    def name(self) -> str:
        return self._name

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any]:
        self._log.append(f"{self._name}.before_agent")
        return {"seen": [self._name]}

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        self._log.append(f"{self._name}.wrap>")
        response = await handler(request)
        self._log.append(f"<{self._name}.wrap")
        return response

    async def aafter_agent(self, state: Any, runtime: Any) -> None:
        self._log.append(f"{self._name}.after_agent")


@tool
async def remember(note: str, runtime: ToolRuntime) -> Command[Any]:
    """Remember a note."""
    return Command(
        update={
            "seen": [note],
            "messages": [ToolMessage("noted", tool_call_id=str(runtime.tool_call_id))],
        }
    )


async def test_hooks_nest_in_list_order_and_state_merges_through_reducers() -> None:
    log: list[str] = []
    model = ScriptedChatModel(
        steps=[lambda _m: tool_call("remember", {"note": "blue"}), lambda _m: reply("ok")]
    )
    agent = create_agent(
        model, [remember], middleware=[Recorder("outer", log), Recorder("inner", log)]
    )
    state = await agent.ainvoke({"messages": [HumanMessage("hi")]})
    assert log[:4] == ["outer.before_agent", "inner.before_agent", "outer.wrap>", "inner.wrap>"]
    assert log[4:6] == ["<inner.wrap", "<outer.wrap"]
    assert log[-2:] == ["inner.after_agent", "outer.after_agent"], "after-hooks run in reverse"
    assert state["seen"] == ["blue", "inner", "outer"], "the reducer merged every update"
    tool_reply = next(m for m in state["messages"] if isinstance(m, ToolMessage))
    assert (tool_reply.content, tool_reply.name) == ("noted", "remember")
    assert state["messages"][-1].content == "ok"


class Gate(AgentMiddleware[AgentState[Any], Any, Any]):
    @hook_config(can_jump_to=["end"])
    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any]:
        return {"messages": [AIMessage(content="closed")], "jump_to": "end"}


async def test_a_jump_to_end_skips_the_model_but_not_the_after_hooks() -> None:
    log: list[str] = []
    model = ScriptedChatModel(steps=[])
    agent = create_agent(model, middleware=[Recorder("r", log), Gate()])
    state = await agent.ainvoke({"messages": [HumanMessage("hi")]})
    assert model.call_count == 0
    assert state["messages"][-1].content == "closed"
    assert log == ["r.before_agent", "r.after_agent"]


async def test_an_unknown_tool_and_bad_arguments_come_back_as_errors() -> None:
    model = ScriptedChatModel(
        steps=[
            lambda _m: tool_call("nope", {}),
            lambda _m: tool_call("remember", {"wrong": 1}),
            lambda _m: reply("done"),
        ]
    )
    state = await create_agent(model, [remember]).ainvoke({"messages": [HumanMessage("hi")]})
    errors = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert [m.status for m in errors] == ["error", "error"]
    assert "not a valid tool" in str(errors[0].content) and "note" in str(errors[1].content)


async def test_a_thread_and_the_store_survive_a_restart(tmp_path: Path) -> None:
    db = str(tmp_path / "threads.sqlite")
    config = {"configurable": {"thread_id": "t1"}}
    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        agent = create_agent(
            ScriptedChatModel(steps=[lambda _m: reply("first")]), checkpointer=saver
        )
        await agent.ainvoke({"messages": [HumanMessage("one")]}, config)
    async with AsyncSqliteSaver.from_conn_string(db) as saver:
        agent = create_agent(
            ScriptedChatModel(steps=[lambda m: reply(f"saw {len(m)}")]), checkpointer=saver
        )
        state = await agent.ainvoke({"messages": [HumanMessage("two")]}, config)
    assert [m.text for m in state["messages"]] == ["one", "first", "two", "saw 3"]

    store_path = str(tmp_path / "store.sqlite")
    async with AsyncSqliteStore.from_conn_string(store_path) as store:
        await store.aput(("ledger", "t1"), "a", {"status": "running"})
        await store.aput(("ledger", "t2"), "b", {"status": "done"}, ttl=60)
        await store.aput(("other",), "c", {"status": "running"})
    async with AsyncSqliteStore.from_conn_string(store_path) as store:
        assert {i.key for i in await store.asearch(("ledger",), limit=10)} == {"a", "b"}
        running = await store.asearch(("ledger",), filter={"status": "running"})
        assert [i.key for i in running] == ["a"]
        await store.adelete(("ledger", "t1"), "a")
        assert await store.aget(("ledger", "t1"), "a") is None
