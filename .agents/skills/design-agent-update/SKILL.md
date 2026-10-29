---
name: design-agent-update
description: Bring a fork or clone of Design Agent up to date with this repository while keeping the team's brand, skills and edits, rebuild what changed, and restart the self-hosted services safely. Use when someone asks to update, upgrade or pull the latest Design Agent.
---

# Update from upstream

The brand pack (`workspace/brand/`), licensed fonts (`private-fonts/`) and `.env` are Git-ignored,
so an update never touches them. The team's own skills and instruction edits can conflict.

1. `git status`. Commit the person's local work first, or ask; never use a bare `git stash`.
2. Add the remote once: `git remote add upstream https://github.com/adamstavely/design-agent.git`.
   Then `git fetch upstream` and read the new entries in `CHANGELOG.md` for breaking changes. (The
   original project at `langchain-ai/design-agent` runs on a different runtime and deploy path; do
   not merge it into this one.)
3. Merge `upstream/main` into the person's branch. In conflicts, keep their skills and instruction
   edits and take upstream's code; ask when a conflict touches both.
4. `uv sync`, `uv run design-agent doctor`, `uv run pytest -q`, `uv run design-agent skills verify`.
5. If `sandbox/setup.sh` changed: `uv run design-agent sandbox build && uv run design-agent sandbox test`.
6. Try the update locally (`uv run design-agent demo`, then one real `uv run design-agent ask`),
   then restart the team's services after a yes:
   `docker compose -f deploy/self-host/compose.yaml up -d --build`
   ([design-agent-deploy](../design-agent-deploy/SKILL.md)).
