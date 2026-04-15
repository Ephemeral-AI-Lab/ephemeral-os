from __future__ import annotations

from types import SimpleNamespace

from code_intelligence.editing.change_labels import change_actor_label


def test_change_actor_label_prefers_task_id() -> None:
    change = SimpleNamespace(task_id="task-123", agent_run_id="agent-run-123")
    assert change_actor_label(change) == "task-123"


def test_change_actor_label_falls_back_to_agent_run_id() -> None:
    change = SimpleNamespace(task_id="", agent_run_id="agent-run-123")
    assert change_actor_label(change) == "agent-run-123"


def test_change_actor_label_defaults_to_unknown_run() -> None:
    change = SimpleNamespace(task_id="", agent_run_id="")
    assert change_actor_label(change) == "unknown-run"
