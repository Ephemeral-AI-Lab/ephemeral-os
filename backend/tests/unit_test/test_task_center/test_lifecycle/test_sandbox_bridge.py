"""Tests for TaskCenter sandbox binding and setup policy."""

from __future__ import annotations

import pytest

from task_center.sandbox_bridge import TaskCenterSandboxBridge


def test_prepares_explicit_sandbox_id_without_create() -> None:
    create_calls: list[dict[str, object]] = []
    start_calls: list[str] = []
    bridge = TaskCenterSandboxBridge(
        create_fn=lambda **kwargs: create_calls.append(kwargs) or {},
        start_fn=lambda sandbox_id: start_calls.append(sandbox_id) or {},
    )

    binding = bridge.prepare_for_run(
        task_center_run_id="run-1",
        sandbox_id=" sbx-explicit ",
    )

    assert binding.sandbox_id == "sbx-explicit"
    assert binding.task_center_run_id == "run-1"
    assert binding.owned_by_task_center is False
    assert create_calls == []
    assert start_calls == ["sbx-explicit"]


def test_creates_sandbox_when_id_is_missing() -> None:
    create_calls: list[dict[str, object]] = []
    start_calls: list[str] = []

    def fake_create(**kwargs):
        create_calls.append(kwargs)
        return {"id": "sbx-created"}

    bridge = TaskCenterSandboxBridge(
        create_fn=fake_create,
        start_fn=lambda sandbox_id: start_calls.append(sandbox_id) or {},
    )

    binding = bridge.prepare_for_run(task_center_run_id="run-2", sandbox_id=None)

    assert binding.sandbox_id == "sbx-created"
    assert binding.task_center_run_id == "run-2"
    assert binding.owned_by_task_center is True
    assert start_calls == []
    assert len(create_calls) == 1
    assert str(create_calls[0]["name"]).startswith("task-center-")
    assert create_calls[0]["labels"] == {
        "origin": "task_center",
        "task_center_run_id": "run-2",
    }


def test_create_without_id_is_rejected() -> None:
    bridge = TaskCenterSandboxBridge(create_fn=lambda **_: {"name": "missing-id"})

    with pytest.raises(RuntimeError, match="create_sandbox returned no id"):
        bridge.prepare_for_run(task_center_run_id="run-3", sandbox_id=None)
