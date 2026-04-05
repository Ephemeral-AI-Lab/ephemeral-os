"""Shared test fixtures."""

from __future__ import annotations

import sys
import pytest
from pathlib import Path


import types

_src_root = Path(__file__).resolve().parents[1] / "src"
if str(_src_root) not in sys.path:
    sys.path.insert(0, str(_src_root))

# Register "ephemeralos" as a namespace package pointing to backend/src so that
# both bare imports (e.g. ``from db.base import Base``) and qualified imports
# (e.g. ``from ephemeralos.db.base import Base``) work in test code.
if "ephemeralos" not in sys.modules:
    _ns = types.ModuleType("ephemeralos")
    _ns.__path__ = [str(_src_root)]
    sys.modules["ephemeralos"] = _ns
else:
    _pkg = sys.modules["ephemeralos"]
    _pkg_path = list(getattr(_pkg, "__path__", []))
    if str(_src_root) not in _pkg_path:
        _pkg_path.append(str(_src_root))
        _pkg.__path__ = _pkg_path


@pytest.fixture(autouse=True)
def _clear_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from host API credentials."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
