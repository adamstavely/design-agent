# Upstream asks

Seams this project works around today, each with what it does instead. The agent runtime lives in
this repository ([`src/design_agent/kit/`](../src/design_agent/kit/__init__.py)), so its own seams
are fixed here rather than asked for upstream. Partner questions for HyperFrames and Paper are in
[docs/partners/](partners/).

## Vendored skills

| Ask | Why | Today |
| --- | --- | --- |
| Shorter descriptions for the resident skill list | vendored skills carry long upstream descriptions | `SkillListMiddleware` swaps them per request from `config/vendored-skills.toml` |
