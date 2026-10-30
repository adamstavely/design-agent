<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://github.com/langchain-ai/design-agent/raw/main/docs/assets/readme/header-dark.png">
    <img src="https://github.com/langchain-ai/design-agent/raw/main/docs/assets/readme/header-light.png" alt="Content Studio: one design brain for your whole company" width="100%">
  </picture>
  <p>
    <a href="LICENSE#gh-light-mode-only"><img src="https://shieldcn.dev/badge/license-Apache%202.0-006DDD.svg?variant=outline&logo=lu:Scale&mode=light" alt="License: Apache 2.0"></a><a href="LICENSE#gh-dark-mode-only"><img src="https://shieldcn.dev/badge/license-Apache%202.0-7FC8FF.svg?variant=outline&logo=lu:Scale&mode=dark" alt="License: Apache 2.0"></a>
    <a href="https://www.python.org#gh-light-mode-only"><img src="https://shieldcn.dev/badge/python-3.11%2B-006DDD.svg?variant=outline&logo=python&mode=light" alt="Python 3.11+"></a><a href="https://www.python.org#gh-dark-mode-only"><img src="https://shieldcn.dev/badge/python-3.11%2B-7FC8FF.svg?variant=outline&logo=python&mode=dark" alt="Python 3.11+"></a>
  </p>
  <p>
    <b><a href="docs/self-hosting.md">Run Content Studio for your team →</a></b> ·
    <a href="#quickstart">Try it locally, no keys needed</a> ·
    <a href="https://www.langchain.com/blog/design-agent">How LangChain built the original</a> ·
    <a href="docs/README.md">Docs</a>
  </p>
</div>

**Content Studio is a design agent you host yourself.** Add your brand and run it in your team's
Slack, over an HTTP API or from the terminal. Anyone can ask it like they'd ask a designer, attach
what they have, and get finished, on-brand files back, from a resized ad to a launch film.

