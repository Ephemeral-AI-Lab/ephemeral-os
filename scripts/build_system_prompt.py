#!/usr/bin/env python3
"""Build and print the system prompt for a given agent name.

Usage:
    python scripts/build_system_prompt.py <agent_name> [--cwd <dir>]

Examples:
    python scripts/build_system_prompt.py coder
    python scripts/build_system_prompt.py planner --cwd /tmp/project
"""

from __future__ import annotations

import argparse
import os
import sys
from types import SimpleNamespace

# Allow imports from backend/src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "src"))

from agents import get_definition  # type: ignore[attr-defined]
from agents.types import AgentDefinition  # type: ignore[attr-defined]
from config.settings import load_settings  # type: ignore[attr-defined]
from engine.runtime.agent import (  # type: ignore[attr-defined]
    _build_agent_system_prompt,
    _build_agent_tool_registry,
    finalize_tool_registry_and_prompt,
)
from team.builtins import register_all as register_team_builtins  # type: ignore[attr-defined]


def _load_from_db(name: str, settings) -> AgentDefinition | None:
    """Try to load an agent definition from the database."""
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
            hooks=record.hooks,
            background=record.background,
            initial_prompt=record.initial_prompt,
            source="user",
        )
    except Exception as exc:
        print(f"Warning: DB lookup failed: {exc}", file=sys.stderr)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Build system prompt for a named agent")
    parser.add_argument("agent_name", help="Name of the agent definition to look up")
    parser.add_argument("--cwd", default=os.getcwd(), help="Working directory (default: cwd)")
    parser.add_argument("--sandbox-id", default="", help="Sandbox ID (passed to toolkit factories)")
    parser.add_argument(
        "--no-capabilities",
        action="store_true",
        help="Skip toolkit/capability awareness section",
    )
    args = parser.parse_args()

    register_team_builtins()
    settings = load_settings()

    # Try file-based lookup first, then fall back to DB
    agent_def = get_definition(args.agent_name)
    if agent_def is None:
        agent_def = _load_from_db(args.agent_name, settings)
    if agent_def is None:
        print(f"Error: agent '{args.agent_name}' not found.", file=sys.stderr)
        sys.exit(1)

    # --- Build system prompt the same way spawn_agent does ---
    config = SimpleNamespace(cwd=args.cwd)
    system_prompt = _build_agent_system_prompt(
        config,
        agent_def,
        settings,
        latest_user_prompt=None,
    )

    # --- Tool registry (mirrors spawn_agent lines 105-150) ---
    if not args.no_capabilities:
        tool_registry = _build_agent_tool_registry(
            config,
            agent_def,
            args.sandbox_id or None,
            args.agent_name,
        )
        system_prompt, _ = finalize_tool_registry_and_prompt(
            tool_registry,
            system_prompt,
            can_spawn_subagents=agent_def.can_spawn_subagents,
        )

    print(system_prompt)


if __name__ == "__main__":
    main()
