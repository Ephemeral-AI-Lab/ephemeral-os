"""Tests for config.settings (non-model config)."""

from __future__ import annotations

import json
from pathlib import Path

from config.settings import Settings, load_settings, save_settings


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


class TestLoadSaveSettings:
    def test_load_missing_file_returns_defaults(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("EPHEMERALOS_DATABASE_URL", raising=False)
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

    def test_load_strips_legacy_model_fields(self, tmp_path: Path, monkeypatch):
        """Legacy model/api_key/etc. in settings.json are silently dropped."""
        monkeypatch.delenv("EPHEMERALOS_DATABASE_URL", raising=False)
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "model": "legacy",
                    "api_key": "legacy",
                    "base_url": "legacy",
                    "api_format": "legacy",
                    "max_tokens": 999,
                    "verbose": True,
                }
            )
        )
        s = load_settings(path)
        assert s.verbose is True
        assert not hasattr(s, "model")
        assert not hasattr(s, "api_key")

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
