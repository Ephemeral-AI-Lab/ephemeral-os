"""Skill exports."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from skills.core.registry import SkillRegistry
    from skills.core.types import SkillDefinition

__all__ = ["SkillDefinition", "SkillRegistry", "get_config_skills_dir", "load_skill_registry"]


def __getattr__(name: str) -> type:
    if name in {"get_config_skills_dir", "load_skill_registry"}:
        from config.paths import get_config_skills_dir
        from skills.core.loader import load_skill_registry

        return {
            "get_config_skills_dir": get_config_skills_dir,
            "load_skill_registry": load_skill_registry,
        }[name]
    if name == "SkillRegistry":
        from skills.core.registry import SkillRegistry

        return SkillRegistry
    if name == "SkillDefinition":
        from skills.core.types import SkillDefinition

        return SkillDefinition
    raise AttributeError(name)
