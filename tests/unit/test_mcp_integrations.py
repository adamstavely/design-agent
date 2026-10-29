"""Integrations: the presets in `config/mcp.example.toml`, and how servers sign in."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from design_agent.config import Settings
from design_agent.tools.mcp import McpConfigError, declared_servers, servers_for_runtime

ROOT = Path(__file__).resolve().parents[2]
SETTING = re.compile(r"# (\[servers\.[a-z0-9]+\]|[a-z_]+ = .*)$")


def presets() -> str:
    """The hosted-integration entries of the example file, uncommented."""
    text = (ROOT / "config" / "mcp.example.toml").read_text(encoding="utf-8")
    section = text.split("--- Hosted integrations", 1)[1].split("--- Your own servers", 1)[0]
    return "\n".join(m.group(1) for line in section.splitlines() if (m := SETTING.match(line)))


def write_config(root: Path, text: str) -> None:
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "mcp.toml").write_text(text, encoding="utf-8")


def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_every_preset_is_a_valid_server_signed_in_by_header(tmp_path: Path) -> None:
    write_config(tmp_path, presets())
    servers = declared_servers(tmp_path)
    assert [s.name for s in servers] == ["notion", "linear", "github", "figma"]
    assert all(s.url.startswith("https://") and s.transport == "http" for s in servers)
    assert all(set(s.headers_env) == {"Authorization"} for s in servers)
    assert all(s.headers({}) is None for s in servers), "an unset credential leaves the server out"


def test_platform_connections_are_refused_with_what_to_use(tmp_path: Path) -> None:
    for extra in ('connection = "docs"', 'owner = "user"'):
        write_config(tmp_path, f'[servers.docs]\nurl = "https://d.example/mcp"\n{extra}\n')
        with pytest.raises(McpConfigError, match="headers_env"):
            declared_servers(tmp_path)


def test_declared_servers_load_on_every_runtime(tmp_path: Path) -> None:
    write_config(tmp_path, '[servers.docs]\nurl = "https://docs.example.com/mcp"\n')
    for runtime in ("local", "self_hosted"):
        assert [s.name for s in servers_for_runtime(tmp_path, settings(), runtime)] == ["docs"]
