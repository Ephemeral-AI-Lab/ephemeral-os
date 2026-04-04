"""Load keybindings from config."""

from __future__ import annotations

from pathlib import Path

from ephemeralos.config.paths import get_config_dir
from ephemeralos.keybindings.parser import parse_keybindings
from ephemeralos.keybindings.resolver import resolve_keybindings


def get_keybindings_path() -> Path:
    """Return the user keybindings path."""
    return get_config_dir() / "keybindings.json"


def load_keybindings() -> dict[str, str]:
    """Load and merge keybindings."""
    path = get_keybindings_path()
    if not path.exists():
        return resolve_keybindings()
    return resolve_keybindings(parse_keybindings(path.read_text(encoding="utf-8")))
