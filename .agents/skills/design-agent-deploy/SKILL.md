---
name: design-agent-deploy
description: Put Design Agent in a team's Slack by self-hosting it. Build the Docker sandbox image, run the HTTP API and Slack app with Docker Compose and Postgres (or by hand with design-agent serve and design-agent slack), verify it with a real request, and ship updates. Use when someone wants the agent running for their team.
---

# Deploy Design Agent

A deployment is what the team uses: the HTTP API, an optional Slack app, Postgres for durability
and Docker for the sandbox computers, on a machine the person operates. Ask before starting
services others will use, before connecting Slack, and before anything that bills. Never print a
key; name missing settings. Setup comes first: `uv run design-agent doctor` should pass and the
brand pack should validate (`design-agent-onboarding`, `brand-onboarding`).

| Needs | |
| --- | --- |
| A host | Linux (or macOS with Docker Desktop) with Docker and Compose v2.24+, ideally dedicated to the agent |
| A model key | `ANTHROPIC_API_KEY` for the default model, or another provider ([docs/models.md](../../../docs/models.md)) |
| Slack (optional) | a Slack app they create from `config/slack-manifest.example.yaml`; Socket Mode needs no public URL |
| Cost | their machine, plus model and media spend |

## With Docker Compose

1. **Settings.** `cp .env.example .env` if there is no `.env` yet, then
   `uv run design-agent config set DESIGN_AGENT_MODEL=…` (the person types the key or sets it in
   `.env`), then `uv run design-agent config generate DESIGN_AGENT_API_TOKENS POSTGRES_PASSWORD`.
   `DESIGN_AGENT_API_TOKENS` is `token:caller,token:caller`; add one per person or service.
2. **Prove the computer.** `uv run design-agent sandbox build && uv run design-agent sandbox test`
   builds the sandbox image (`design-agent-sandbox:local`) and runs its probe. Compose builds the
   same image; this step just fails faster.
3. **Start it** after a yes: `docker compose -f deploy/self-host/compose.yaml up -d --build`, then
   `curl -s http://127.0.0.1:8080/health`. Compose starts Postgres (it sets `DATABASE_URL` for the
   services) and the API on `127.0.0.1:8080`, with `workspace/brand/` and `private-fonts/` mounted
   read-only.
4. **Slack (optional).** Create the app from `config/slack-manifest.example.yaml`, install it, set
   `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` (Socket Mode; for signed HTTP, `SLACK_TRANSPORT=http`
   and `SLACK_SIGNING_SECRET`), then add `--profile slack` to the compose command.
5. **Verify with real requests:** a quick still, then a short storyboarded teaser. In Slack you
   should see live progress, a footer with what's running and the cost so far, a check-in while it
   renders, and files attached at the end. Over the API, `POST /threads/{id}/messages` with
   `Authorization: Bearer <token>`, then poll `GET /threads/{id}/updates` for background work and
   fetch finals from `GET /threads/{id}/files/{name}`.
6. **Logs:** `docker compose -f deploy/self-host/compose.yaml logs api` (and `slack`). Logs carry
   exception classes, never provider messages or keys.
7. **Updates:** pull, then run the same `up -d --build`. A restart stops running helpers; each
   affected thread offers to resume from its saved transcript, and check-ins are re-armed.

## Without Compose

`uv sync --extra self-host --extra slack`, set `DATABASE_URL` for Postgres (or leave it unset for
SQLite files in `workspace/state/`), `uv run design-agent sandbox build`, then run
`uv run design-agent serve` and, for Slack, `uv run design-agent slack`. Keep both under a process
supervisor.

## Security

The API starts sandbox containers through the Docker socket, which is root-equivalent on that
host: use a machine dedicated to the agent. Containers run without network, with capabilities
dropped and CPU, memory and process limits; keys stay in the agent process. The API binds to
`127.0.0.1`: put a TLS-terminating proxy in front before exposing it.

A local preflight does not prove a deployment: only a real request does. Details:
[Self-hosting](../../../docs/self-hosting.md), [Operations](../../../OPERATIONS.md).
