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
    planner = _load_named(MAIN_PROFILE_DIR, "planner_closes_or_defers")
    handoff_executor = _load_named(MAIN_PROFILE_DIR, "executor_success_handoff")
    verifier = _load_named(MAIN_PROFILE_DIR, "verifier")
    evaluator = _load_named(MAIN_PROFILE_DIR, "evaluator")

    # The planner's recursive_partial_plan notification trigger was retired
    # in favour of the agent.md `terminals:` filter on planner_closes_goal —
    # if the variant fires, submit_plan_defers_goal is never bound to the LLM
    # tool registry, so a soft reminder serves no purpose.
    assert planner.notification_triggers == []
    assert handoff_executor.notification_triggers == ["request_goal_after_edit"]
    assert verifier.notification_triggers == ["resolver_limit"]
    assert evaluator.notification_triggers == ["resolver_limit"]


def test_recursive_agent_loader_finds_harness_profiles() -> None:
    loaded = load_agents_tree(MAIN_PROFILE_DIR)
    by_name = {agent.name: agent for agent in loaded}

    assert {
        "planner_closes_or_defers",
        "executor",
        "executor_success_handoff",
        "executor_success_failure",
        "verifier",
        "evaluator",
    } <= set(by_name)
    # The thin executor entry-point owns the depth-gated variants and has no
    # terminals of its own — handoff vs failure lives on the resolved targets.
    assert by_name["executor"].agent_kind == AgentKind.EXECUTOR
    assert by_name["executor"].terminals == []
    assert by_name["executor"].variants, "executor must declare depth variants"
    variant_targets = {v.use for v in by_name["executor"].variants}
    assert variant_targets == {"executor_success_handoff", "executor_success_failure"}
    # The depth-shallow target keeps the handoff terminal; the leaf target
    # exposes only success + failure.
    assert (
        "submit_execution_handoff"
        in by_name["executor_success_handoff"].terminals
    )
    assert (
        "submit_execution_handoff"
        not in by_name["executor_success_failure"].terminals
    )


def test_executor_handoff_profile_uses_goal_solution_terminal() -> None:
    handoff = _load_named(MAIN_PROFILE_DIR, "executor_success_handoff")

    assert "submit_execution_handoff" in handoff.terminals
    assert "ask_resolver" not in handoff.allowed_tools
