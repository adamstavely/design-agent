"""Skills: folders with a `SKILL.md`, listed by name and description in the system prompt.

At the start of a run the skill sources (`/skills/`) are scanned through the backend and each
`SKILL.md` front matter (`name`, `description`) goes into the state as `skills_metadata`. Every
model call renders that list into the system prompt; the agent reads a skill's full instructions
with `read_file` when a job needs it. A later source overrides an earlier one by name.
"""

from __future__ import annotations

import posixpath
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, NotRequired, TypedDict

import yaml

from design_agent.kit.deep.backends import BackendProtocol
from design_agent.kit.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
    append_system,
)


class SkillMetadata(TypedDict):
    name: str
    description: str
    path: str
    allowed_tools: NotRequired[list[str]]


class SkillsState(AgentState[Any]):
    skills_metadata: NotRequired[list[SkillMetadata]]


SKILLS_PROMPT = """## Skills

Skills are workflows and reference material for particular jobs. Their full instructions load on demand: when a request matches a skill below, read its SKILL.md with `read_file` (pass `limit=1000`, skills run long) and follow it. Paths in a skill are absolute.

{skills}"""


def parse_front_matter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


class SkillsMiddleware(AgentMiddleware[SkillsState, Any, Any]):
    state_schema = SkillsState

    def __init__(self, *, backend: BackendProtocol, sources: Sequence[str]) -> None:
        super().__init__()
        self.backend = backend
        self.sources = list(sources)

    async def _load(self) -> list[SkillMetadata]:
        found: dict[str, SkillMetadata] = {}
        for source in self.sources:
            listing = await self.backend.als(source)
            folders = [e["path"].rstrip("/") for e in listing.entries or [] if e.get("is_dir")]
            if not folders:
                continue
            paths = [posixpath.join(folder, "SKILL.md") for folder in folders]
            for item in await self.backend.adownload_files(paths):
                if item.content is None:
                    continue
                meta = parse_front_matter(item.content.decode("utf-8", errors="replace"))
                name = str(meta.get("name") or posixpath.basename(posixpath.dirname(item.path)))
                skill: SkillMetadata = {
                    "name": name,
                    "description": " ".join(str(meta.get("description") or "").split()),
                    "path": item.path,
                }
                tools = meta.get("allowed-tools") or meta.get("allowed_tools")
                if isinstance(tools, list):
                    skill["allowed_tools"] = [str(t) for t in tools]
                found[name] = skill
        return sorted(found.values(), key=lambda s: s["name"])

    async def abefore_agent(self, state: SkillsState, runtime: Any) -> dict[str, Any] | None:
        if state.get("skills_metadata"):
            return None
        return {"skills_metadata": await self._load()}

    def render(self, skills: Sequence[SkillMetadata]) -> str:
        if not skills:
            return SKILLS_PROMPT.format(
                skills=f"(No skills yet; add them under {' or '.join(self.sources)}.)"
            )
        lines = []
        for skill in skills:
            lines.append(f"- **{skill['name']}**: {skill['description']}")
            lines.append(f"  -> `{skill['path']}`")
        return SKILLS_PROMPT.format(skills="\n".join(lines))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        state = request.state if isinstance(request.state, dict) else {}
        return await handler(
            append_system(request, self.render(state.get("skills_metadata") or []))
        )


__all__ = [
    "PrivateStateAttr",
    "SkillMetadata",
    "SkillsMiddleware",
    "SkillsState",
    "parse_front_matter",
]
