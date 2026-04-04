"""Paths for persistent project memory."""

from __future__ import annotations

from hashlib import sha1
from pathlib import Path

from ephemeralos.config.paths import get_data_dir


def get_project_memory_dir(cwd: str | Path) -> Path:
    """Return the persistent memory directory for a project."""
    path = Path(cwd).resolve()
    digest = sha1(str(path).encode("utf-8")).hexdigest()[:12]
    memory_dir = get_data_dir() / "memory" / f"{path.name}-{digest}"
    memory_dir.mkdir(parents=True, exist_ok=True)
    return memory_dir


def get_memory_entrypoint(cwd: str | Path) -> Path:
    """Return the project memory entrypoint file."""
    return get_project_memory_dir(cwd) / "MEMORY.md"
