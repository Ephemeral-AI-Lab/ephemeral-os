"""Tests for sandbox.credentials."""

from __future__ import annotations


class TestLoadCredentials:
    def test_loads_from_env(self, monkeypatch):
        monkeypatch.setenv("DAYTONA_API_KEY", "key-from-env")
        monkeypatch.setenv("DAYTONA_API_URL", "https://url-from-env")
        monkeypatch.setenv("DAYTONA_TARGET", "target-from-env")
        monkeypatch.setattr(
            "sandbox.provider.daytona.client._load_dotenv_values",
            lambda: {},
        )

        from sandbox.provider.daytona.client import load_credentials

        key, url, target = load_credentials()
        assert key == "key-from-env"
        assert url == "https://url-from-env"
        assert target == "target-from-env"

    def test_env_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("DAYTONA_API_KEY", "env-key")
        monkeypatch.setenv("DAYTONA_API_URL", "https://env-url")
        monkeypatch.setattr(
            "sandbox.provider.daytona.client._load_dotenv_values",
            lambda: {
                "DAYTONA_API_KEY": "dotenv-key",
                "DAYTONA_API_URL": "https://dotenv-url",
            },
        )

        from sandbox.provider.daytona.client import load_credentials

        key, url, target = load_credentials()
        assert key == "env-key"
        assert url == "https://env-url"

    def test_dotenv_fallback(self, monkeypatch):
        monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
        monkeypatch.delenv("DAYTONA_API_URL", raising=False)
        monkeypatch.delenv("DAYTONA_TARGET", raising=False)
        monkeypatch.setattr(
            "sandbox.provider.daytona.client._load_dotenv_values",
            lambda: {
                "DAYTONA_API_KEY": "dotenv-key",
                "DAYTONA_API_URL": "https://dotenv-url",
                "DAYTONA_TARGET": "dotenv-target",
            },
        )

        from sandbox.provider.daytona.client import load_credentials

        key, url, target = load_credentials()
        assert key == "dotenv-key"
        assert url == "https://dotenv-url"
        assert target == "dotenv-target"

    def test_returns_empty_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
        monkeypatch.delenv("DAYTONA_API_URL", raising=False)
        monkeypatch.delenv("DAYTONA_TARGET", raising=False)
        monkeypatch.setattr(
            "sandbox.provider.daytona.client._load_dotenv_values",
            lambda: {},
        )

        from sandbox.provider.daytona.client import load_credentials

        key, url, target = load_credentials()
        assert key == ""
        assert url == ""
        assert target == ""

    def test_project_root_lookup_uses_marker_file(self, tmp_path):
        from sandbox.provider.daytona.client import _find_project_root

        root = tmp_path / "repo"
        nested = root / "backend" / "src" / "sandbox"
        nested.mkdir(parents=True)
        (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

        assert _find_project_root(nested / "credentials.py") == root

    def test_project_root_lookup_falls_back_to_start_for_shallow_paths(self, tmp_path):
        from sandbox.provider.daytona.client import _find_project_root

        start = tmp_path / "client.py"

        assert _find_project_root(start) == start
