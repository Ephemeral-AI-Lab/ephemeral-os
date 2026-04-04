"""Keybinding file parsing."""

from __future__ import annotations

import json


def parse_keybindings(text: str) -> dict[str, str]:
    """Parse a JSON keybinding mapping."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("keybindings file must be a JSON object")
    parsed: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("keybindings keys and values must be strings")
        parsed[key] = value
    return parsed
