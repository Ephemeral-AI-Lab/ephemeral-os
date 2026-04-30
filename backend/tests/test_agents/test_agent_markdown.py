"""Tests for repository agent markdown definitions."""

from __future__ import annotations

from pathlib import Path

from agents.loader import load_agents_dir


REPO_ROOT = Path(__file__).resolve().parents[3]
AGENTS_ROOT = REPO_ROOT / "backend" / "src" / "agents"


def _load_one(directory: Path):
    loaded = load_agents_dir(directory)
    assert len(loaded) == 1
    return loaded[0]


def test_harness_agent_markdown_declares_notification_triggers() -> None:
    planner = _load_one(AGENTS_ROOT / "main_agent" / "planner")
    executor = _load_one(AGENTS_ROOT / "main_agent" / "generator" / "executor")
    verifier = _load_one(AGENTS_ROOT / "main_agent" / "generator" / "verifier")
    evaluator = _load_one(AGENTS_ROOT / "main_agent" / "evaluator")

    assert planner.notification_triggers == ["recursive_partial_plan"]
    assert executor.notification_triggers == ["request_complex_task_after_edit"]
    assert verifier.notification_triggers == ["resolver_limit"]
    assert evaluator.notification_triggers == ["resolver_limit"]


def test_executor_agent_uses_complex_task_solution_terminal() -> None:
    executor = _load_one(AGENTS_ROOT / "main_agent" / "generator" / "executor")

    assert "request_complex_task_solution" in executor.terminals
    assert "submit_request_plan" not in executor.terminals
    assert "ask_resolver" not in executor.allowed_tools
