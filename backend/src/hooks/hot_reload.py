"""Best-effort hot reloading for settings-backed hooks."""

from __future__ import annotations

from pathlib import Path

from config import load_settings
from hooks.loader import HookRegistry, load_hook_registry


class HookReloader:
    """Reload hook definitions when the settings file changes."""

    def __init__(self, settings_path: Path) -> None:
        self._settings_path = settings_path
        self._last_mtime_ns = -1
        self._registry = HookRegistry()

    def current_registry(self) -> HookRegistry:
        """Return the latest registry, reloading if needed."""
        try:
            stat = self._settings_path.stat()
        except FileNotFoundError:
            self._registry = HookRegistry()
            self._last_mtime_ns = -1
            return self._registry

        if stat.st_mtime_ns != self._last_mtime_ns:
            self._last_mtime_ns = stat.st_mtime_ns
            self._registry = load_hook_registry(load_settings(self._settings_path))
        return self._registry
