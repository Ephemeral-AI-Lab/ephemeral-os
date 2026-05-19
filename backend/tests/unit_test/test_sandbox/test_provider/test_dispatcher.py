"""Tests for the EOS_SANDBOX_PROVIDER dispatcher (PLAN_v4 §6 Step 3)."""

from __future__ import annotations

import logging
from typing import Any

import pytest

import sandbox.provider.bootstrap as dispatcher_module


@pytest.fixture(autouse=True)
def _reset_bootstrap_sentinel(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Reset the sentinel before each test; also clear EOS_SANDBOX_PROVIDER."""
    monkeypatch.delenv("EOS_SANDBOX_PROVIDER", raising=False)
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    dispatcher_module._reset_for_tests()
    yield
    dispatcher_module._reset_for_tests()


@pytest.fixture
def stub_provider_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Replace bootstrap_*_provider with recorders so no real registration happens."""
    calls: dict[str, list[str]] = {"docker": [], "daytona": []}

    def _fake_docker() -> None:
        calls["docker"].append("called")

    def _fake_daytona() -> None:
        calls["daytona"].append("called")

    import sandbox.provider.docker.bootstrap as docker_bootstrap
    import sandbox.provider.daytona.bootstrap as daytona_bootstrap

    monkeypatch.setattr(docker_bootstrap, "bootstrap_docker_provider", _fake_docker)
    monkeypatch.setattr(daytona_bootstrap, "bootstrap_daytona_provider", _fake_daytona)
    return calls


def test_explicit_docker(monkeypatch: pytest.MonkeyPatch, stub_provider_calls) -> None:
    monkeypatch.setenv("EOS_SANDBOX_PROVIDER", "docker")
    dispatcher_module.bootstrap_sandbox_provider()
    assert stub_provider_calls["docker"] == ["called"]
    assert stub_provider_calls["daytona"] == []


def test_explicit_daytona(monkeypatch: pytest.MonkeyPatch, stub_provider_calls) -> None:
    monkeypatch.setenv("EOS_SANDBOX_PROVIDER", "daytona")
    dispatcher_module.bootstrap_sandbox_provider()
    assert stub_provider_calls["daytona"] == ["called"]
    assert stub_provider_calls["docker"] == []


def test_case_insensitive(monkeypatch: pytest.MonkeyPatch, stub_provider_calls) -> None:
    monkeypatch.setenv("EOS_SANDBOX_PROVIDER", "DOCKER")
    dispatcher_module.bootstrap_sandbox_provider()
    assert stub_provider_calls["docker"] == ["called"]


def test_mixed_case(monkeypatch: pytest.MonkeyPatch, stub_provider_calls) -> None:
    monkeypatch.setenv("EOS_SANDBOX_PROVIDER", "Daytona")
    dispatcher_module.bootstrap_sandbox_provider()
    assert stub_provider_calls["daytona"] == ["called"]


def test_unset_on_linux_picks_docker(
    monkeypatch: pytest.MonkeyPatch, stub_provider_calls
) -> None:
    monkeypatch.setattr(dispatcher_module.sys, "platform", "linux")
    dispatcher_module.bootstrap_sandbox_provider()
    assert stub_provider_calls["docker"] == ["called"]


def test_unset_on_darwin_picks_daytona(
    monkeypatch: pytest.MonkeyPatch, stub_provider_calls
) -> None:
    monkeypatch.setattr(dispatcher_module.sys, "platform", "darwin")
    dispatcher_module.bootstrap_sandbox_provider()
    assert stub_provider_calls["daytona"] == ["called"]


def test_unset_on_unsupported_platform_raises(
    monkeypatch: pytest.MonkeyPatch, stub_provider_calls
) -> None:
    monkeypatch.setattr(dispatcher_module.sys, "platform", "win32")
    with pytest.raises(RuntimeError, match="unsupported platform"):
        dispatcher_module.bootstrap_sandbox_provider()


def test_unknown_provider_raises(
    monkeypatch: pytest.MonkeyPatch, stub_provider_calls
) -> None:
    monkeypatch.setenv("EOS_SANDBOX_PROVIDER", "foobar")
    with pytest.raises(RuntimeError, match="foobar"):
        dispatcher_module.bootstrap_sandbox_provider()


def test_second_call_same_env_is_silent_noop(
    monkeypatch: pytest.MonkeyPatch, stub_provider_calls, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("EOS_SANDBOX_PROVIDER", "docker")
    dispatcher_module.bootstrap_sandbox_provider()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="sandbox.provider.bootstrap"):
        dispatcher_module.bootstrap_sandbox_provider()
    assert stub_provider_calls["docker"] == ["called"]  # not called again
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_second_call_different_env_emits_warning_and_noop(
    monkeypatch: pytest.MonkeyPatch, stub_provider_calls, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("EOS_SANDBOX_PROVIDER", "docker")
    dispatcher_module.bootstrap_sandbox_provider()
    monkeypatch.setenv("EOS_SANDBOX_PROVIDER", "daytona")
    with caplog.at_level(logging.WARNING, logger="sandbox.provider.bootstrap"):
        dispatcher_module.bootstrap_sandbox_provider()
    assert stub_provider_calls["docker"] == ["called"]
    assert stub_provider_calls["daytona"] == []  # not bootstrapped
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings
    assert "called twice" in warnings[0].getMessage()


def test_daytona_credentials_with_docker_logs_once(
    monkeypatch: pytest.MonkeyPatch, stub_provider_calls, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("EOS_SANDBOX_PROVIDER", "docker")
    monkeypatch.setenv("DAYTONA_API_KEY", "sentinel")
    with caplog.at_level(logging.INFO, logger="sandbox.provider.bootstrap"):
        dispatcher_module.bootstrap_sandbox_provider()
    messages = [r.getMessage() for r in caplog.records]
    assert any("Daytona credentials detected" in m for m in messages)


def test_logs_active_provider_once(
    monkeypatch: pytest.MonkeyPatch, stub_provider_calls, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("EOS_SANDBOX_PROVIDER", "docker")
    with caplog.at_level(logging.INFO, logger="sandbox.provider.bootstrap"):
        dispatcher_module.bootstrap_sandbox_provider()
    provider_lines = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("sandbox provider = ")
    ]
    assert provider_lines == ["sandbox provider = docker"]
