"""Atomic write helpers shared by the audit recorder and the performance report.

Both writers go through tmp-file + ``os.replace`` so partial writes are never
observable by readers (a JSON file is either the previous snapshot or the new
one — never a torn write).

Two json variants are provided so callers can pick the formatting that already
matches their on-disk artifacts:

- ``atomic_write_json(path, data)`` — compact, no trailing newline. Used by the
  recorder for high-frequency artifacts (``run.json``, ``metrics.json``,
  per-goal / per-trial JSON).
- ``atomic_write_pretty_json(path, data)`` — ``indent=2`` + trailing newline.
  Used by the performance report for human-/git-friendly artifacts.

``atomic_write_text(path, data)`` writes a pre-rendered string the same way.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    """Write ``data`` as compact JSON via tmp + ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(data, default=str, ensure_ascii=False)
    tmp_path.write_text(encoded, encoding="utf-8")
    os.replace(tmp_path, path)


def atomic_write_pretty_json(path: Path, data: Mapping[str, Any]) -> None:
    """Write ``data`` as indented JSON (indent=2) with a trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(data, default=str, ensure_ascii=False, indent=2)
    tmp_path.write_text(encoded + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def atomic_write_text(path: Path, data: str) -> None:
    """Write a pre-rendered string via tmp + ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(data, encoding="utf-8")
    os.replace(tmp_path, path)


__all__ = [
    "atomic_write_json",
    "atomic_write_pretty_json",
    "atomic_write_text",
]
