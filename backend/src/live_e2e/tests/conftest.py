"""pytest conftest — re-exports fixtures for live_e2e tests."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[4]
load_dotenv(_REPO_ROOT / ".env", override=False)

pytest_plugins = ["live_e2e.fixtures"]
