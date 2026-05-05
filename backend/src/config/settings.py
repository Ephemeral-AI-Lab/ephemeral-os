"""Settings model and loading logic for EphemeralOS.

Model/LLM configuration lives exclusively in the ``model_registrations``
DB table — see :mod:`config.model_config`. This module owns only the
provider-neutral non-model settings (system prompt, database, UI).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import BaseModel, Field

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DOTENV_PATH = _PROJECT_ROOT / ".env"


class DatabaseSettings(BaseModel):
    """PostgreSQL database configuration."""

    url: str = ""
    pool_pre_ping: bool = True
    pool_size: int = 5
    max_overflow: int = 10
    echo: bool = False


class SandboxSettings(BaseModel):
    """Sandbox provider defaults."""

    default_image: str = ""
    default_snapshot: str = ""


class Settings(BaseModel):
    """Main settings model for EphemeralOS (non-model config)."""

    # Behavior
    system_prompt: str | None = None

    # Database
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)

    # Sandbox
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)

    # UI
    theme: str = "default"
    fast_mode: bool = False
    effort: str = "medium"
    passes: int = 1
    verbose: bool = False

    def merge_cli_overrides(self, **overrides: Any) -> Settings:
        """Return a new Settings with CLI overrides applied (non-None values only)."""
        updates = {k: v for k, v in overrides.items() if v is not None}
        return self.model_copy(update=updates)


def _apply_env_overrides(settings: Settings) -> Settings:
    """Apply supported environment variable overrides over loaded settings."""
    updates: dict[str, Any] = {}
    dotenv_values_map = {
        str(key): str(value).strip()
        for key, value in dotenv_values(_DOTENV_PATH).items()
        if key and value is not None and str(value).strip()
    }

    def _get_override(name: str) -> str:
        return os.environ.get(name, "").strip() or dotenv_values_map.get(name, "")

    database_url = _get_override("EPHEMERALOS_DATABASE_URL")
    if database_url:
        db = settings.database.model_copy(update={"url": database_url})
        updates["database"] = db

    sandbox_default_image = _get_override("EPHEMERALOS_SANDBOX_DEFAULT_IMAGE")
    sandbox_default_snapshot = _get_override("EPHEMERALOS_SANDBOX_DEFAULT_SNAPSHOT")
    if sandbox_default_image:
        sandbox = settings.sandbox.model_copy(
            update={"default_image": sandbox_default_image}
        )
        updates["sandbox"] = sandbox
    if sandbox_default_snapshot:
        sandbox = updates.get("sandbox", settings.sandbox).model_copy(
            update={"default_snapshot": sandbox_default_snapshot}
        )
        updates["sandbox"] = sandbox

    if not updates:
        return settings
    return settings.model_copy(update=updates)


def load_settings(config_path: Path | None = None) -> Settings:
    """Load settings from config file, merging with defaults."""
    if config_path is None:
        from config.paths import get_config_file_path

        config_path = get_config_file_path()

    if config_path.exists():
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        return _apply_env_overrides(Settings.model_validate(raw))

    return _apply_env_overrides(Settings())


def save_settings(settings: Settings, config_path: Path | None = None) -> None:
    """Persist settings to the config file."""
    if config_path is None:
        from config.paths import get_config_file_path

        config_path = get_config_file_path()

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        settings.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
