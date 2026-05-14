"""Daytona SDK credentials — API key, URL, and target resolution."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import dotenv_values


def _find_project_root(start: Path) -> Path:
    for candidate in (start.parent, *start.parents):
        if (candidate / "pyproject.toml").is_file() or (candidate / ".git").exists():
            return candidate
    return start.parents[6]


_PROJECT_ROOT = _find_project_root(Path(__file__).resolve())
_DOTENV_PATH = _PROJECT_ROOT / ".env"


def load_credentials() -> tuple[str, str, str]:
    dotenv_map = _load_dotenv_values()

    api_key = _credential_value("DAYTONA_API_KEY", dotenv_map)
    api_url = _credential_value("DAYTONA_API_URL", dotenv_map)
    target = _credential_value("DAYTONA_TARGET", dotenv_map)

    return api_key, api_url, target


def load_required_credentials(
    *,
    unavailable_cls: type[Exception],
    not_configured_message: str,
) -> tuple[str, str, str]:
    """Load credentials and raise the caller-specific exception if missing."""
    api_key, api_url, target = load_credentials()
    if not api_key or not api_url:
        raise unavailable_cls(not_configured_message)
    return api_key, api_url, target


def build_sdk_client(
    factory_name: str,
    *,
    api_key: str,
    api_url: str,
    target: str,
    unavailable_cls: type[Exception],
    not_installed_message: str,
) -> Any:
    """Import the Daytona SDK factory and build a configured client.

    ``factory_name`` must be ``"Daytona"`` or ``"AsyncDaytona"``. Cache
    storage is owned by the caller — this helper only builds a fresh
    instance.
    """
    try:
        import daytona_sdk
    except ImportError as exc:
        raise unavailable_cls(not_installed_message) from exc
    try:
        factory = getattr(daytona_sdk, factory_name)
        config_cls = daytona_sdk.DaytonaConfig
    except AttributeError as exc:
        raise unavailable_cls(not_installed_message) from exc
    cfg_kwargs: dict[str, str] = {"api_key": api_key, "api_url": api_url}
    if target:
        cfg_kwargs["target"] = target
    return factory(config_cls(**cfg_kwargs))


def _credential_value(
    env_name: str,
    dotenv_map: dict[str, str],
) -> str:
    return os.environ.get(env_name, "").strip() or dotenv_map.get(env_name, "")


def _load_dotenv_values() -> dict[str, str]:
    return {
        str(key): str(value).strip()
        for key, value in dotenv_values(_DOTENV_PATH).items()
        if key and value is not None and str(value).strip()
    }


__all__ = ["build_sdk_client", "load_credentials", "load_required_credentials"]
