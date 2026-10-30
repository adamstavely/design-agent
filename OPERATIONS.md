# Operations

Running Design Agent day to day: configuration, the sandbox, deployments, costs and what to do when
something breaks.

| Topic | Guide |
| --- | --- |
| Models, roles and the capability registry | [docs/models.md](docs/models.md) |
| Self-hosting with Docker Compose | [docs/self-hosting.md](docs/self-hosting.md) |
| Brand packs, skills, learning, MCP | [docs/customization.md](docs/customization.md) |
| Paper modes and the bridge | [docs/partners/paper.md](docs/partners/paper.md) |
| Evals and the gallery | [docs/evals.md](docs/evals.md) |
| Every CLI command | [docs/cli.md](docs/cli.md) |
| How it works | [docs/architecture/](docs/architecture/README.md) |

## Configuration

Settings live in `.env` (Git-ignored); `.env.example` documents every one.
`uv run design-agent config show` prints them with secrets masked, `config set KEY=value` changes
them, `config generate` fills random tokens and passwords. `uv run design-agent doctor` checks the
model for every role, the brand pack, the sandbox, the vendored skills, Paper and MCP without
printing secrets.

## The sandbox computer

One recipe (`sandbox/setup.sh`) builds every sandbox: the Docker image
(`design-agent sandbox build`, or Compose's build) and CI. It
pins Chrome Headless Shell, ffmpeg, HyperFrames, D2, the Node render toolchain and open fonts by
version and checksum, and ends with a probe (`sandbox test`) that renders a page, a 2-second film
and a diagram. Change the recipe, rebuild, re-run `sandbox test` and `make render`, then restart
the API and Slack services so new containers use the new image.

Per thread: one container with no network, with the thread's files in `/workspace/projects/` kept between turns. Idle containers stop after
`DESIGN_AGENT_SANDBOX_IDLE_TTL_SECONDS` and restart on the next command; their volumes stay.

## Costs

Every model call is priced from `config/prices.toml` (list prices with the date checked and the
source) and summed per job, helper and role. Figures are estimates; your provider's invoice is the
truth. Set `DESIGN_AGENT_JOB_BUDGET_USD` to make the agent ask before going past a budget, and
`DESIGN_AGENT_HELPER_EFFORT_CAP` to cap helpers' effort.

## Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| `demo` says it needs a sandbox | Docker is not running or the image is missing: `uv run design-agent sandbox build` |
| A render fails with a missing font | the file is not in `fonts/` or `private-fonts/`, or `fonts.toml` names a family the file does not carry: `fonts verify` |
| "The connection to the model stalled" | a call passed `DESIGN_AGENT_MODEL_TIMEOUT_SECONDS`; reply `continue` |
| "The model stayed unavailable" | the provider failed through every retry; check the provider's status, reply `continue` |
| "A restart may have stopped…" | a deploy or restart stopped helpers; ask the agent to resume them |
| Paper "not connected" | Paper Desktop is closed or the bridge is unreachable; boards still render in the sandbox |
| An MCP server is missing | it failed to load; the startup log names it and the rest keep working |

Logs: `docker compose -f deploy/self-host/compose.yaml logs api` (and `slack`) for self-hosted;
the CLI prints to the terminal. Logs carry exception classes, never provider messages or keys.

## Upgrading

- Python dependencies: `uv lock --upgrade`, then the checks in [CONTRIBUTING.md](CONTRIBUTING.md).
- HyperFrames and its skills: the [vendor-sync skill](.agents/skills/vendor-sync/SKILL.md).
- The self-hosted stack: `git pull`, then
  `docker compose -f deploy/self-host/compose.yaml up -d --build` (rebuilds the sandbox image and
  the services); the [design-agent-update skill](.agents/skills/design-agent-update/SKILL.md)
  walks through it.

## Backups

Self-hosted state is in Postgres (`postgres` volume) and delivered files in the `out` volume; back
both up with your usual tools. Without Compose and `DATABASE_URL`, state is in SQLite files under
`workspace/state/`.
