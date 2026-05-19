"""register_sweevo_snapshot branches across providers (PLAN_v4 §6 Step 5)."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import benchmarks.sweevo.sandbox as sweevo_sandbox


@pytest.fixture
def instance() -> SimpleNamespace:
    return SimpleNamespace(
        instance_id="i-1",
        instance_id_swe="swe-1",
        docker_image="ghcr.io/example/sweevo:tag",
    )


def _stub_default_provider(name: str) -> MagicMock:
    fake = MagicMock()
    fake.name = name
    return fake


@pytest.mark.parametrize("provider_name", ["daytona", "docker"])
def test_register_sweevo_snapshot_covers_supported_providers(
    monkeypatch: pytest.MonkeyPatch, instance: SimpleNamespace, provider_name: str
) -> None:
    monkeypatch.setattr(
        "sandbox.provider.registry.get_default_provider",
        lambda: _stub_default_provider(provider_name),
    )

    fake_completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=fake_completed) as run_mock:
        result = sweevo_sandbox.register_sweevo_snapshot(instance)

    assert run_mock.called
    # Verify the underlying CLI matches the provider.
    args = run_mock.call_args_list[0].args[0]
    if provider_name == "daytona":
        assert args[0] == "daytona"
    else:
        assert args[0] == "docker"
    assert result.startswith("sweevo-")


def test_register_sweevo_snapshot_unknown_provider_raises(
    monkeypatch: pytest.MonkeyPatch, instance: SimpleNamespace
) -> None:
    monkeypatch.setattr(
        "sandbox.provider.registry.get_default_provider",
        lambda: _stub_default_provider("e2b"),
    )

    with pytest.raises(NotImplementedError, match="e2b"):
        sweevo_sandbox.register_sweevo_snapshot(instance)


def test_register_sweevo_snapshot_docker_pulls_then_tags(
    monkeypatch: pytest.MonkeyPatch, instance: SimpleNamespace
) -> None:
    monkeypatch.setattr(
        "sandbox.provider.registry.get_default_provider",
        lambda: _stub_default_provider("docker"),
    )

    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=completed) as run_mock:
        sweevo_sandbox.register_sweevo_snapshot(instance)

    assert len(run_mock.call_args_list) == 2
    assert run_mock.call_args_list[0].args[0][:2] == ["docker", "pull"]
    assert run_mock.call_args_list[1].args[0][:2] == ["docker", "tag"]


def test_register_sweevo_snapshot_docker_pull_failure_raises(
    monkeypatch: pytest.MonkeyPatch, instance: SimpleNamespace
) -> None:
    monkeypatch.setattr(
        "sandbox.provider.registry.get_default_provider",
        lambda: _stub_default_provider("docker"),
    )

    failure = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not found")
    with patch("subprocess.run", return_value=failure):
        with pytest.raises(RuntimeError, match="docker pull"):
            sweevo_sandbox.register_sweevo_snapshot(instance)
