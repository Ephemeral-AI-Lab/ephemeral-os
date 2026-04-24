"""Team and agent definitions loaded from backend/config."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from agents.loader import load_agents_dir
from agents.registry import (
    RESERVED_BUILTIN_AGENT_NAMES,
    register_definition,
    unregister_definition,
)
from agents.types import AgentDefinition
from config.markdown import parse_markdown_frontmatter
from config.paths import get_config_agents_dir, get_config_teams_dir
from team.core.models import TeamDefinition

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical agent names — imported across the codebase for dispatch logic.
# ---------------------------------------------------------------------------
ROOT_PLANNER = "root_planner"
TEAM_PLANNER = "team_planner"
DEVELOPER = "developer"
VALIDATOR = "validator"
SCOUT = "scout"
TEAM_REPLANNER = "team_replanner"

_EXPECTED_CONFIG_AGENT_COUNT = 6
_EXPECTED_CONFIG_TEAM_COUNT = 1

_AGENTS_CONFIG_DIR = get_config_agents_dir()
_TEAMS_CONFIG_DIR = get_config_teams_dir()


# ---------------------------------------------------------------------------
# In-memory team definition registry
# ---------------------------------------------------------------------------


_DEFINITIONS: dict[str, TeamDefinition] = {}
_REGISTERED_CONFIG_AGENT_NAMES: set[str] = set()
_REGISTERED_CONFIG_TEAM_NAMES: set[str] = set()


def register_team_definition(defn: TeamDefinition) -> None:
    """Register or replace a team definition at runtime."""
    _DEFINITIONS[defn.name] = defn


def unregister_team_definition(name: str) -> None:
    """Remove a team definition from the registry (no-op if absent)."""
    _DEFINITIONS.pop(name, None)


def get_team_definition(name: str) -> TeamDefinition | None:
    """Look up a team definition by name."""
    return _DEFINITIONS.get(name)


def list_team_definitions() -> list[TeamDefinition]:
    """Return all registered team definitions."""
    return list(_DEFINITIONS.values())


# ---------------------------------------------------------------------------
# Team-definition markdown loader
# ---------------------------------------------------------------------------


def load_teams_dir(directory: Path) -> list[TeamDefinition]:
    """Load team definitions from ``.md`` files in *directory*."""
    if not directory.is_dir():
        return []
    teams: list[TeamDefinition] = []
    for path in sorted(directory.glob("*.md")):
        try:
            fm, body = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
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
            description = body.strip() or str(fm.get("description") or f"Team: {name}")
            teams.append(
                TeamDefinition(
                    id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"builtin.team.{name}")),
                    name=name,
                    description=description,
                    entry_planner=entry_planner,
                    roster=roster,
                )
            )
        except Exception:
            logger.debug("Failed to load team from %s", path, exc_info=True)
    return teams


# ---------------------------------------------------------------------------
# Builtin registration at boot
# ---------------------------------------------------------------------------


def _load_config_agent_definitions() -> list[AgentDefinition]:
    defs = load_agents_dir(_AGENTS_CONFIG_DIR)
    for d in defs:
        d.source = "builtin"
    if len(defs) != _EXPECTED_CONFIG_AGENT_COUNT:
        logger.error(
            "Expected %d config agents but loaded %d from %s — check config files",
            _EXPECTED_CONFIG_AGENT_COUNT,
            len(defs),
            _AGENTS_CONFIG_DIR,
        )
    return defs


def _load_config_team_definitions() -> list[TeamDefinition]:
    defs = load_teams_dir(_TEAMS_CONFIG_DIR)
    if len(defs) != _EXPECTED_CONFIG_TEAM_COUNT:
        logger.error(
            "Expected %d config teams but loaded %d from %s — check config files",
            _EXPECTED_CONFIG_TEAM_COUNT,
            len(defs),
            _TEAMS_CONFIG_DIR,
        )
    return defs


def register_all() -> None:
    """Register all config-backed team agents and team definitions.

    Definition files under ``backend/config`` are the source of truth.
    """
    for name in RESERVED_BUILTIN_AGENT_NAMES | _REGISTERED_CONFIG_AGENT_NAMES:
        unregister_definition(name)
    _REGISTERED_CONFIG_AGENT_NAMES.clear()
    agents = _load_config_agent_definitions()
    for defn in agents:
        register_definition(defn)
        _REGISTERED_CONFIG_AGENT_NAMES.add(defn.name)

    for name in tuple(_REGISTERED_CONFIG_TEAM_NAMES):
        unregister_team_definition(name)
    _REGISTERED_CONFIG_TEAM_NAMES.clear()
    teams = _load_config_team_definitions()
    for tdefn in teams:
        register_team_definition(tdefn)
        _REGISTERED_CONFIG_TEAM_NAMES.add(tdefn.name)

    logger.info(
        "team definitions registered from backend/config (%d agents, %d teams)",
        len(agents),
        len(teams),
    )
