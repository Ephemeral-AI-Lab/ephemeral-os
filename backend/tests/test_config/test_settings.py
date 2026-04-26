"""Tests for config.settings (non-model config)."""

from __future__ import annotations

import json
from pathlib import Path

from config.settings import Settings, load_settings, save_settings
from db.engine import _DROPPED_COLUMNS


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.fast_mode is False
        assert s.effort == "medium"
        assert s.theme == "default"
        assert s.verbose is False
        assert s.database.url == ""

    def test_merge_cli_overrides(self):
        s = Settings()
        updated = s.merge_cli_overrides(verbose=True, system_prompt=None)
        assert updated.verbose is True
        assert updated.system_prompt is None

    def test_merge_cli_overrides_returns_new_instance(self):
        s = Settings()
        updated = s.merge_cli_overrides(verbose=True)
        assert s is not updated
        assert s.verbose is False
        assert updated.verbose is True

    def test_task_center_dropped_columns_include_obsolete_topology_fields(self):
        assert _DROPPED_COLUMNS["task_center_tasks"] >= {
            "spec",
            "title",
            "summary",
            "parent_id",
            "closes_for",
            "children",
            "evaluator_id",
            "acceptance_criteria",
            "handoff_note",
        }


class TestLoadSaveSettings:
    def test_load_missing_file_returns_defaults(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("EPHEMERALOS_DATABASE_URL", raising=False)
        monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
        monkeypatch.delenv("DAYTONA_API_URL", raising=False)
        monkeypatch.delenv("DAYTONA_TARGET", raising=False)
        monkeypatch.setattr("config.settings._DOTENV_PATH", tmp_path / ".env")
        path = tmp_path / "nonexistent.json"
        s = load_settings(path)
        assert s == Settings()

    def test_load_existing_file(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("EPHEMERALOS_DATABASE_URL", raising=False)
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"verbose": True, "fast_mode": True}))
        s = load_settings(path)
        assert s.verbose is True
        assert s.fast_mode is True

    def test_save_and_load_roundtrip(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("EPHEMERALOS_DATABASE_URL", raising=False)
        path = tmp_path / "settings.json"
        original = Settings(verbose=True, effort="high")
        save_settings(original, path)
        loaded = load_settings(path)
        assert loaded.verbose == original.verbose
        assert loaded.effort == original.effort

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        path = tmp_path / "deep" / "nested" / "settings.json"
        save_settings(Settings(), path)
        assert path.exists()

    def test_database_url_env_override(self, tmp_path: Path, monkeypatch):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({}))
        monkeypatch.setenv("EPHEMERALOS_DATABASE_URL", "postgresql://env/override")
        s = load_settings(path)
        assert s.database.url == "postgresql://env/override"

    def test_daytona_env_overrides(self, tmp_path: Path, monkeypatch):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({}))
        monkeypatch.setenv("DAYTONA_API_KEY", "env-key")
        monkeypatch.setenv("DAYTONA_API_URL", "https://env-url")
        monkeypatch.setenv("DAYTONA_TARGET", "env-target")

        s = load_settings(path)

        assert s.daytona_api_key == "env-key"
        assert s.daytona_api_url == "https://env-url"
        assert s.daytona_target == "env-target"

    def test_daytona_dotenv_fallback(self, tmp_path: Path, monkeypatch):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({}))
        dotenv_path = tmp_path / ".env"
        dotenv_path.write_text(
            "DAYTONA_API_KEY=dotenv-key\n"
            "DAYTONA_API_URL=https://dotenv-url\n"
            "DAYTONA_TARGET=dotenv-target\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
        monkeypatch.delenv("DAYTONA_API_URL", raising=False)
        monkeypatch.delenv("DAYTONA_TARGET", raising=False)
        monkeypatch.setattr("config.settings._DOTENV_PATH", dotenv_path)

        s = load_settings(path)

        assert s.daytona_api_key == "dotenv-key"
        assert s.daytona_api_url == "https://dotenv-url"
        assert s.daytona_target == "dotenv-target"

    def test_daytona_env_overrides_take_precedence_over_dotenv(self, tmp_path: Path, monkeypatch):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({}))
        dotenv_path = tmp_path / ".env"
        dotenv_path.write_text(
            "DAYTONA_API_KEY=dotenv-key\n"
            "DAYTONA_API_URL=https://dotenv-url\n"
            "DAYTONA_TARGET=dotenv-target\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("config.settings._DOTENV_PATH", dotenv_path)
        monkeypatch.setenv("DAYTONA_API_KEY", "env-key")
        monkeypatch.setenv("DAYTONA_API_URL", "https://env-url")
        monkeypatch.setenv("DAYTONA_TARGET", "env-target")

        s = load_settings(path)

        assert s.daytona_api_key == "env-key"
        assert s.daytona_api_url == "https://env-url"
        assert s.daytona_target == "env-target"
