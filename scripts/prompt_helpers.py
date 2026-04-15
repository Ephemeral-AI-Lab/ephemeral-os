#!/usr/bin/env python3
"""Helpers for assembling agent and team prompt reports."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace


_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SRC = _ROOT / "backend" / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from agents import get_definition  # type: ignore[attr-defined]
from agents.types import AgentDefinition  # type: ignore[attr-defined]
from config.settings import load_settings  # type: ignore[attr-defined]
from engine.runtime.agent import (  # type: ignore[attr-defined]
    _build_agent_system_prompt,
    _build_agent_tool_registry,
    finalize_tool_registry_and_prompt,
)
from team.builtins import register_all as register_team_builtins  # type: ignore[attr-defined]
from team.models import TeamDefinition  # type: ignore[attr-defined]
from team.registry import get_team_definition, list_team_definitions  # type: ignore[attr-defined]
from team.runtime.context_builder import DEFAULT_TERMINAL_TOOLS  # type: ignore[attr-defined]


def register_builtins() -> None:
    """Register builtin agents and teams into the in-memory registries."""
    register_team_builtins()


def current_settings():
    """Load current runtime settings."""
    return load_settings()


def load_agent_definition(name: str, settings) -> AgentDefinition | None:
    """Load an agent definition by name from memory first, then DB."""
    agent_def = get_definition(name)
    if agent_def is not None:
        return agent_def

    try:
        from db.engine import initialize_db  # type: ignore[attr-defined]
        from agents.db.store import AgentDefinitionStore  # type: ignore[attr-defined]

        sf = initialize_db(settings.database)
        if sf is None:
            return None

        store = AgentDefinitionStore()
        store.initialize(sf)
        record = store.get_by_name(name)
        if record is None:
            return None

        return AgentDefinition(
            name=record.name,
            description=record.description,
            system_prompt=record.system_prompt,
            model=record.model,
            effort=record.effort,
            tool_call_limit=record.tool_call_limit,
            toolkits=record.toolkits or [],
            skills=record.skills or [],
            blocked_tools=record.blocked_tools or [],
            allowed_triggers=record.allowed_triggers or [],
            hooks=record.hooks,
            background=record.background,
            initial_prompt=record.initial_prompt,
            role=record.role,
            agent_type=record.agent_type or "agent",
            supported_kinds=record.supported_kinds or ["atomic", "expandable"],
            source=record.source or "user",
            can_spawn_subagents=record.can_spawn_subagents,
            require_fresh_client=record.require_fresh_client,
            include_skills=record.include_skills,
            dispatchable_via_run_subagent=record.dispatchable_via_run_subagent,
        )
    except Exception:
        return None


def build_agent_system_prompt_text(
    agent_def: AgentDefinition,
    *,
    cwd: str,
    settings,
    sandbox_id: str = "",
    include_capabilities: bool = True,
    terminal_tools: set[str] | list[str] | None = None,
) -> str:
    """Build the assembled system prompt exactly as spawn_agent would."""
    config = SimpleNamespace(cwd=cwd)
    system_prompt = _build_agent_system_prompt(
        config,
        agent_def,
        settings,
        latest_user_prompt=None,
    )

    if include_capabilities:
        tool_registry = _build_agent_tool_registry(
            config,
            agent_def,
            sandbox_id or None,
            agent_def.name,
        )
        system_prompt, _ = finalize_tool_registry_and_prompt(
            tool_registry,
            system_prompt,
            can_spawn_subagents=agent_def.can_spawn_subagents,
            role=agent_def.role,
            blocked_tools=agent_def.blocked_tools,
            terminal_tools=terminal_tools,
        )

    return system_prompt


def resolve_terminal_tools_for_role(team_def: TeamDefinition | None, role: str | None) -> set[str]:
    """Resolve terminal tools for a team role using team overrides or defaults."""
    role_name = str(role or "").strip()
    if not role_name:
        return set()
    td_map = getattr(team_def, "terminal_tools", None) or {}
    terminal_set = td_map.get(role_name) if td_map else None
    if not terminal_set:
        terminal_set = DEFAULT_TERMINAL_TOOLS.get(role_name, set())
    return set(terminal_set)


def load_team_definition(identifier: str, settings) -> TeamDefinition | None:
    """Resolve a team by DB id first, then by name from DB or builtin registry."""
    try:
        from db.engine import initialize_db  # type: ignore[attr-defined]
        from team.persistence.store import TeamDefinitionStore  # type: ignore[attr-defined]

        sf = initialize_db(settings.database)
        if sf is not None:
            store = TeamDefinitionStore()
            store.initialize(sf)
            team_def = store.get_by_id(identifier)
            if team_def is not None:
                return team_def
            team_def = store.get_by_name(identifier)
            if team_def is not None:
                return team_def
    except Exception:
        pass

    team_def = get_team_definition(identifier)
    if team_def is not None:
        return team_def

    for candidate in list_team_definitions():
        if candidate.id == identifier:
            return candidate
    return None


def default_team_prompt_report_path(team_def: TeamDefinition, output_dir: str | None = None) -> Path:
    """Return a stable default output path for a team prompt report."""
    safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in team_def.name).strip("-")
    stem = f"team-system-prompts-{safe_name or 'team'}-{team_def.id[:8]}"
    base_dir = Path(output_dir) if output_dir else Path(os.getcwd())
    return base_dir / f"{stem}.md"
