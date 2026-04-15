"""Shared role-based tool visibility policy for team-mode agents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleToolPolicy:
    """Role-level policy for visible submission and terminal tools."""

    allowed_submission_tools: frozenset[str]
    terminal_tools: frozenset[str]


_ROLE_TOOL_POLICIES: dict[str, RoleToolPolicy] = {
    "planner": RoleToolPolicy(
        allowed_submission_tools=frozenset({"draft_task_plan", "submit_task_plan"}),
        terminal_tools=frozenset({"submit_task_plan"}),
    ),
    "replanner": RoleToolPolicy(
        allowed_submission_tools=frozenset(
            {"draft_task_plan", "submit_task_plan", "declare_blocker"}
        ),
        terminal_tools=frozenset({"submit_task_plan", "declare_blocker"}),
    ),
    "developer": RoleToolPolicy(
        allowed_submission_tools=frozenset({"submit_task_summary"}),
        terminal_tools=frozenset({"submit_task_summary"}),
    ),
    "reviewer": RoleToolPolicy(
        allowed_submission_tools=frozenset({"submit_task_summary"}),
        terminal_tools=frozenset({"submit_task_summary"}),
    ),
    "resolver": RoleToolPolicy(
        allowed_submission_tools=frozenset({"submit_task_summary"}),
        terminal_tools=frozenset({"submit_task_summary"}),
    ),
    "explorer": RoleToolPolicy(
        allowed_submission_tools=frozenset(),
        terminal_tools=frozenset(),
    ),
    "scout": RoleToolPolicy(
        allowed_submission_tools=frozenset(),
        terminal_tools=frozenset(),
    ),
}


def get_role_tool_policy(role: str | None) -> RoleToolPolicy | None:
    """Return the shared team-mode role policy, if any."""
    role_name = str(role or "").strip()
    if not role_name:
        return None
    return _ROLE_TOOL_POLICIES.get(role_name)


def default_terminal_tools_for_role(role: str | None) -> set[str]:
    """Return the default terminal tool set for a role."""
    policy = get_role_tool_policy(role)
    if policy is None:
        return set()
    return set(policy.terminal_tools)


def blocked_submission_tools_for_role(
    role: str | None,
    available_submission_tools: list[str] | set[str] | tuple[str, ...],
) -> set[str]:
    """Return submission tools that should be hidden for this role."""
    policy = get_role_tool_policy(role)
    if policy is None:
        return set()
    available = {
        str(name).strip()
        for name in available_submission_tools
        if str(name).strip()
    }
    return available - set(policy.allowed_submission_tools)
