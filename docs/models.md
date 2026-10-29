# Models

Design Agent talks to models through the official `anthropic` and `openai` Python SDKs
(`src/design_agent/kit/models/`), so any chat model with tool calling and vision behind one of
those APIs can run it. Set one in `.env` (or `uv run design-agent config set …`):

```bash
DESIGN_AGENT_MODEL=anthropic:claude-opus-5-5     # provider:model
ANTHROPIC_API_KEY=…
```

`anthropic:` and `openai:` models run on their own SDKs and keep every feature in the table below.
Other providers run through their OpenAI-compatible endpoints with no extra packages:

| Provider prefix | Endpoint | Key |
| --- | --- | --- |
| `google_genai:` or `google:` | Gemini's OpenAI-compatible API | `GOOGLE_API_KEY` |
| `groq:` | Groq | `GROQ_API_KEY` |
| `mistral:` or `mistralai:` | Mistral | `MISTRAL_API_KEY` |
| `openrouter:` | OpenRouter | `OPENROUTER_API_KEY` |

`GOOGLE_API_KEY` and `GROQ_API_KEY` are read from `.env`; `MISTRAL_API_KEY` and
`OPENROUTER_API_KEY` are read from the process environment (Compose passes `.env` through; for
the CLI, export them in your shell). For any
other OpenAI-compatible endpoint, set `DESIGN_AGENT_MODEL_BASE_URL` and
`DESIGN_AGENT_MODEL_API_KEY_ENV` (the name of the variable that holds its key).

## Roles

| Role | Setting | Default | What it does |
| --- | --- | --- | --- |
| Lead | `DESIGN_AGENT_MODEL`, `DESIGN_AGENT_MODEL_EFFORT` | `anthropic:claude-opus-5-5` at `high` | the conversation, plans, creative calls |
| Helpers | `DESIGN_AGENT_HELPER_MODEL`, `DESIGN_AGENT_HELPER_EFFORT_CAP` | the lead's model; effort per task (`low`, `xhigh`, `max`) | background builders, critics, renders |
| `look` | `DESIGN_AGENT_LOOK_MODEL` | the lead's model at `low` | looks at renders (up to 8 images per call) |
| Tool selector | `DESIGN_AGENT_TOOL_SELECTOR_MODEL` | the lead's model | picks MCP tools, only for models without native tool search |
| Learning | `DESIGN_AGENT_LEARNING_MODEL` | a low-effort helper | the post-job review that proposes observations |

Every role needs vision. `uv run design-agent doctor` checks each one against the registry.

## The capability registry

`src/design_agent/models/registry.py` records, per exact model id, what the agent may rely on:
vision, effort levels, context and output limits, prompt-cache lifetimes, server-side context
editing, and how tools are disclosed. Only verified entries claim provider features; anything else
runs on the portable path (all features that do not depend on the provider).

| Model | Tool disclosure | Notes |
| --- | --- | --- |
| `anthropic:claude-opus-5-5`, `claude-fable-5-1`, `claude-sonnet-5-5` | provider tool search | efforts `low`…`max`; adaptive thinking; 5 m / 1 h cache |
| `anthropic:claude-haiku-5-5` | provider tool search | no effort parameter; good for `look` and the selector |
| `openai:gpt-6.1-sol`, `gpt-6-sol`, `gpt-5.6-sol` | provider tool search | Responses API; reasoning effort |
| `google_genai:gemini-3.6-flash` | bounded selector | no provider-side tool search |
| anything else, or a base URL | bounded selector | no provider-feature claims |

Adding a model: add an entry with the facts from the provider's current documentation, a test in
`tests/unit/test_config_and_registry.py`, and run the eval set (`docs/evals.md`) before relying on it.

## Cost and limits

- Each call is bounded by `DESIGN_AGENT_MODEL_TIMEOUT_SECONDS` (600 by default); provider SDK
  retries are off and the agent's own retry handles transient failures
  ([failure boundaries](architecture/context-and-failures.md#failure-boundaries)).
- The lead's prompt cache lives an hour (`DESIGN_AGENT_CACHE_TTL`), helpers' five minutes.
- Spend is estimated per job from `config/prices.toml`; `DESIGN_AGENT_JOB_BUDGET_USD` makes the
  agent ask before going past a budget.
