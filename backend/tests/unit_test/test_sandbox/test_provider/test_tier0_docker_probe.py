"""Unit tests for the tier-0 docker-branch capability probe (PLAN_v2 Step 3)."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _load_tier0():
    repo_root = Path(__file__).resolve().parents[5]
    tests_root = repo_root / "backend/tests"
    if str(tests_root) not in sys.path:
        sys.path.insert(0, str(tests_root))
    return importlib.import_module("live_e2e_test._tools.tier0_health")


_tier0 = _load_tier0()


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _settings_with_image(image: str) -> SimpleNamespace:
    return SimpleNamespace(sandbox=SimpleNamespace(default_image=image))


def test_docker_probe_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOS_LIVE_E2E_IMAGE", "my-image:tag")
    monkeypatch.setenv("EOS_DOCKER_PRIVILEGED", "")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return _completed(0)

    with patch.object(_tier0.shutil, "which", return_value="/usr/bin/docker"), \
         patch.object(_tier0.subprocess, "run", side_effect=fake_run):
        result = _tier0.probe_tier0_docker()

    assert result.passed is True
    assert "docker_info=ok" in result.notes
    assert "image_inspect=ok" in result.notes
    assert "capability_probe=ok" in result.notes
    assert "eos_docker_privileged=" in result.notes
    assert [c[0] for c in calls] == ["docker", "docker", "docker"]
    assert calls[1][:3] == ["docker", "image", "inspect"]
    assert calls[2][:3] == ["docker", "run", "--rm"]


def test_docker_probe_falls_back_to_default_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EOS_LIVE_E2E_IMAGE", raising=False)
    monkeypatch.setenv("EOS_DOCKER_PRIVILEGED", "")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return _completed(0)

    with patch.object(_tier0.shutil, "which", return_value="/usr/bin/docker"), \
         patch("config.settings.load_settings", return_value=_settings_with_image("default:tag")), \
         patch.object(_tier0.subprocess, "run", side_effect=fake_run):
        result = _tier0.probe_tier0_docker()

    assert result.passed is True
    assert "image_inspect=ok image='default:tag'" in result.notes
    assert calls[1] == ["docker", "image", "inspect", "default:tag"]
    assert calls[2][-4:-1] == ["default:tag", "sh", "-c"]


def test_docker_probe_daemon_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOS_LIVE_E2E_IMAGE", "my-image:tag")
    with patch.object(_tier0.shutil, "which", return_value="/usr/bin/docker"), \
         patch.object(_tier0.subprocess, "run", return_value=_completed(1)):
        result = _tier0.probe_tier0_docker()
    assert result.passed is False
    assert "docker_info=fail" in result.notes


def test_docker_probe_missing_image_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EOS_LIVE_E2E_IMAGE", raising=False)
    with patch.object(_tier0.shutil, "which", return_value="/usr/bin/docker"), \
         patch("config.settings.load_settings", return_value=_settings_with_image("")), \
         patch.object(_tier0.subprocess, "run", return_value=_completed(0)):
        result = _tier0.probe_tier0_docker()
    assert result.passed is False
    assert "missing_live_image_default" in result.notes


def test_docker_probe_image_inspect_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOS_LIVE_E2E_IMAGE", "missing:tag")
    sequence = iter([_completed(0), _completed(1)])

    def fake_run(argv, **kwargs):
        return next(sequence)

    with patch.object(_tier0.shutil, "which", return_value="/usr/bin/docker"), \
         patch.object(_tier0.subprocess, "run", side_effect=fake_run):
        result = _tier0.probe_tier0_docker()
    assert result.passed is False
    assert "image_inspect=fail" in result.notes
    assert "missing:tag" in result.notes


def test_docker_probe_capability_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOS_LIVE_E2E_IMAGE", "img:tag")
    sequence = iter([
        _completed(0),
        _completed(0),
        _completed(1, stderr="mount: permission denied"),
    ])

    def fake_run(argv, **kwargs):
        return next(sequence)

    with patch.object(_tier0.shutil, "which", return_value="/usr/bin/docker"), \
         patch.object(_tier0.subprocess, "run", side_effect=fake_run):
        result = _tier0.probe_tier0_docker()
    assert result.passed is False
    assert "capability_probe=fail" in result.notes
    assert "mount: permission denied" in result.notes


def test_docker_probe_no_docker_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOS_LIVE_E2E_IMAGE", "img:tag")
    with patch.object(_tier0.shutil, "which", return_value=None):
        result = _tier0.probe_tier0_docker()
    assert result.passed is False
    assert "docker_unavailable" in result.notes


def test_probe_tier0_dispatches_to_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOS_SANDBOX_PROVIDER", "docker")
    sentinel = _tier0.Tier0Result(passed=True, api_health="ok", notes="docker-branch")
    with patch.object(_tier0, "probe_tier0_docker", return_value=sentinel) as docker_branch:
        result = _tier0.probe_tier0(api_url="http://unused/api")
    docker_branch.assert_called_once()
    assert result is sentinel


def test_probe_tier0_defaults_to_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EOS_SANDBOX_PROVIDER", raising=False)
    sentinel = _tier0.Tier0Result(passed=True, api_health="ok", notes="docker-branch")
    with patch.object(_tier0, "probe_tier0_docker", return_value=sentinel) as docker_branch:
        result = _tier0.probe_tier0(api_url="http://unused/api")
    docker_branch.assert_called_once()
    assert result is sentinel


def test_probe_tier0_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOS_SANDBOX_PROVIDER", "nopesvc")
    result = _tier0.probe_tier0(api_url="http://unused/api")
    assert result.passed is False
    assert "unsupported provider" in result.notes


def test_probe_tier0_daytona_branch_calls_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOS_SANDBOX_PROVIDER", "daytona")
    with patch.object(_tier0, "_check_api_health", return_value=("ok", "ok")) as health, \
         patch.object(_tier0, "_detect_stuck_rows", return_value=(False, [], "")), \
         patch.object(
             _tier0, "_detect_runner_bootstrap_issue",
             return_value=_tier0.RunnerBootstrapIssue(
                 docker_available=False, runner_healthy=None, notes="skipped",
             ),
         ):
        result = _tier0.probe_tier0(api_url="http://x/api")
    health.assert_called_once()
    assert result.passed is True
