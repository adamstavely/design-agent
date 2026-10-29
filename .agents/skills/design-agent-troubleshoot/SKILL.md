---
name: design-agent-troubleshoot
description: Diagnose Design Agent when something fails. Setup, a render, a deploy, Slack or a model call. Run doctor and the connection tests, read logs and thread transcripts, match the symptom to its fix, and never print credentials. Use when a command errors, a reply never comes, or a file looks wrong.
---

# Troubleshoot Design Agent

Report missing settings by name and never paste a key, token or `.env` line.

1. `uv run design-agent doctor` (add `--json` to parse it) checks every model role, keys, the brand,
   the sandbox, the vendored skills, MCP and Paper. Fix what it names first.
2. Real calls, a few cents each: `uv run design-agent test model`, `test sandbox`, `test paper`,
   `test slack`, `test db`.
3. Match the symptom:

| Symptom | Fix |
| --- | --- |
| `demo` needs a sandbox | start Docker, then `uv run design-agent sandbox build` |
| A render is missing a font | `uv run design-agent fonts verify`; `family` in `fonts.toml` must be the name inside the file |
| Off-brand colours or a failed brand check | `uv run design-agent brand validate`, then `brand css` |
| "The connection to the model stalled" or "stayed unavailable" | check the provider's status, then reply `continue` in the thread |
| "A restart may have stopped…" | a deploy or restart stopped helpers; ask the agent to resume them |
| Paper "not connected" | Paper Desktop is closed or the bridge is down; boards still render in the sandbox |
| `doctor` fails the `skills` check | a vendored skill drifted from `skills-lock.json`: restore it with `git checkout`, or re-vendor with the vendor-sync skill |
| An MCP server is missing | the startup log names the one that failed; the rest keep working |

4. **Logs:** `docker compose -f deploy/self-host/compose.yaml logs api` (and `slack`) when
   self-hosted; the CLI prints to the terminal. Logs carry exception classes, never provider
   messages or keys.
5. **What the run did:** a thread's conversation and its helpers' transcripts are kept in
   `workspace/state/` (SQLite) or Postgres, and each project's files, renders and `run.json` are in
   its sandbox under `/workspace/projects/`. Continue the thread locally with
   `uv run design-agent ask --thread <id> "what went wrong with the last render?"` and the agent
   reads its own files to answer.
6. **A real bug:** reproduce it with a failing test before changing code, then follow `AGENTS.md`.

More: [Operations](../../../OPERATIONS.md).
