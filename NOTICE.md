# Notices

Design Agent is Copyright 2026 LangChain, Inc., licensed under the Apache License 2.0
([LICENSE](LICENSE)).

## Provenance

Design Agent is the open-source version of LangChain's internal design and motion agent
(internal codename "Content Studio"). Its harness, middleware and gates were ported from that
agent through an allowlist with names, identifiers and company content removed. LangChain built
and open-sourced it at [langchain-ai/design-agent](https://github.com/langchain-ai/design-agent).

This repository is a fork of that project. It replaces the original Deep Agents and Managed Deep
Agents runtime with a small agent runtime of its own (`src/design_agent/kit/`) over the Anthropic
and OpenAI SDKs, and runs self-hosted only. The changes are listed in [CHANGELOG.md](CHANGELOG.md)
and are released under the same Apache License 2.0.

## Third-party components

| Component | Where | License |
| --- | --- | --- |
| HyperFrames agent skills (HeyGen), vendored unmodified at release v0.8.169 | `skills/` entries listed in `skills-lock.json` | Apache-2.0, [licenses/hyperframes-LICENSE.txt](licenses/hyperframes-LICENSE.txt) |
| HyperFrames CLI | installed in the sandbox image, not redistributed here | Apache-2.0 |
| Geist, Fragment Mono, Inter, IBM Plex Mono, Fira Code, Fraunces | `fonts/` | SIL Open Font License 1.1, [fonts/licenses/](fonts/licenses/) |
| Apache ECharts | installed in the sandbox image | Apache-2.0 |
| sharp, puppeteer-core | installed in the sandbox image | Apache-2.0 |
| GSAP | installed in the sandbox image, never vendored | GSAP Standard "no charge" license |
| D2 | binary installed in the sandbox image | MPL-2.0 |
| Chrome Headless Shell | binary installed in the sandbox image | Chromium licenses |
| FFmpeg | Debian package in the sandbox image | LGPL/GPL per the Debian build |

The sample brand in [examples/brands/example-studio/](examples/brands/example-studio/) is original
work released under CC0-1.0.
