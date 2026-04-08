"""Settings model and loading logic for EphemeralOS.

Model/LLM configuration lives exclusively in the ``model_registrations``
DB table — see :mod:`config.model_config`. This module owns only the
non-model settings (system prompt, hooks, database, daytona, UI).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from hooks.schemas import HookDefinition


class DatabaseSettings(BaseModel):
    """PostgreSQL database configuration."""

    url: str = ""
    pool_pre_ping: bool = True
    pool_size: int = 5
    max_overflow: int = 10
    echo: bool = False


class Settings(BaseModel):
    """Main settings model for EphemeralOS (non-model config)."""

    # Behavior
    system_prompt: str | None = None
    hooks: dict[str, list[HookDefinition]] = Field(default_factory=dict)

    # Database
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)

    # Daytona sandbox
    daytona_api_key: str = ""
    daytona_api_url: str = ""
    daytona_target: str = ""

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

    database_url = os.environ.get("EPHEMERALOS_DATABASE_URL")
    if database_url:
        db = settings.database.model_copy(update={"url": database_url})
        updates["database"] = db

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
        # Drop legacy model fields if they're still present on disk.
        for legacy in ("model", "api_key", "max_tokens", "base_url", "api_format"):
            raw.pop(legacy, None)
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
