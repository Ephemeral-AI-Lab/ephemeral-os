"""Tests for the central typed config loader."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from config import (
    CentralConfig,
    DatabaseConfig,
    get_central_config,
    load_central_config,
    override_central_config,
)


_ENV_KEYS = {
    "DAYTONA_API_KEY",
    "DAYTONA_API_URL",
    "DAYTONA_TARGET",
    "EPHEMERALOS_DATABASE_URL",
    "EPHEMERALOS_RUN_CAPACITY_LIVE_E2E",
    "EPHEMERALOS_RUNTIME_CLIENT_TIMEOUT",
    "EPHEMERALOS_RUN_HEAVY_LIVE_E2E",
    "EPHEMERALOS_SANDBOX_DEFAULT_IMAGE",
    "EPHEMERALOS_SANDBOX_DEFAULT_SNAPSHOT",
    "EPHEMERALOS_SANDBOX_TIMEOUT_SECONDS",
    "EOS_DAEMON_TCP_HOST",
    "EOS_DAEMON_TCP_PORT",
    "EOS_DOCKER_DAEMON_TCP",
    "EOS_DOCKER_NO_PRIVILEGE",
    "EOS_DOCKER_PRIVILEGED",
    "EOS_SANDBOX_PROVIDER",
    "EOS_SWEEVO_FORCE_FRESH_SANDBOX",
    "EOS_SWEEVO_REAL_AGENT_MAX_DURATION_S",
    "EOS_SWEEVO_REUSE_SANDBOX",
    "EOS_SWEEVO_SANDBOX_QUOTA",
}


@pytest.fixture
def clean_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("EOS__") or name in _ENV_KEYS:
            monkeypatch.delenv(name, raising=False)


def test_load_central_config_from_yaml(tmp_path: Path, clean_config_env: None) -> None:
    path = tmp_path / "ephemeralos.yaml"
    path.write_text(
        """
database:
  pool_size: 7
  max_overflow: 11
sandbox:
  default_provider: daytona
  timeout_s: 45
  docker:
    privileged: true
    default_snapshot: docker-snapshot
  daytona:
    api_url: http://localhost:3000/api
    default_image: ghcr.io/example/default:latest
    default_snapshot: daytona-snapshot
providers:
  retry:
    max_retries: 4
    status_codes: [429, 503]
runner:
  audit_dir: custom-runs
  live_e2e:
    heavy_enabled: true
    capacity_enabled: true
""",
        encoding="utf-8",
    )

    cfg = load_central_config(path, dotenv_path=tmp_path / ".env")

    assert cfg.database.pool_size == 7
    assert cfg.sandbox.default_provider == "daytona"
    assert cfg.sandbox.docker.privileged is True
    assert cfg.sandbox.docker.default_snapshot == "docker-snapshot"
    assert cfg.sandbox.daytona.api_url == "http://localhost:3000/api"
    assert cfg.sandbox.daytona.default_image == "ghcr.io/example/default:latest"
    assert cfg.sandbox.daytona.default_snapshot == "daytona-snapshot"
    assert cfg.providers.retry.status_codes == frozenset({429, 503})
    assert cfg.runner.audit_dir == Path("custom-runs")
    assert cfg.runner.live_e2e.heavy_enabled is True
    assert cfg.runner.live_e2e.capacity_enabled is True


def test_unknown_top_level_key_fails(tmp_path: Path, clean_config_env: None) -> None:
    path = tmp_path / "ephemeralos.yaml"
    path.write_text("ui: {}\n", encoding="utf-8")

    with pytest.raises(ValidationError) as exc_info:
        load_central_config(path, dotenv_path=tmp_path / ".env")

    assert "ui" in str(exc_info.value)


def test_unknown_nested_key_fails(tmp_path: Path, clean_config_env: None) -> None:
    path = tmp_path / "ephemeralos.yaml"
    path.write_text(
        """
sandbox:
  docker:
    unknown: true
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as exc_info:
        load_central_config(path, dotenv_path=tmp_path / ".env")

    assert "sandbox.docker.unknown" in str(exc_info.value)


