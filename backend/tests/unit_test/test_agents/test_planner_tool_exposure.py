"""Round 3 Phase 5: planner tool surface includes load_skill_reference, not load_skill."""

from __future__ import annotations

import asyncio
from pathlib import Path

from agents import load_agents_dir
from skills.core.registry import SkillRegistry
from skills.core.types import SkillDefinition
from tools._framework.core.base import ToolExecutionContextService
from tools.skills._factory import (
    _registry,
    make_load_skill_reference_for_skill,
)

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PLANNER_DIR = BACKEND_ROOT / "src" / "agents" / "profile" / "main"


def _load_planner():
    by_name = {a.name: a for a in load_agents_dir(PLANNER_DIR)}
    return by_name["planner"]


def test_planner_profile_exposes_load_skill_reference():
    assert "load_skill_reference" in _load_planner().allowed_tools


def test_planner_profile_does_not_expose_load_skill():
    assert "load_skill" not in _load_planner().allowed_tools


def test_no_main_or_helper_profile_lists_load_skill():
    profiles = BACKEND_ROOT / "src" / "agents" / "profile"
    for path in profiles.rglob("*.md"):
        content = path.read_text(encoding="utf-8")
        assert "- load_skill\n" not in content, (
            f"{path}: load_skill is not shipped in v1 (Round 3 design)"
        )


def test_only_planner_profile_declares_load_skill_reference():
    profiles = BACKEND_ROOT / "src" / "agents" / "profile"
    declaring: list[str] = []
    for path in profiles.rglob("*.md"):
        content = path.read_text(encoding="utf-8")
        if "load_skill_reference" in content:
            declaring.append(path.name)
    assert sorted(declaring) == ["planner.md"]


def test_load_skill_reference_is_scoped_to_own_skill():
    """A tool scoped to slug X must refuse to load slug Y."""
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="planner",
            description="planner",
            content="# x",
            source="test",
            references={"checklist": "checklist body"},
        )
    )
    registry.register(
        SkillDefinition(
            name="executor",
            description="executor",
            content="# y",
            source="test",
            references={"rubric": "rubric body"},
        )
    )

    tool = make_load_skill_reference_for_skill(
        skill_slug="planner", skill_registry=registry
    )

    own = asyncio.run(
        tool.execute(
            tool.input_model(skill_name="planner", reference_name="checklist"),
            ToolExecutionContextService(cwd=Path("/tmp")),
        )
    )
    foreign = asyncio.run(
        tool.execute(
            tool.input_model(
                skill_name="executor", reference_name="rubric"
            ),
            ToolExecutionContextService(cwd=Path("/tmp")),
        )
    )

    assert own.is_error is False
    assert own.output == "checklist body"
    assert foreign.is_error is True


def test_bundled_skill_registry_includes_planner_skill():
    """The shipped planner skill folder is picked up by bundled discovery."""
    _registry.cache_clear()
    registry = _registry()
    assert registry.get("planner") is not None
