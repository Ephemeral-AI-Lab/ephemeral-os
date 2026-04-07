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

# Allow imports from backend/src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "src"))

from agents.loader import get_agent_definition  # type: ignore[attr-defined]
from agents.types import AgentDefinition  # type: ignore[attr-defined]
from prompts.runtime_prompt import build_agent_capabilities_prompt, build_runtime_system_prompt  # type: ignore[attr-defined]
from prompts.system_prompt import build_system_prompt  # type: ignore[attr-defined]
from config.settings import load_settings  # type: ignore[attr-defined]


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
            max_turns=record.max_turns,
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

    settings = load_settings()

    # Try file-based lookup first, then fall back to DB
    agent_def = get_agent_definition(args.agent_name)
    if agent_def is None:
        agent_def = _load_from_db(args.agent_name, settings)
    if agent_def is None:
        print(f"Error: agent '{args.agent_name}' not found.", file=sys.stderr)
        sys.exit(1)

    # --- Build system prompt the same way spawn_agent does ---

    if agent_def.system_prompt:
        system_prompt = agent_def.system_prompt
    else:
        settings.system_prompt = None
        system_prompt = build_runtime_system_prompt(settings, cwd=args.cwd)

    # --- Tool registry (mirrors spawn_agent lines 105-150) ---
    if not args.no_capabilities:
        from tools import create_default_tool_registry  # type: ignore[attr-defined]
        from tools.factory import create_toolkit, has_factory, ToolkitContext  # type: ignore[attr-defined]

        tool_registry = create_default_tool_registry()

        toolkit_ctx = ToolkitContext(
            agent_name=args.agent_name,
            cwd=args.cwd,
            metadata={"sandbox_id": args.sandbox_id or ""},
        )

        if agent_def.toolkits:
            for tk_name in agent_def.toolkits:
                if tool_registry.get_toolkit(tk_name) is not None:
                    continue
                if has_factory(tk_name):
                    try:
                        tk = create_toolkit(tk_name, toolkit_ctx)
                        tool_registry.register_toolkit(tk)
                    except Exception as exc:
                        print(
                            f"Warning: failed to create toolkit '{tk_name}': {exc}", file=sys.stderr
                        )

            tool_registry.restrict_to_toolkits(agent_def.toolkits)

        # --- SkillsToolkit (mirrors spawn_agent lines 166-177) ---
        if agent_def.skills:
            from skills.loader import load_skill_registry  # type: ignore[attr-defined]
            from tools.builtins.skills import make_skills_toolkit  # type: ignore[attr-defined]

            skill_registry = load_skill_registry(args.cwd)
            skills_toolkit = make_skills_toolkit(skill_registry, agent_def.skills)
            tool_registry.register_toolkit(skills_toolkit)

        # --- Background toolkit (mirrors spawn_agent) ---
        bg_tool_names = [
            t.name
            for t in tool_registry.list_tools()
            if getattr(t, "background", "forbidden") != "forbidden"
        ]
        has_background_tools = bool(bg_tool_names)
        if has_background_tools:
            from tools.builtins.background import make_background_toolkit  # type: ignore[attr-defined]

            tool_registry.register_toolkit(make_background_toolkit(bg_tool_names))
        awareness = build_agent_capabilities_prompt(
            toolkits=tool_registry.list_toolkits(),
            has_background_tools=bool(bg_tool_names),
            bg_tool_names=bg_tool_names,
        )
        if awareness:
            system_prompt = system_prompt + "\n\n" + awareness

    print(system_prompt)


if __name__ == "__main__":
    main()
