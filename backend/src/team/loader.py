"""Team definition loading from Markdown files with YAML frontmatter."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from agents.loader import _parse_frontmatter
from team.models import TeamDefinition

logger = logging.getLogger(__name__)


def load_teams_dir(directory: Path) -> list[TeamDefinition]:
    """Load team definitions from ``.md`` files in *directory*."""
    if not directory.is_dir():
        return []
    teams: list[TeamDefinition] = []
    for path in sorted(directory.glob("*.md")):
        try:
            fm, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            name = str(fm.get("name") or path.stem)
            entry_planner = str(fm.get("entry_planner") or "")
            if not entry_planner:
                logger.debug("Skipping %s — missing entry_planner", path)
                continue
            raw_roster = fm.get("roster") or {}
            if not isinstance(raw_roster, dict):
                logger.debug("Skipping %s — roster is not a mapping", path)
                continue
            roster: dict[str, list[str]] = {
                str(role): [str(a) for a in agents]
                for role, agents in raw_roster.items()
                if isinstance(agents, list)
            }
            raw_terminal_tools = fm.get("terminal_tools") or {}
            if raw_terminal_tools and not isinstance(raw_terminal_tools, dict):
                logger.debug("Skipping terminal_tools in %s — not a mapping", path)
                raw_terminal_tools = {}
            terminal_tools: dict[str, set[str]] = {
                str(role): {str(tool) for tool in tools if str(tool)}
                for role, tools in raw_terminal_tools.items()
                if isinstance(tools, list)
            }
            description = body.strip() or str(fm.get("description") or f"Team: {name}")
            teams.append(
                TeamDefinition(
                    id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"builtin.team.{name}")),
                    name=name,
                    description=description,
                    entry_planner=entry_planner,
                    roster=roster,
                    terminal_tools=terminal_tools,
                )
            )
        except Exception:
            logger.debug("Failed to load team from %s", path, exc_info=True)
    return teams
