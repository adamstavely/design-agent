# Integrations

The agent reaches other tools through MCP servers: Notion for a brief, Linear for the request
behind it, Figma for the frames, GitHub for the pull request a video explains. Each server's tools
are named `{server}_{tool}` and found through tool search, so a dozen servers do not crowd the
agent's context, and one that fails to load leaves the rest working. What a server returns is
treated as untrusted input, like any file someone attaches.

`config/mcp.example.toml` has ready entries for the services below. Copy it to `config/mcp.toml`
(Git-ignored) and uncomment the ones you want.

## Signing in

Every server signs in with headers read from the host environment (`headers_env`), usually a
bearer token for one shared account:

```toml
[servers.github]
url = "https://api.githubcopilot.com/mcp/"
headers_env = { Authorization = "GITHUB_MCP_AUTHORIZATION" }
```

with `GITHUB_MCP_AUTHORIZATION="Bearer <token>"` in the agent's environment: in `.env`, which
Compose passes to its services, or exported in your shell for the CLI. Header values never reach
the sandbox.
Everyone's requests use that one account, and anyone who can message the agent can read what it
can, so give it a service account, workspace or teamspace made for the agent, and narrow its tools
with `include_tools`. There are no per-person sign-ins: the `connection` and `owner` keys of
earlier versions are rejected with an error.

## The presets

| Service | Server | Good for | Before you start |
| --- | --- | --- | --- |
| Notion | `https://mcp.notion.com/mcp` | Briefs, launch docs, content calendars | |
| Linear | `https://mcp.linear.app/mcp` | Design requests and specs in issues | |
| GitHub | `https://api.githubcopilot.com/mcp/` | Pull requests and release notes for explainers | |
| Figma | `https://mcp.figma.com/mcp` | Frames, components and variables | Figma admits only clients in its MCP catalog |

Other hosted servers work the same way when they accept a token in a header; a server that only
allows an interactive OAuth sign-in cannot be used, since the agent has no per-person sign-in. Each
provider runs its own server and can change it; if one stops connecting, check its docs for the
current address and how it takes a token.

## Your own server

Any HTTP or SSE MCP server works the same way; `config/mcp.example.toml` ends with a template. A
local app's stdio server (`transport = "stdio"`) runs on the agent's host. Paper is set up
with `PAPER_*` instead ([Paper](partners/paper.md)).

`uv run design-agent doctor` lists the servers this checkout declares.
