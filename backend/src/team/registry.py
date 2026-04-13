"""In-memory registry for team definitions."""

from __future__ import annotations

import logging

from team.models import TeamDefinition

logger = logging.getLogger(__name__)

_DEFINITIONS: dict[str, TeamDefinition] = {}


def register_team_definition(defn: TeamDefinition) -> None:
    """Register or replace a team definition at runtime."""
    _DEFINITIONS[defn.name] = defn


def get_team_definition(name: str) -> TeamDefinition | None:
    """Look up a team definition by name."""
    return _DEFINITIONS.get(name)


def list_team_definitions() -> list[TeamDefinition]:
    """Return all registered team definitions."""
    return list(_DEFINITIONS.values())