LangChain built it as their in-house design agent and open-sourced it under Apache 2.0
([langchain-ai/design-agent](https://github.com/langchain-ai/design-agent)). This fork runs it on
its own small agent runtime over the Anthropic and OpenAI SDKs, with no LangChain or LangSmith
dependency and no hosted platform: one machine with Docker runs all of it. At LangChain the original
shipped over 3,000 assets, posts with its work drew about 4.5M views on X, and three in four
requests came from outside marketing; [their write-up](https://www.langchain.com/blog/design-agent)
goes deeper into each part.

## See it work

LangChain asked the original for its own launch film. It went full Rocky.

https://github.com/user-attachments/assets/ee5b98e0-7ec6-4b24-ada6-1f27087947a3

<p align="center"><sub>From a 1-bit chick to champion in 47 seconds.</sub></p>

## How it works

The agent runs on a small runtime in this repository ([`src/design_agent/kit/`](src/design_agent/kit/__init__.py)):
the agent loop, middleware, file and shell tools, skills, subagents, conversation compaction,
stores and MCP, calling models through the official `anthropic` and `openai` SDKs. You host it: the
CLI, an HTTP API and a Slack app share one assembly, with SQLite or Postgres for state and Docker
for the sandboxes. The model makes the creative calls, and code renders, checks and delivers.

- **Slack.** Mention it in a thread with a brief and any files. The self-hosted Slack app
  (`design-agent slack`, Socket Mode or HTTP) hands the message and attachments to the agent, and
  the finished files come back to the same thread. While it works, one message updates step by
  step, a footer shows what's running and the cost so far, and anything you add mid-job reaches
  the work in progress.
- **A computer per thread.** Each thread gets its own Docker sandbox: a Linux machine with
  Chrome, FFmpeg, [HyperFrames](https://hyperframes.heygen.com), D2 and your fonts, built from one
  pinned recipe ([`sandbox/setup.sh`](sandbox/)), with no network. Everything it makes is code in a
  project folder there (a slide is HTML, a film is an animation timeline), so a change is an edit
  and a re-render, not a restart. API keys never enter it.
- **Skills and a design wiki.** The always-on prompt is small. Every [skill](skills/) is read from
  `/skills/` and listed by name and description in the system prompt; for each ask the agent first
  reads a router skill that picks the right workflow, from a resized ad to a launch film, out of 35.
  The wiki holds the craft (type, colour, layout, charts) and your brand pack holds the facts.
  Skills load only when a job needs them.
- **Your tools.** Video is built in HyperFrames, which treats a film as a web page on a timeline.
  [Paper](https://paper.design) is a shared canvas where you edit a storyboard or layout directly.
  Image, voice and music models run as tools outside the sandbox, and MCP servers like Notion,
  Linear, GitHub and Figma connect through `config/mcp.toml`, signed in with header tokens from your
  environment ([Integrations](docs/integrations.md)).
- **Async subagents.** On a big job the main agent plans, splits the work and starts subagents in
  the background on the same sandbox and files. It is woken when one finishes, fails or stalls, so
  it never sits waiting and can keep talking to you. Films go storyboard, animatic, then
  production, and code makes every part pass review before assembly.
- **It gets better.** After a job, `learn_from_job` writes notes on what worked against the skill
  it used, kept in the agent's store. Later jobs read and rate those notes, and the ones that don't
  hold up are dropped.

### Watch it explain itself

LangChain asked the original to explain its own harness as a nature documentary, starring a pixel
parrot. It walks through the computer, the tools and the context behind its work.

https://github.com/user-attachments/assets/65eac8e4-0fac-4a5a-9cca-02e26a0a8baa

<p align="center"><sub>Storyboarded, built and narrated by the agent, in one Slack thread.</sub></p>

Read the [architecture](docs/architecture/README.md), or start changing it:
[instructions.md](instructions.md) · [skills/](skills/) · [Customization](docs/customization.md) ·
[Evals](docs/evals.md) · [Operations](OPERATIONS.md) · [Contributing](CONTRIBUTING.md).

## Why it doesn't look like AI

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://github.com/langchain-ai/design-agent/raw/main/docs/assets/readme/same-prompt-dark.png">
    <img src="https://github.com/langchain-ai/design-agent/raw/main/docs/assets/readme/same-prompt-light.png" alt="The same prompt for a hiring chart to a local coding agent with a brand skill and to Content Studio: a broken logo lockup, a generic font, no axis and an off-brand bar on the left; a clean lockup, the brand typeface, an axis with gridlines and brand-blue bars on the right" width="100%">
  </picture>
</p>

<p align="center"><sub>The same prompt to a local coding agent with a brand skill, and to Content Studio.</sub></p>

- **It looks at its work.** It renders, then looks at the image before it posts anything.
- **Your brand is code.** Layouts only take your tokens: your colours, fonts and spacing.
- **It checks what a machine can count.** Colours, fonts, contrast, sizes and chart math are checked
  in code on every render. Taste stays with your designers.

<p align="center">
  <img src="docs/assets/readme/hero.png" alt="The agent's computer: a Slack request for a launch film, project folders, a HyperFrames render in the terminal, the film preview, layers and references" width="100%">
</p>

## Quickstart

| Building it yourself | Starting here |
| --- | --- |
| Months on a harness, a sandbox, skills and a Slack UX | Fork it, add your brand, run it with Docker |
| Polished work that looks like a hundred others | Work that follows your brand, checked on every render |
| Design requests wait in a queue | Anyone asks in Slack: a graphic in minutes, a long deck overnight |
| Every team fixes the same mistakes | Fix a skill once, and every asset after it follows |

You need **Docker** and a model key:

```bash
curl -fsSL https://raw.githubusercontent.com/adamstavely/design-agent/main/install.sh | bash
cd design-agent
uv run design-agent config set ANTHROPIC_API_KEY=sk-ant-…
uv run design-agent sandbox build   # the agent's computer, a local Docker image
uv run design-agent demo            # scripted model, no keys spent
```

The installer gets [uv](https://docs.astral.sh/uv/) if you don't have it, clones the repository and
installs its dependencies; `git clone` and `uv sync` do the same. The demo runs the real agent,
sandbox and checks with a scripted model, and leaves a launch card in three sizes and a 4-second
teaser in `workspace/out/demo/`.

Then ask for something real. Files land in `workspace/out/<thread>/`, and the last line tells you
what the run cost:

```bash
uv run design-agent ask "A LinkedIn card announcing our design review service, 1200x627."
```

**For your team,** run the HTTP API (`uv run design-agent serve`) and your own Slack app
(`uv run design-agent slack`, after `uv sync --extra self-host --extra slack`), or start both with
Postgres in one command:

```bash
uv run design-agent config generate DESIGN_AGENT_API_TOKENS POSTGRES_PASSWORD
docker compose -f deploy/self-host/compose.yaml --profile slack up -d --build
```

[Self-hosting](docs/self-hosting.md) covers the Slack app, the API, durability and security.

**Or ask your coding agent.** Open the repo in Claude Code, Codex or Cursor and say what you want.
[AGENTS.md](AGENTS.md) routes each request to a step-by-step [skill](.agents/skills/):

| Say | It will |
| --- | --- |
| "Put it in our Slack." | build the sandbox, start the API and Slack app after your yes, and check it works |
| "Build our brand pack from our website and brand guide." | draft the pack, validate it, and ask you to review it |
| "Teach it to make our weekly newsletter header." | add a skill for it and try it on a real request |
| "Did that change make it better?" | run the evals before and after, and show you the numbers |

## Make it yours

| Want | Add | Guide |
| --- | --- | --- |
| Your brand | `workspace/brand/` (until then, a neutral sample brand) | [brand-onboarding skill](.agents/skills/brand-onboarding/SKILL.md) |
| Generated images | `OPENAI_API_KEY` | [Models](docs/models.md) |
| Voice, music, sound effects | `ELEVENLABS_API_KEY` | [sound skill](skills/sound/SKILL.md) |
| The Paper canvas | `PAPER_MODE=local` with Paper Desktop, or `bridged` when the agent runs on a server | [Paper](docs/partners/paper.md) |
| Web search, your docs, other tools | MCP servers in `config/mcp.toml` | [Customization](docs/customization.md) |

The images and films on this page use LangChain's brand, because the original agent made them
there. Licensed fonts go in `private-fonts/`, which Git ignores.

## Questions

**What does it cost?** The code is free (Apache 2.0), and it runs on your own machine. You pay your
model provider: at LangChain the median request cost about 50 cents in model spend, and long films
and decks cost more.

**Will it use my brand?** Yes. Add your brand pack and every file follows it. The
[brand-onboarding skill](.agents/skills/brand-onboarding/SKILL.md) builds the pack with you from
your brand guide, website, files or a Paper file.

**Where do I start?** With the graphic your team asks for every week. Let people brief the agent,
review the work and ask for changes in Slack, then teach it the next thing.

**Do I need Slack?** No. Call the HTTP API (`design-agent serve`) or use the terminal
(`design-agent ask`). Long jobs that work in the background (films, parallel looks) report back in
the Slack thread; over the API, poll the thread's `updates`, and in the terminal `ask` stays open
until they finish.

**Is it safe with my keys?** The sandbox never holds a credential and has no network by default.
Host tools do the fetching, generating and delivering, and drop results in as files.

**Which models?** Claude Opus 5.5 by default, through the Anthropic SDK. OpenAI models work through
the OpenAI SDK, and Google Gemini, Groq, Mistral, OpenRouter or any OpenAI-compatible endpoint
through their OpenAI-compatible APIs. See [Models](docs/models.md).

**Where does it run?** On a machine you operate. [Self-hosting](docs/self-hosting.md) runs it with
Docker Compose: an HTTP API, your own Slack app, Postgres and a sandbox container per thread.

**Why two names?** Content Studio is what LangChain calls the agent. The repository, package,
command and the sample Slack app are `design-agent`.

**Does it replace a designer?** No. It takes the repetitive requests, so designers spend their time on
the work only they can do, and their judgment lives on in its skills and checks.

**Build your own.** Fork it, add your brand tokens, fonts and skills, and run it in your team's
Slack. [Quickstart](#quickstart). If it's useful, a star helps other teams find it.

## Built with

<p>
  <a href="https://hyperframes.heygen.com"><img src="https://raw.githubusercontent.com/heygen-com/hyperframes/main/docs/logo/symbol-dark.svg" alt="" height="24" align="absmiddle"> <b>HyperFrames</b></a>
  &nbsp;&nbsp;
  <a href="https://paper.design"><img src="https://avatars.githubusercontent.com/u/178361271?s=48" alt="" height="24" align="absmiddle"> <b>Paper</b></a>
</p>

Also: the [Anthropic](https://github.com/anthropics/anthropic-sdk-python) and
[OpenAI](https://github.com/openai/openai-python) Python SDKs · [FastMCP](https://gofastmcp.com) ·
[Docker](https://www.docker.com) · [Chrome Headless Shell](https://developer.chrome.com/blog/chrome-headless-shell) ·
[FFmpeg](https://ffmpeg.org) · [D2](https://d2lang.com) · [Apache ECharts](https://echarts.apache.org) ·
[sharp](https://sharp.pixelplumbing.com). Logos belong to their owners. HyperFrames and Paper are
partner projects it builds on. Originally built and open-sourced by
[LangChain](https://github.com/langchain-ai/design-agent) ([Notices](NOTICE.md)).

---

[Apache 2.0](LICENSE) · [Notices](NOTICE.md) · [Changelog](CHANGELOG.md) · [Security](SECURITY.md) · [Code of conduct](CODE_OF_CONDUCT.md)
