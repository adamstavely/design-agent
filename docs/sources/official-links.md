# Official engineering sources

Check the relevant primary source before changing an external contract. These APIs move weekly;
installed package behavior and a live probe outrank model memory and this list. Update the owning
architecture page when supported behavior changes.

## Agent runtime

The runtime is in this repository (`src/design_agent/kit/`); these are the packages it sits on.

- Anthropic Python SDK: <https://github.com/anthropics/anthropic-sdk-python> · Messages API
  <https://platform.claude.com/docs/en/api/messages>
- OpenAI Python SDK: <https://github.com/openai/openai-python> · Responses API
  <https://developers.openai.com/api/docs/guides/migrate-to-responses>
- FastMCP (MCP client for `config/mcp.toml` servers and the Paper bridge): <https://gofastmcp.com>
  · Model Context Protocol specification: <https://modelcontextprotocol.io/specification>
- OpenAI-compatible endpoints for other providers (`kit/models/openai.py`): Groq
  <https://console.groq.com/docs/openai>, Mistral <https://docs.mistral.ai/api/>, Gemini
  <https://ai.google.dev/gemini-api/docs/openai>, OpenRouter <https://openrouter.ai/docs>
- Docker Engine (sandbox containers): <https://docs.docker.com/engine/> · Compose
  <https://docs.docker.com/compose/>

Confirm each URL still resolves when you move a pin forward; the docs sites rename pages.

## HyperFrames (HeyGen) — video engine

- Site: <https://hyperframes.heygen.com> · Quickstart: <https://hyperframes.heygen.com/quickstart>
- Catalog: <https://hyperframes.heygen.com/catalog>
- Repository (Apache-2.0): <https://github.com/heygen-com/hyperframes> · releases
  <https://github.com/heygen-com/hyperframes/releases> · adopters list `ADOPTERS.md`
- Agent skills: `npx skills add heygen-com/hyperframes` (standalone) or the HyperFrames plugin;
  update with `npx hyperframes skills update`.
- CLI at the pinned 0.8.169 (verified with `--help` in the sandbox): `hyperframes init | lint |
  check | snapshot | preview | render --quality draft|looks|delivery --output <file>`; `validate`,
  `inspect` and `layout` are deprecated aliases of `check`.
- Studio (desktop preview app): <https://hyperframes.dev/studio/download>

## Paper — shared design canvas

- MCP docs: <https://paper.design/docs/mcp>
- Downloads (Paper Desktop): <https://paper.design/downloads>
- Agent plugins: `claude plugin marketplace add paper-design/agent-plugins && claude plugin install
  paper-desktop@paper`; Codex: `codex mcp add paper -- /path/to/paper mcp`.
- Transport: Paper Desktop starts the MCP server and installs the `paper` CLI. The recommended
  transport is the stdio server `paper mcp`; the legacy HTTP endpoint
  `http://127.0.0.1:29979/mcp` is served only while a file is open. No hosted or token-based remote
  MCP is documented as of this writing; free plans have MCP limits that reset on Pro.

## Models and tool disclosure

- Anthropic tool search: <https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool>
- Anthropic context editing and compaction: <https://platform.claude.com/docs/en/build-with-claude/context-editing>
- Anthropic vision (image token cost): <https://platform.claude.com/docs/en/build-with-claude/vision>
- OpenAI tool search: <https://developers.openai.com/api/docs/guides/tools-tool-search>
- OpenAI image generation: <https://developers.openai.com/api/docs/guides/image-generation>

## Media services (optional)

- ElevenLabs text to speech and music: <https://elevenlabs.io/docs>
- FFmpeg: <https://ffmpeg.org/documentation.html>
- Chrome Headless Shell: <https://developer.chrome.com/blog/chrome-headless-shell>
- sharp: <https://sharp.pixelplumbing.com>
- Apache ECharts: <https://echarts.apache.org> · D2: <https://d2lang.com>

## Slack

- Slack agents and AI apps (streaming, status): <https://docs.slack.dev/ai/>
- Bolt for Python: <https://docs.slack.dev/tools/bolt-python/>
- Socket Mode: <https://docs.slack.dev/apis/events-api/using-socket-mode>

## Skills format

- Agent Skills specification: <https://agentskills.io/specification>
