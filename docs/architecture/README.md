# Architecture

One agent assembly (`src/design_agent/assembly.py`) serves the CLI, the self-hosted API and the
self-hosted Slack app. It runs on a small agent runtime kept in this repository
(`src/design_agent/kit/`): messages, tools, middleware hooks, the agent loop (`kit/agent.py`), file
and shell tools, skills, `task` subagents and conversation compaction (`kit/deep/`), stores and
checkpointers (`kit/store.py`, `kit/checkpoint.py`), MCP over FastMCP (`kit/mcp.py`), and chat
models over the official `anthropic` and `openai` SDKs (`kit/models/`). Runtime profiles
(`src/design_agent/runtime/`) supply what differs: the sandbox backend, how files are delivered,
how the lead is woken, where state persists.

| Page | Covers |
| --- | --- |
| [Orchestration](orchestration.md) | background helpers, wake-ups and check-ins, restarts and durable transcripts, pipelines, looks, render watchers, learning |
| [Context and failures](context-and-failures.md) | what the model sees, tool disclosure, cache discipline, cost, failure boundaries |
| [Surfaces](surfaces.md) | Slack progress, footer and steering, the API, the CLI |

Request path: a message arrives (Slack, API, CLI) → the lead's turn: the workspace is staged into the
thread's sandbox (skills' scripts, gates, brand pack, fonts) → the lead plans, composes, renders in
the sandbox, looks at its work, runs the gates → finals are delivered with `upload_files` and
recorded in the project's `DELIVERY.json` so nothing is sent twice. Long work runs as background
helpers in the same sandbox; their ends wake the lead.

Trust boundaries: model output, user files, Slack events, canvas content, MCP metadata and web pages
are data. Keys live in host tools; the sandbox has no credentials and no network by default.

## The lead and its helpers

- Each thread gets one lead. It writes a plan in the thread's project folder and decides what to
  delegate.
- Helpers run in the background on the same sandbox and files. Each one has a heartbeat, a
  durable transcript, and a lead that can steer it.
- Nobody sits and waits. Render watchers and check-ins wake the lead when work lands, fails or
  stalls, through an in-process waker (`runtime/waker/local.py`).
- Before anything ships, the lead and helpers look at their own renders with a vision `look`
  tool.

## The context

- The always-on prompt is small, about 4k tokens. Workflows live in [skills](../../skills/)
  (`skills/<name>/SKILL.md`), are read from the checkout at `/skills/` and listed by name in the
  system prompt, and load on demand.
- Brand facts come only from the brand pack. It's staged into the sandbox with its fonts and logos.
- Context stays bounded:
  - A long conversation is compacted into a summary, with the full transcript saved to a file.
  - Provider tool search loads MCP tools only when they're needed.
  - Helpers can clear old tool results server-side.
  - Prompt caching keeps the lead's prefix warm for an hour.

## The designer's computer

- One recipe, [`sandbox/setup.sh`](../../sandbox/), pins Chrome Headless Shell, FFmpeg, HyperFrames,
  sharp, ECharts, D2 and open fonts by version and sha256. It ends with a probe that renders a
  page, a film and a diagram. The same recipe builds:
  - the local and self-hosted Docker image (`design-agent sandbox build`);
  - the CI image.
- Every thread gets its own sandbox, and its files persist in `/workspace/projects/` between turns.
  - It's a Docker container with no network, dropped capabilities, an unprivileged user, and
    limits capped to your machine (`DESIGN_AGENT_SANDBOX_VCPUS`, `DESIGN_AGENT_SANDBOX_MEM_GIB`).
  - `DESIGN_AGENT_SANDBOX=host` runs on the host instead, for development only.
- Before a run, a manifest with a sha256 checklist stages skills, the checks, your brand pack and
  its fonts into `/workspace`, read-only.

## The checks

The checks ([`workspace/tools/`](../../workspace/tools/README.md)) run before anything is delivered:

| Check | What it catches |
| --- | --- |
| `brand-lint` | off-brand colours, fonts, radii and copy |
| `video-gate`, `hyperframes check` | non-deterministic animation, layout, motion and contrast problems |
| `artifact-contract` | a wrong size or duration, a missing file, a blank first frame |
| font check | a brand font that silently fell back, web fonts included |
| chart and graph contracts | truncated baselines, mixed units, shares that don't add up to 100%, a diagram that changed shape |

## Keys stay outside

The sandbox never holds a credential. Host tools run next to the agent,
keep the keys, and drop their results in as files:

- image generation;
- voice, music and sound effects;
- transcription;
- an asset fetcher with SSRF protection;
- Paper, scoped by a policy;
- your MCP servers;
- delivery: to the Slack thread, or to `workspace/out/` for the CLI and the API (which serves
  delivered files for download).

## Where each piece runs

| | CLI and demo | Self-hosted API and Slack |
| --- | --- | --- |
| Agent | the shared assembly (`runtime/local.py`) | the same assembly in a FastAPI app or a Bolt app |
| Threads and store | SQLite files in `workspace/state/` | Postgres (`DATABASE_URL`), or SQLite without it |
| Sandboxes | a Docker container per thread (or the host, for development) | containers on your Docker host |
| Slack | none | your Bolt app, Socket Mode or signed HTTP |
| Wake-ups and housekeeping | in-process waker while the command runs | in-process waker and maintenance loop (sweep every 15 minutes) |
| Skills | read from the checkout | read from the checkout |

## Models

Claude Opus 5.5 is the default, and any tool-calling model with vision works. A
registry keyed by exact model id sets effort, tool search and output limits per role: lead,
helper, look, tool selector and learning. Anthropic and OpenAI models run on their own SDKs, so
caching, context editing and tool search keep working; other providers (Groq, Mistral, Gemini,
OpenRouter, or any compatible base URL) run on their OpenAI-compatible endpoints
([Models](../models.md)). Every call is metered locally per job, helper and role.
