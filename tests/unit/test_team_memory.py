"""Team memory: `/memories/agent/` is a host folder the agent writes like any other, and its
`AGENTS.md` is in the lead's prompt from the next turn on."""

from __future__ import annotations

from pathlib import Path

from design_agent.config import Settings
from design_agent.kit.checkpoint import InMemorySaver
from design_agent.kit.deep import create_deep_agent
from design_agent.kit.deep.backends import StateBackend
from design_agent.kit.messages import BaseMessage, HumanMessage, SystemMessage
from design_agent.middleware.team_memory import MAX_INDEX_CHARS, TeamMemoryMiddleware
from design_agent.runtime.sandbox import _with_skills
from design_agent.testing.scripted_model import ScriptedChatModel, reply, tool_call

ROOT = Path(__file__).resolve().parents[2]


def system_text(messages: list[BaseMessage]) -> str:
    return next(m.text for m in messages if isinstance(m, SystemMessage))


def test_memory_is_on_by_default_under_the_workspace_and_can_be_turned_off(
    tmp_path: Path,
) -> None:
    def settings(**kw: object) -> Settings:
        return Settings(_env_file=None, **kw)  # type: ignore[call-arg,arg-type]

    assert settings().resolved_memory_path(tmp_path) == tmp_path / "workspace/memories/agent"
    assert settings(enable_memory=False).resolved_memory_path(tmp_path) is None
    elsewhere = tmp_path / "shared"
    assert settings(memory_path=elsewhere).resolved_memory_path(tmp_path) == elsewhere / "agent"


def test_the_route_writes_to_the_host_folder_and_shows_in_listings(tmp_path: Path) -> None:
    folder = tmp_path / "memories" / "agent"
    backend = _with_skills(StateBackend(), ROOT, folder)

    assert backend.write("/memories/agent/AGENTS.md", "- Captions in sentence case\n").path
    assert (folder / "AGENTS.md").read_text() == "- Captions in sentence case\n"
    assert backend.write("/workspace/notes.md", "scratch").path
    assert not (folder / "notes.md").exists(), "only the route lands on the host"

    top = {e["path"] for e in backend.ls("/").entries or []}
    assert {"/memories/", "/skills/", "/workspace/"} <= top
    assert [e["path"] for e in backend.ls("/memories").entries or []] == ["/memories/agent/"]
    assert [e["path"] for e in backend.ls("/memories/agent").entries or []] == [
        "/memories/agent/AGENTS.md"
    ]


async def test_a_saved_rule_reaches_the_prompt_on_the_next_turn(tmp_path: Path) -> None:
    folder = tmp_path / "memories" / "agent"
    prompts: list[str] = []

    def seen(text: str):  # type: ignore[no-untyped-def]
        def step(messages: list[BaseMessage]):  # type: ignore[no-untyped-def]
            prompts.append(system_text(messages))
            return reply(text)

        return step

    def save(messages: list[BaseMessage]):  # type: ignore[no-untyped-def]
        prompts.append(system_text(messages))
        return tool_call(
            "write_file",
            {"file_path": "/memories/agent/AGENTS.md", "content": "- Never use drop shadows"},
        )

    model = ScriptedChatModel(steps=[save, seen("Saved."), seen("No shadows, then.")])
    agent = create_deep_agent(
        model,
        system_prompt="You design.",
        middleware=[TeamMemoryMiddleware(folder)],
        backend=_with_skills(StateBackend(), ROOT, folder),
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "t1"}}
    await agent.ainvoke({"messages": [HumanMessage("Never use drop shadows.")]}, config)
    await agent.ainvoke({"messages": [HumanMessage("Make a card.")]}, config)

    assert (folder / "AGENTS.md").read_text() == "- Never use drop shadows"
    assert "Empty: nothing saved yet" in prompts[0]
    assert prompts[1] == prompts[0], "the prompt holds for the whole turn (cache-stable)"
    assert "- Never use drop shadows" in prompts[2]
    assert prompts[2].startswith("You design.")


def test_an_overgrown_index_is_cut_with_a_note(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("x" * (MAX_INDEX_CHARS + 500))
    index = TeamMemoryMiddleware(tmp_path).read_index()
    assert index.startswith("x" * MAX_INDEX_CHARS)
    assert "keep it a short index" in index
    assert TeamMemoryMiddleware(tmp_path / "missing").read_index() == ""
