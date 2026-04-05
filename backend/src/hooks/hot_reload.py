"""Best-effort hot reloading for settings-backed hooks."""

from __future__ import annotations

from pathlib import Path

from hooks.loader import HookRegistry


class HookReloader:
    """Reload hook definitions when the settings file changes."""

    def __init__(self, settings_path: Path) -> None:
        self._settings_path = settings_path
        self._last_mtime_ns = -1
        self._registry = HookRegistry()
