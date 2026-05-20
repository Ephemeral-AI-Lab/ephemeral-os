"""Tests for repository agent markdown definitions."""

from __future__ import annotations

from pathlib import Path

from agents import AgentKind, load_agents_dir, load_agents_tree


BACKEND_ROOT = Path(__file__).resolve().parents[3]
AGENTS_ROOT = BACKEND_ROOT / "src" / "agents"
MAIN_PROFILE_DIR = AGENTS_ROOT / "profile" / "main"


def _load_named(directory: Path, name: str):
    loaded = load_agents_dir(directory)
    by_name = {a.name: a for a in loaded}
    assert name in by_name, f"agent {name!r} not found in {directory}"
    return by_name[name]


def test_harness_agent_markdown_declares_notification_triggers() -> None:
    planner = _load_named(MAIN_PROFILE_DIR, "planner")
    executor = _load_named(MAIN_PROFILE_DIR, "executor")
    verifier = _load_named(MAIN_PROFILE_DIR, "verifier")
    evaluator = _load_named(MAIN_PROFILE_DIR, "evaluator")

    # Planner terminal restrictions are launch-time router policy; the profile
    # does not carry soft reminder triggers for recursive partial plans.
    assert planner.notification_triggers == []
    assert executor.notification_triggers == ["request_goal_after_edit"]
    assert verifier.notification_triggers == ["resolver_limit"]
    assert evaluator.notification_triggers == ["resolver_limit"]


def test_recursive_agent_loader_finds_harness_profiles() -> None:
    loaded = load_agents_tree(MAIN_PROFILE_DIR)
    by_name = {agent.name: agent for agent in loaded}

    assert {
        "planner",
        "executor",
        "verifier",
        "evaluator",
    } <= set(by_name)
    assert by_name["executor"].agent_kind == AgentKind.EXECUTOR
    assert by_name["executor"].terminals == [
        "submit_execution_handoff",
        "submit_execution_success",
        "submit_execution_blocker",
    ]


def test_executor_profile_uses_goal_solution_terminal() -> None:
    executor = _load_named(MAIN_PROFILE_DIR, "executor")

    assert "submit_execution_handoff" in executor.terminals
    assert "ask_resolver" not in executor.allowed_tools
