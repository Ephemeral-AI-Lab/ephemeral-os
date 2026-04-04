"""Keybinding resolution."""

from __future__ import annotations

from ephemeralos.keybindings.default_bindings import DEFAULT_KEYBINDINGS


def resolve_keybindings(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Merge user overrides over the default keybindings."""
    resolved = dict(DEFAULT_KEYBINDINGS)
    if overrides:
        resolved.update(overrides)
    return resolved
