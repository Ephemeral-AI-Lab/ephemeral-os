"""Tests for request sandbox provisioning policy."""

from __future__ import annotations

import pytest

from runtime.sandbox_provisioning import RequestSandboxProvisioner


def test_prepares_explicit_sandbox_id_without_create() -> None:
    create_calls: list[dict[str, object]] = []
    start_calls: list[str] = []
    provisioner = RequestSandboxProvisioner(
        create_fn=lambda **kwargs: create_calls.append(kwargs) or {},
        start_fn=lambda sandbox_id: start_calls.append(sandbox_id) or {},
    )

    binding = provisioner.prepare_for_run(
        request_id="request-1",
        sandbox_id=" sbx-explicit ",
    )

    assert binding.sandbox_id == "sbx-explicit"
    assert binding.request_id == "request-1"
    assert create_calls == []
    assert start_calls == ["sbx-explicit"]


def test_creates_sandbox_when_id_is_missing() -> None:
    create_calls: list[dict[str, object]] = []
    start_calls: list[str] = []

    def fake_create(**kwargs):
        create_calls.append(kwargs)
        return {"id": "sbx-created"}

    provisioner = RequestSandboxProvisioner(
        create_fn=fake_create,
        start_fn=lambda sandbox_id: start_calls.append(sandbox_id) or {},
    )

    binding = provisioner.prepare_for_run(request_id="request-2", sandbox_id=None)

    assert binding.sandbox_id == "sbx-created"
    assert binding.request_id == "request-2"
    assert start_calls == []
    assert len(create_calls) == 1
    assert str(create_calls[0]["name"]).startswith("request-")
    assert create_calls[0]["labels"] == {
        "origin": "workflow",
        "request_id": "request-2",
    }


def test_create_without_id_is_rejected() -> None:
    provisioner = RequestSandboxProvisioner(create_fn=lambda **_: {"name": "missing-id"})

    with pytest.raises(RuntimeError, match="create_sandbox returned no id"):
        provisioner.prepare_for_run(request_id="request-3", sandbox_id=None)
