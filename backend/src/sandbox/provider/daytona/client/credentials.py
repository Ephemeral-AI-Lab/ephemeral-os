"""Daytona SDK credentials — API key, URL, and target resolution."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

_PROJECT_ROOT = Path(__file__).resolve().parents[6]
_DOTENV_PATH = _PROJECT_ROOT / ".env"


def has_credentials() -> bool:
    api_key, api_url, _ = load_credentials()
    return bool(api_key and api_url)


def load_credentials() -> tuple[str, str, str]:
    dotenv_map = _load_dotenv_values()

    api_key = _credential_value("DAYTONA_API_KEY", dotenv_map)
    api_url = _credential_value("DAYTONA_API_URL", dotenv_map)
    target = _credential_value("DAYTONA_TARGET", dotenv_map)

    return api_key, api_url, target


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


__all__ = ["has_credentials", "load_credentials"]