def test_env_overrides_yaml_and_includes_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_config_env: None,
) -> None:
    path = tmp_path / "ephemeralos.yaml"
    path.write_text(
        """
database:
  url: postgresql://yaml/db
  pool_size: 5
sandbox:
  daytona:
    api_key: yaml-key
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("EOS__DATABASE__POOL_SIZE", "12")
    monkeypatch.setenv("EPHEMERALOS_DATABASE_URL", "postgresql://env/db")
    monkeypatch.setenv("DAYTONA_API_KEY", "daytona-env-key")
    monkeypatch.setenv("EPHEMERALOS_SANDBOX_DEFAULT_SNAPSHOT", "legacy-snapshot")

    cfg = load_central_config(path, dotenv_path=tmp_path / ".env")

    assert cfg.database.pool_size == 12
    assert cfg.database.url == "postgresql://env/db"
    assert cfg.sandbox.daytona.api_key == "daytona-env-key"
    assert cfg.sandbox.docker.default_snapshot == "legacy-snapshot"
    assert cfg.sandbox.daytona.default_snapshot == "legacy-snapshot"


def test_live_e2e_gates_and_sandbox_reuse_mode_are_yaml_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_config_env: None,
) -> None:
    path = tmp_path / "ephemeralos.yaml"
    path.write_text(
        """
runner:
  sandbox_reuse_mode: fresh
  live_e2e:
    heavy_enabled: false
    capacity_enabled: false
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("EPHEMERALOS_RUN_HEAVY_LIVE_E2E", "1")
    monkeypatch.setenv("EPHEMERALOS_RUN_CAPACITY_LIVE_E2E", "1")
    monkeypatch.setenv("EOS_SWEEVO_REUSE_SANDBOX", "1")
    monkeypatch.setenv("EOS_SWEEVO_FORCE_FRESH_SANDBOX", "1")
    monkeypatch.setenv("EOS__RUNNER__LIVE_E2E__HEAVY_ENABLED", "true")
    monkeypatch.setenv("EOS__RUNNER__LIVE_E2E__CAPACITY_ENABLED", "true")
    monkeypatch.setenv("EOS__RUNNER__SANDBOX_REUSE_MODE", "force_fresh")

    cfg = load_central_config(path, dotenv_path=tmp_path / ".env")

    assert cfg.runner.live_e2e.heavy_enabled is False
    assert cfg.runner.live_e2e.capacity_enabled is False
    assert cfg.runner.sandbox_reuse_mode == "fresh"


def test_live_e2e_gates_and_sandbox_reuse_mode_ignore_env_without_yaml_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_config_env: None,
) -> None:
    path = tmp_path / "ephemeralos.yaml"
    path.write_text("runner:\n  sandbox_quota: 7\n", encoding="utf-8")
    monkeypatch.setenv("EPHEMERALOS_RUN_HEAVY_LIVE_E2E", "1")
    monkeypatch.setenv("EPHEMERALOS_RUN_CAPACITY_LIVE_E2E", "1")
    monkeypatch.setenv("EOS_SWEEVO_REUSE_SANDBOX", "1")
    monkeypatch.setenv("EOS_SWEEVO_FORCE_FRESH_SANDBOX", "1")
    monkeypatch.setenv("EOS__RUNNER__LIVE_E2E__HEAVY_ENABLED", "true")
    monkeypatch.setenv("EOS__RUNNER__LIVE_E2E__CAPACITY_ENABLED", "true")
    monkeypatch.setenv("EOS__RUNNER__SANDBOX_REUSE_MODE", "force_fresh")
    monkeypatch.setenv("EOS_SWEEVO_SANDBOX_QUOTA", "9")

    cfg = load_central_config(path, dotenv_path=tmp_path / ".env")

    assert cfg.runner.live_e2e.heavy_enabled is False
    assert cfg.runner.live_e2e.capacity_enabled is False
    assert cfg.runner.sandbox_reuse_mode == "fresh"
    assert cfg.runner.sandbox_quota == 9


def test_dotenv_file_is_not_loaded_by_central_config(
    tmp_path: Path,
    clean_config_env: None,
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "EPHEMERALOS_DATABASE_URL=postgresql://dotenv/db\nDAYTONA_API_KEY=daytona-dotenv-key\n",
        encoding="utf-8",
    )

    cfg = load_central_config(tmp_path / "missing.yaml", dotenv_path=dotenv_path)

    assert cfg.database.url == "sqlite:///./.ephemeralos/ephemeralos.db"
    assert cfg.sandbox.daytona.api_key == ""


def test_override_central_config_scopes_active_config() -> None:
    cfg = CentralConfig(database=DatabaseConfig(url="sqlite:///override.db"))

    with override_central_config(cfg):
        assert get_central_config() is cfg
