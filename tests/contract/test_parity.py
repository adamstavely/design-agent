"""Parity (SPEC §15, §23): every runtime assembles the same agent.

The CLI, the self-hosted API and the self-hosted Slack app differ only in what each surface
supplies: where files are delivered and, in Slack, live progress, the footer and steering. The
prompt, skills, tools, the rest of the middleware order and helpers are the same object graph.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from design_agent.assembly import AgentComponents, build_agent_components
from design_agent.config import Settings
from design_agent.middleware.background import BackgroundServices
from design_agent.runtime.local import local_profile
from design_agent.runtime.waker.local import InProcessWaker
from design_agent.surfaces.slack import BoltSlackResolver, SlackSurface
from design_agent.testing.fakes import folder_binding
from design_agent.testing.scripted_model import ScriptedChatModel

ROOT = Path(__file__).resolve().parents[2]


def shape(components: AgentComponents) -> dict[str, Any]:
    return {
        "prompt": components.system_prompt,
        "skills": components.skills,
        "tools": sorted(t.name for t in components.tools if t.name != "upload_files"),
        "middleware": [type(m).__name__ for m in components.middleware],
        "helpers": [s["name"] for s in components.subagents],
        "helper_middleware": [
            [type(m).__name__ for m in s.get("middleware", [])] for s in components.subagents
        ],
        "brand": components.brand.brand.name,
        "manifest": components.manifest.digest,
    }


SLACK_ONLY = {
    "LeadSteeringMiddleware",
    "ThreadProgressMiddleware",
    "ReplyFooterMiddleware",
    "HelperProgressMiddleware",
}


def test_cli_api_and_slack_assemble_the_same_agent(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, workspace_root=tmp_path / "ws")  # type: ignore[call-arg]
    model = ScriptedChatModel(steps=[])
    roles = {"helper": ScriptedChatModel(steps=[]), "look": ScriptedChatModel(steps=[])}

    async def run_turn(*_: Any) -> None:
        return None

    waker = InProcessWaker(run_turn)
    services = BackgroundServices(waker=waker, capture_target=waker.capture)
    binding = folder_binding(tmp_path / "sb", ROOT)

    def build(surface: str, slack: Any = None) -> AgentComponents:
        profile = local_profile(
            ROOT, binding, tmp_path / "out", services, slack=slack, surface=surface
        )
        return build_agent_components(
            settings=settings, runtime=profile, model=model, role_models=roles
        )

    cli, api = build("cli"), build("api")
    slack = build("slack", SlackSurface(BoltSlackResolver("xoxb-test")))
    assert shape(cli) == shape(api)
    slack_shape = shape(slack)
    assert SLACK_ONLY & set(slack_shape["middleware"]), "Slack adds its presentation middleware"
    slack_shape["middleware"] = [m for m in slack_shape["middleware"] if m not in SLACK_ONLY]
    slack_shape["helper_middleware"] = [
        [m for m in ms if m not in SLACK_ONLY] for ms in slack_shape["helper_middleware"]
    ]
    assert slack_shape == shape(cli)
    assert all("upload_files" in {t.name for t in c.tools} for c in (cli, api, slack))
