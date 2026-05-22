"""Settings sources for the central EphemeralOS config loader."""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import PydanticBaseSettingsSource

from config.paths import get_central_config_file_path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

_CONFIG_PATH_OVERRIDE: ContextVar[Path | None] = ContextVar(
    "ephemeralos_config_path_override",
    default=None,
)

_EOS_PREFIX = "EOS__"

LegacyProcessor = tuple[tuple[str, ...], Any]


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


_LEGACY_ENV_MAP: dict[str, LegacyProcessor] = {
    "EPHEMERALOS_DATABASE_URL": (("database", "url"), str.strip),
    "EPHEMERALOS_SANDBOX_DEFAULT_IMAGE": (
        ("sandbox", "daytona", "default_image"),
        str.strip,
    ),
    "EPHEMERALOS_SANDBOX_TIMEOUT_SECONDS": (("sandbox", "timeout_s"), str.strip),
    "EPHEMERALOS_RUNTIME_CLIENT_TIMEOUT": (
        ("sandbox", "runtime_client_timeout_s"),
        str.strip,
    ),
    "EOS_SANDBOX_PROVIDER": (
        ("sandbox", "default_provider"),
        lambda value: value.strip().lower(),
    ),
    "EOS_DOCKER_DAEMON_TCP": (("sandbox", "docker", "daemon_tcp"), str.strip),
    "EOS_DOCKER_PRIVILEGED": (("sandbox", "docker", "privileged"), str.strip),
    "EOS_DOCKER_NO_PRIVILEGE": (("sandbox", "docker", "no_privilege"), str.strip),
    "EOS_DAEMON_TCP_HOST": (("sandbox", "daytona", "tcp_host"), str.strip),
    "EOS_DAEMON_TCP_PORT": (("sandbox", "daytona", "tcp_port"), str.strip),
    "DAYTONA_API_KEY": (("sandbox", "daytona", "api_key"), str.strip),
    "DAYTONA_API_URL": (("sandbox", "daytona", "api_url"), str.strip),
    "DAYTONA_TARGET": (("sandbox", "daytona", "target"), str.strip),
    "MINIMAX_BASE_URL": (("providers", "minimax", "base_url"), str.strip),
    "MINIMAX_MODEL": (("providers", "minimax", "model"), str.strip),
    "EPHEMERALOS_RUN_HEAVY_LIVE_E2E": (
        ("runner", "live_e2e", "heavy_enabled"),
        str.strip,
    ),
    "EPHEMERALOS_RUN_CAPACITY_LIVE_E2E": (
        ("runner", "live_e2e", "capacity_enabled"),
        str.strip,
    ),
    "EOS_SWEEVO_REAL_AGENT_MAX_DURATION_S": (
        ("runner", "live_e2e", "real_agent_max_duration_s"),
        str.strip,
    ),
    "EOS_SWEEVO_SANDBOX_QUOTA": (("runner", "sandbox_quota"), str.strip),
}


def _set_nested(target: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cursor = target
    for key in path[:-1]:
        child = cursor.setdefault(key, {})
        if not isinstance(child, dict):
            child = {}
            cursor[key] = child
        cursor = child
    cursor[path[-1]] = value


def _parse_complex_env_value(value: str) -> Any:
    stripped = value.strip()
    if stripped.startswith(("[", "{")):
        try:
            return yaml.safe_load(stripped)
        except yaml.YAMLError:
            return stripped
    return stripped


def _data_from_env(mapping: Mapping[str, str]) -> dict[str, Any]:
    data: dict[str, Any] = {}

    for name, raw in mapping.items():
        if name.startswith(_EOS_PREFIX):
            path = tuple(part.lower() for part in name[len(_EOS_PREFIX) :].split("__") if part)
            if path:
                _set_nested(data, path, _parse_complex_env_value(raw))

    for name, (path, processor) in _LEGACY_ENV_MAP.items():
        raw = mapping.get(name)
        if raw is None or raw.strip() == "":
            continue
        _set_nested(data, path, processor(raw))

    legacy_snapshot = mapping.get("EPHEMERALOS_SANDBOX_DEFAULT_SNAPSHOT")
    if legacy_snapshot is not None and legacy_snapshot.strip():
        snapshot = legacy_snapshot.strip()
        _set_nested(data, ("sandbox", "docker", "default_snapshot"), snapshot)
        _set_nested(data, ("sandbox", "daytona", "default_snapshot"), snapshot)

    sandbox = data.get("sandbox")
    if isinstance(sandbox, dict) and "provider" in sandbox:
        sandbox.setdefault("default_provider", sandbox.pop("provider"))

    if _truthy(mapping.get("EOS_SWEEVO_FORCE_FRESH_SANDBOX", "")):
        _set_nested(data, ("runner", "sandbox_reuse_mode"), "force_fresh")
    elif _truthy(mapping.get("EOS_SWEEVO_REUSE_SANDBOX", "")):
        _set_nested(data, ("runner", "sandbox_reuse_mode"), "reuse")

    return data


class YamlConfigSource(PydanticBaseSettingsSource):
    """Optional YAML config source for ``ephemeralos.yaml``."""

    def __call__(self) -> dict[str, Any]:
        path = _CONFIG_PATH_OVERRIDE.get() or get_central_config_file_path()
        if not path.exists():
            return {}
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ValueError(f"central config YAML must contain a mapping: {path}")
        return raw

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False


class EnvConfigSource(PydanticBaseSettingsSource):
    """Environment source for ``EOS__`` nested vars and retained legacy bindings."""

    def __call__(self) -> dict[str, Any]:
        return _data_from_env({key: str(value) for key, value in os.environ.items()})

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False


@contextmanager
def config_source_paths(
    *,
    config_path: Path | None = None,
    dotenv_path: Path | None = None,
):
    """Temporarily override loader input paths.

    ``dotenv_path`` is retained for API compatibility. Central config no longer
    reads ``.env``; callers that need overrides must export real process envs.
    """
    del dotenv_path
    config_token = _CONFIG_PATH_OVERRIDE.set(config_path)
    try:
        yield
    finally:
        _CONFIG_PATH_OVERRIDE.reset(config_token)
