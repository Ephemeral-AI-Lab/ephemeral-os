"""Skill loading from repository config."""

from __future__ import annotations

from pathlib import Path

from skills.bundled import get_bundled_skills
from skills.core.registry import SkillRegistry


def load_skill_registry(cwd: str | Path | None = None) -> SkillRegistry:
    """Load config-backed skills."""
    del cwd
    registry = SkillRegistry()
    for skill in get_bundled_skills():
        registry.register(skill)
    return registry
