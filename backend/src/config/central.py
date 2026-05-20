"""Central typed configuration for EphemeralOS."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from config.loader import (
    EnvConfigSource,
    YamlConfigSource,
    config_source_paths,
)
from config.sections import (
    DatabaseConfig,
    EngineConfig,
    ProvidersConfig,
    RunnerConfig,
    SandboxConfig,
)


class CentralConfig(BaseSettings):
    """Composition root for all runtime-tunable EphemeralOS config."""

    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    runner: RunnerConfig = Field(default_factory=RunnerConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)

    model_config = SettingsConfigDict(
        extra="forbid",
        env_nested_delimiter="__",
        env_prefix="EOS__",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del env_settings, dotenv_settings
        return (
            init_settings,
            EnvConfigSource(settings_cls),
            YamlConfigSource(settings_cls),
            file_secret_settings,
        )

    def merge_cli_overrides(self, **overrides: Any) -> CentralConfig:
        """Return a shallow copy with non-None top-level overrides applied."""
        return self.model_copy(
            update={key: value for key, value in overrides.items() if value is not None}
        )


_CENTRAL_CONFIG_OVERRIDE: ContextVar[CentralConfig | None] = ContextVar(
    "ephemeralos_central_config_override",
    default=None,
)


def load_central_config(
    config_path: Path | None = None,
    *,
    dotenv_path: Path | None = None,
) -> CentralConfig:
    """Load central config with precedence: defaults < YAML < env < init."""
    with config_source_paths(config_path=config_path, dotenv_path=dotenv_path):
        return CentralConfig()


def get_central_config() -> CentralConfig:
    """Return the active central config, lazily loading the default when unset."""
    override = _CENTRAL_CONFIG_OVERRIDE.get()
    if override is not None:
        return override
    return load_central_config()


@contextmanager
def override_central_config(config: CentralConfig) -> Iterator[CentralConfig]:
    """Temporarily install a central config for code paths under test."""
    token = _CENTRAL_CONFIG_OVERRIDE.set(config)
    try:
        yield config
    finally:
        _CENTRAL_CONFIG_OVERRIDE.reset(token)
