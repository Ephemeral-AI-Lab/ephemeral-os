"""Unit tests for the live-harness _resolve_live_image helper (PLAN_v2 §5.1)."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest


def _load_sandbox_fixture():
    """Import the live-harness sandbox_fixture module.

    ``live_e2e_test`` is under pytest's ``norecursedirs`` but is still a
    real Python package (has ``__init__.py``). Adding ``backend/tests`` to
    sys.path lets us use the canonical absolute import for the qualified
    package path, which is the same import path the live suite uses.
    """
    repo_root = Path(__file__).resolve().parents[5]
    tests_root = repo_root / "backend/tests"
    if str(tests_root) not in sys.path:
        sys.path.insert(0, str(tests_root))
    return importlib.import_module(
        "live_e2e_test.sandbox._harness.sandbox_fixture"
    )


_sandbox_fixture = _load_sandbox_fixture()
_resolve_live_image = _sandbox_fixture._resolve_live_image


@dataclass
class _FakeProviderSettings:
    default_image: str


@dataclass
class _FakeSandboxSettings:
    daytona: _FakeProviderSettings


@dataclass
class _FakeSettings:
    sandbox: _FakeSandboxSettings


def _patch_settings(image: str):
    return patch.object(
        _sandbox_fixture,
        "load_settings",
        return_value=_FakeSettings(
            sandbox=_FakeSandboxSettings(
                daytona=_FakeProviderSettings(default_image=image),
            ),
        ),
    )


def test_env_set_wins_for_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOS_LIVE_E2E_IMAGE", "explicit-image:tag")
    with _patch_settings("registry/daytona-image:v1"):
        assert _resolve_live_image("docker") == "explicit-image:tag"


def test_env_set_wins_for_daytona(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EOS_LIVE_E2E_IMAGE", "explicit-image:tag")
    with _patch_settings("registry/daytona-image:v1"):
        assert _resolve_live_image("daytona") == "explicit-image:tag"


def test_env_unset_daytona_falls_back_to_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EOS_LIVE_E2E_IMAGE", raising=False)
    with _patch_settings("registry/daytona-image:v1"):
        assert _resolve_live_image("daytona") == "registry/daytona-image:v1"


def test_env_unset_docker_skips_without_explicit_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EOS_LIVE_E2E_IMAGE", raising=False)
    with _patch_settings("registry/daytona-image:v1"):
        with pytest.raises(pytest.skip.Exception):
            _resolve_live_image("docker")


def test_env_unset_daytona_empty_settings_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EOS_LIVE_E2E_IMAGE", raising=False)
    with _patch_settings("   "):
        with pytest.raises(pytest.skip.Exception):
            _resolve_live_image("daytona")


def test_env_unset_docker_empty_settings_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EOS_LIVE_E2E_IMAGE", raising=False)
    with _patch_settings("   "):
        with pytest.raises(pytest.skip.Exception):
            _resolve_live_image("docker")
