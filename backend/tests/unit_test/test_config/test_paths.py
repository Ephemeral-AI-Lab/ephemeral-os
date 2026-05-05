"""Tests for config.paths."""

from __future__ import annotations

from pathlib import Path

from config.paths import (
    get_config_dir,
    get_config_file_path,
    get_data_dir,
    get_logs_dir,
)


def test_get_config_dir_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EPHEMERALOS_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    config_dir = get_config_dir()
    assert config_dir == tmp_path / ".ephemeralos"
    assert config_dir.is_dir()


def test_get_config_dir_env_override(tmp_path: Path, monkeypatch):
    custom = tmp_path / "custom_config"
    monkeypatch.setenv("EPHEMERALOS_CONFIG_DIR", str(custom))
    config_dir = get_config_dir()
    assert config_dir == custom
    assert config_dir.is_dir()


def test_get_config_file_path(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EPHEMERALOS_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    path = get_config_file_path()
    assert path == tmp_path / ".ephemeralos" / "settings.json"


def test_get_data_dir_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EPHEMERALOS_CONFIG_DIR", raising=False)
    monkeypatch.delenv("EPHEMERALOS_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    data_dir = get_data_dir()
    assert data_dir == tmp_path / ".ephemeralos" / "data"
    assert data_dir.is_dir()


def test_get_data_dir_env_override(tmp_path: Path, monkeypatch):
    custom = tmp_path / "custom_data"
    monkeypatch.setenv("EPHEMERALOS_DATA_DIR", str(custom))
    data_dir = get_data_dir()
    assert data_dir == custom
    assert data_dir.is_dir()


def test_get_logs_dir_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EPHEMERALOS_CONFIG_DIR", raising=False)
    monkeypatch.delenv("EPHEMERALOS_LOGS_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    logs_dir = get_logs_dir()
    assert logs_dir == tmp_path / ".ephemeralos" / "logs"
    assert logs_dir.is_dir()


def test_get_logs_dir_env_override(tmp_path: Path, monkeypatch):
    custom = tmp_path / "custom_logs"
    monkeypatch.setenv("EPHEMERALOS_LOGS_DIR", str(custom))
    logs_dir = get_logs_dir()
    assert logs_dir == custom
    assert logs_dir.is_dir()
