"""Team role visibility helpers for prompt and tool schema reports."""

from __future__ import annotations

# ruff: noqa: E402

import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SRC = _ROOT / "backend" / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from engine.runtime.agent import _build_agent_tool_registry, finalize_tool_registry_and_prompt
from prompt.helpers import (
    current_settings,
    effective_agent_definition_for_team_report,
    load_agent_definition,
    load_team_definition,
    register_builtins,
    resolve_terminal_tools_for_role,
)
from tools.core.base import decorate_schemas_for_background
from tools.core.schema_summary import format_tool_schema_summary


def _member_roles(roster: dict[str, list[str]], entry_planner: str) -> dict[str, list[str]]:
    members: dict[str, list[str]] = {}
    for role, agent_names in roster.items():
        for agent_name in agent_names:
            roles = members.setdefault(agent_name, [])
            if role not in roles:
                roles.append(role)
    if entry_planner and entry_planner not in members:
        members[entry_planner] = ["planner"]
    return members


def _format_effective_provider_schema_controls(
    registry,
    *,
    terminal_tools: set[str],
    has_background_tools: bool,
) -> str:
    schemas = registry.to_api_schema()
    if has_background_tools:
        schemas = decorate_schemas_for_background(
            registry,
            schemas,
            terminal_tools=terminal_tools,
        )
    lines = ["  effective_provider_schema_controls:"]
    for schema in schemas:
        name = str(schema.get("name") or "")
        input_schema = schema.get("input_schema") or {}
        properties = input_schema.get("properties") or {}
        required = input_schema.get("required") or []
        control_fields = [field for field in ("background",) if field in properties]
        if name in terminal_tools or control_fields:
            lines.append(
                "    - "
                f"{name}: fields={', '.join(properties) or '(none)'}; "
                f"required={', '.join(required) or '(none)'}; "
                f"control_fields={', '.join(control_fields) or '(none)'}"
            )
    if len(lines) == 1:
        lines.append("    (none)")
    return "\n".join(lines)


def _role_visibility_summary(
    *,
    team_name: str,
    cwd: Path,
    sandbox_id: str,
    include_descriptions: bool,
    include_instructions: bool,
) -> str:
    register_builtins()
    settings = current_settings()
    team_def = load_team_definition(team_name, settings)
    if team_def is None:
        return f"Team Role Tool Visibility\n  team {team_name!r} not found"

    lines: list[str] = [
        "Team Role Tool Visibility",
        f"  team: {team_def.name}",
        f"  team_id: {team_def.id}",
        "",
    ]
    exposure: dict[str, list[str]] = {
        "submit_plan": [],
        "submit_replan": [],
        "submit_task_summary": [],
        "request_replan": [],
    }
    for agent_name, roster_roles in _member_roles(team_def.roster, team_def.entry_planner).items():
        base_def = load_agent_definition(agent_name, settings)
        if base_def is None:
            lines.extend([f"Agent: {agent_name}", "  missing agent definition", ""])
            continue
        agent_def = effective_agent_definition_for_team_report(base_def, team_def)
        terminal_tools = resolve_terminal_tools_for_role(team_def, getattr(agent_def, "role", None))
        config = SimpleNamespace(cwd=str(cwd))
        registry = _build_agent_tool_registry(config, agent_def, sandbox_id, agent_def.name)
        _, has_background_tools = finalize_tool_registry_and_prompt(
            registry,
            "",
            can_spawn_subagents=agent_def.can_spawn_subagents,
            terminal_tools=terminal_tools,
        )
        terminal_tool_names = set(terminal_tools)
        tool_names = sorted(tool.name for tool in registry.list_tools())
        for tool_name in exposure:
            if tool_name in tool_names:
                exposure[tool_name].append(agent_name)
        lines.extend(
            [
                f"Agent: {agent_name}",
                f"  roster_roles: {', '.join(roster_roles)}",
                f"  agent_role: {agent_def.role or ''}",
                f"  terminal_tools: {', '.join(sorted(terminal_tools)) or '(none)'}",
                f"  visible_tools: {', '.join(tool_names) or '(none)'}",
                _format_effective_provider_schema_controls(
                    registry,
                    terminal_tools=terminal_tool_names,
                    has_background_tools=has_background_tools,
                ),
                format_tool_schema_summary(
                    registry.list_toolkits(),
                    include_descriptions=include_descriptions,
                    include_instructions=include_instructions,
                ),
                "",
            ]
        )

    lines.extend(
        [
            "Visibility Checks",
            f"  submit_plan visible to: {', '.join(exposure['submit_plan']) or '(none)'}",
            f"  submit_replan visible to: {', '.join(exposure['submit_replan']) or '(none)'}",
            f"  submit_task_summary visible to: {', '.join(exposure['submit_task_summary']) or '(none)'}",
            f"  request_replan visible to: {', '.join(exposure['request_replan']) or '(none)'}",
            (
                "  request_replan note: internal TaskCenter method reached through "
                "submit_task_summary(type='fail'), not a model-facing tool."
            ),
        ]
    )
    return "\n".join(lines)


__all__ = [
    "_format_effective_provider_schema_controls",
    "_member_roles",
    "_role_visibility_summary",
]
