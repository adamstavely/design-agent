"""`doctor`'s checks that read the checkout itself."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from design_agent.doctor import PACKAGES, _check_skills

ROOT = Path(__file__).resolve().parents[2]


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    for name in ("skills", "skill-binaries", "licenses"):
        shutil.copytree(ROOT / name, root / name, symlinks=True)
    shutil.copytree(ROOT / ".agents", root / ".agents", symlinks=True)
    shutil.copy(ROOT / "skills-lock.json", root / "skills-lock.json")
    return root


def test_this_checkouts_vendored_skills_match_the_lock(tmp_path: Path) -> None:
    check = _check_skills(_project(tmp_path))
    assert check.status == "ok", check.detail


def test_an_edited_vendored_skill_is_reported(tmp_path: Path) -> None:
    root = _project(tmp_path)
    lock = json.loads((root / "skills-lock.json").read_text())
    assert lock, "the lock lists vendored skills"
    vendored = next(
        p for p in sorted((root / "skills").rglob("SKILL.md")) if "hyperframes" in str(p)
    )
    vendored.write_text(vendored.read_text() + "\nedited\n")
    assert _check_skills(root).status == "warn"


def test_doctor_checks_the_provider_sdks_not_an_agent_framework() -> None:
    assert set(PACKAGES) == {"anthropic", "openai", "fastmcp"}
