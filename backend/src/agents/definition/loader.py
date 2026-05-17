"""Agent definition loading from Markdown files with YAML frontmatter."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from pydantic import ValidationError

from config.markdown import parse_markdown_frontmatter

from .model import AgentDefinition

logger = logging.getLogger(__name__)


def _load_agent_files(paths: Iterable[Path]) -> list[AgentDefinition]:
    agents: list[AgentDefinition] = []
    for path in sorted(paths):
        try:
            fm, body = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            logger.error("Could not read agent definition %s", path, exc_info=True)
            raise
        data = dict(fm)
        if not data.get("name"):
            data["name"] = path.stem
        data["description"] = str(data.get("description") or f"Agent: {data['name']}")
        if body:
            data["system_prompt"] = body
        if "agent_kind" not in data:
            raise ValueError(
                f"Agent profile {path} is missing required 'agent_kind:' "
                "frontmatter field. Declare one of planner / executor / verifier / "
                "evaluator / advisor / explorer / resolver."
            )
        skill_value = data.get("skill")
        if skill_value:
            skill_path = (path.parent / str(skill_value)).resolve()
            if not skill_path.is_file():
                raise FileNotFoundError(
                    f"Agent profile {path} declares skill: {skill_value!r}, "
                    f"but {skill_path} does not exist."
                )
            data["skill"] = skill_path
        try:
            agents.append(AgentDefinition.model_validate(data))
        except ValidationError:
            logger.error("Invalid agent definition in %s", path, exc_info=True)
            raise
    return agents


def load_agents_dir(directory: Path) -> list[AgentDefinition]:
    """Load agent definitions from .md files directly in *directory*."""
    if not directory.is_dir():
        return []
    return _load_agent_files(directory.glob("*.md"))


def load_agents_tree(directory: Path) -> list[AgentDefinition]:
    """Load agent definitions from all .md files under *directory*."""
    if not directory.is_dir():
        return []
    return _load_agent_files(directory.rglob("*.md"))
