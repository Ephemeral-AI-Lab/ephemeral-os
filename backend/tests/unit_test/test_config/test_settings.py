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
        assert s.database.url == "sqlite:///./.ephemeralos/ephemeralos.db"
        assert s.sandbox.daytona.default_image == ""
        assert s.sandbox.daytona.default_snapshot == ""
        assert s.sandbox.docker.default_snapshot == ""

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

    def test_legacy_control_plane_dropped_columns_include_obsolete_topology_fields(self):
        legacy_tasks_table = "task_center_" "tasks"
        legacy_runs_table = "task_center_" "runs"
        assert _DROPPED_COLUMNS[legacy_tasks_table] >= {
            "spec",
            "title",
            "summary",
            "system_prompt",
            "parent_id",
            "closes_for",
            "children",
            "evaluator_id",
            "acceptance_criteria",
            "handoff_note",
            "user_prompt",
        }
        assert _DROPPED_COLUMNS[legacy_runs_table] >= {
            "root_task_id",
        }


class TestLoadSaveSettings:
    def test_load_missing_file_returns_defaults(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("EPHEMERALOS_DATABASE_URL", raising=False)
        monkeypatch.delenv("EPHEMERALOS_SANDBOX_DEFAULT_IMAGE", raising=False)
        monkeypatch.delenv("EPHEMERALOS_SANDBOX_DEFAULT_SNAPSHOT", raising=False)
        path = tmp_path / "nonexistent.json"
        s = load_settings(path)
        assert s == Settings()

    def test_load_existing_file(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("EPHEMERALOS_DATABASE_URL", raising=False)
        monkeypatch.delenv("EPHEMERALOS_SANDBOX_DEFAULT_IMAGE", raising=False)
        monkeypatch.delenv("EPHEMERALOS_SANDBOX_DEFAULT_SNAPSHOT", raising=False)
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"verbose": True, "fast_mode": True}))
        s = load_settings(path)
        assert s.verbose is True
        assert s.fast_mode is True

    def test_load_existing_file_with_sandbox_defaults(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("EPHEMERALOS_DATABASE_URL", raising=False)
        monkeypatch.delenv("EPHEMERALOS_SANDBOX_DEFAULT_IMAGE", raising=False)
        monkeypatch.delenv("EPHEMERALOS_SANDBOX_DEFAULT_SNAPSHOT", raising=False)
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "sandbox": {
                        "docker": {
                            "default_snapshot": "docker-snapshot",
                        },
                        "daytona": {
                            "default_image": "ghcr.io/example/default:latest",
                            "default_snapshot": "daytona-snapshot",
                        },
                    },
                }
            )
        )
        s = load_settings(path)
        assert s.sandbox.docker.default_snapshot == "docker-snapshot"
        assert s.sandbox.daytona.default_image == "ghcr.io/example/default:latest"
        assert s.sandbox.daytona.default_snapshot == "daytona-snapshot"

    def test_save_and_load_roundtrip(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("EPHEMERALOS_DATABASE_URL", raising=False)
        monkeypatch.delenv("EPHEMERALOS_SANDBOX_DEFAULT_IMAGE", raising=False)
        monkeypatch.delenv("EPHEMERALOS_SANDBOX_DEFAULT_SNAPSHOT", raising=False)
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

    def test_sandbox_default_image_env_override(self, tmp_path: Path, monkeypatch):
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "sandbox": {
                        "daytona": {
                            "default_image": "ghcr.io/example/file:latest",
                        },
                    },
                }
            )
        )
        monkeypatch.setenv(
            "EPHEMERALOS_SANDBOX_DEFAULT_IMAGE",
            "ghcr.io/example/env:latest",
        )
        s = load_settings(path)
        assert s.sandbox.daytona.default_image == "ghcr.io/example/env:latest"

    def test_sandbox_default_snapshot_env_override(self, tmp_path: Path, monkeypatch):
        path = tmp_path / "settings.json"
        path.write_text(
            json.dumps(
                {
                    "sandbox": {
                        "docker": {
                            "default_snapshot": "docker-file-snapshot",
                        },
                        "daytona": {
                            "default_snapshot": "daytona-file-snapshot",
                        },
                    },
                }
            )
        )
        monkeypatch.setenv(
            "EPHEMERALOS_SANDBOX_DEFAULT_SNAPSHOT",
            "env-snapshot",
        )
        s = load_settings(path)
        assert s.sandbox.docker.default_snapshot == "env-snapshot"
        assert s.sandbox.daytona.default_snapshot == "env-snapshot"
