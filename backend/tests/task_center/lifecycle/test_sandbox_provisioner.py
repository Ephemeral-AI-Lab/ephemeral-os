"""Tests for TaskCenter sandbox binding policy."""

from __future__ import annotations

import pytest

from task_center.sandbox_provisioner import SandboxProvisioner


def test_passes_through_explicit_sandbox_id_without_create() -> None:
    calls: list[dict[str, object]] = []
    provisioner = SandboxProvisioner(create_fn=lambda **kwargs: calls.append(kwargs) or {})

    binding = provisioner.provision(
        task_center_run_id="run-1",
        sandbox_id=" sbx-explicit ",
    )

    assert binding.sandbox_id == "sbx-explicit"
    assert binding.task_center_run_id == "run-1"
    assert binding.owned_by_task_center is False
    assert calls == []


def test_creates_sandbox_when_id_is_missing() -> None:
    calls: list[dict[str, object]] = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return {"id": "sbx-created"}

    provisioner = SandboxProvisioner(create_fn=fake_create)

    binding = provisioner.provision(task_center_run_id="run-2", sandbox_id=None)

    assert binding.sandbox_id == "sbx-created"
    assert binding.task_center_run_id == "run-2"
    assert binding.owned_by_task_center is True
    assert len(calls) == 1
    assert str(calls[0]["name"]).startswith("task-center-")
    assert calls[0]["labels"] == {
        "origin": "task_center",
        "task_center_run_id": "run-2",
    }


def test_create_without_id_is_rejected() -> None:
    provisioner = SandboxProvisioner(create_fn=lambda **_: {"name": "missing-id"})

    with pytest.raises(RuntimeError, match="create_sandbox returned no id"):
        provisioner.provision(task_center_run_id="run-3", sandbox_id=None)
