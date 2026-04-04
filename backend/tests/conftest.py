"""Shared test fixtures."""

from __future__ import annotations

import sys
import pytest
import types
from pathlib import Path


# Ensure test imports using the project package name "ephemeralos" continue to work
# when tests live under backend/tests and the source is located at backend/src.
_src_root = Path(__file__).resolve().parents[1] / "src"
if str(_src_root) not in sys.path:
    sys.path.insert(0, str(_src_root))

if "ephemeralos" not in sys.modules:
    ephemeralos = types.ModuleType("ephemeralos")
    ephemeralos.__path__ = [str(_src_root)]
    sys.modules["ephemeralos"] = ephemeralos
else:
    package = sys.modules["ephemeralos"]
    package_path = list(getattr(package, "__path__", []))
    if str(_src_root) not in package_path:
        package_path.append(str(_src_root))
        package.__path__ = package_path


@pytest.fixture(autouse=True)
def _clear_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from host API credentials."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
