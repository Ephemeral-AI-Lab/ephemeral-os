"""Helpers for managing memory files."""

from __future__ import annotations

from pathlib import Path
from re import sub

from ephemeralos.memory.paths import get_memory_entrypoint, get_project_memory_dir


def list_memory_files(cwd: str | Path) -> list[Path]:
    """List memory markdown files for the project."""
    memory_dir = get_project_memory_dir(cwd)
    return sorted(path for path in memory_dir.glob("*.md"))


def add_memory_entry(cwd: str | Path, title: str, content: str) -> Path:
    """Create a memory file and append it to MEMORY.md."""
    memory_dir = get_project_memory_dir(cwd)
    slug = sub(r"[^a-zA-Z0-9]+", "_", title.strip().lower()).strip("_") or "memory"
    path = memory_dir / f"{slug}.md"
    path.write_text(content.strip() + "\n", encoding="utf-8")

    entrypoint = get_memory_entrypoint(cwd)
    existing = entrypoint.read_text(encoding="utf-8") if entrypoint.exists() else "# Memory Index\n"
    if path.name not in existing:
        existing = existing.rstrip() + f"\n- [{title}]({path.name})\n"
        entrypoint.write_text(existing, encoding="utf-8")
    return path


def remove_memory_entry(cwd: str | Path, name: str) -> bool:
    """Delete a memory file and remove its index entry."""
    memory_dir = get_project_memory_dir(cwd)
    matches = [path for path in memory_dir.glob("*.md") if path.stem == name or path.name == name]
    if not matches:
        return False
    path = matches[0]
    if path.exists():
        path.unlink()

    entrypoint = get_memory_entrypoint(cwd)
    if entrypoint.exists():
        lines = [
            line
            for line in entrypoint.read_text(encoding="utf-8").splitlines()
            if path.name not in line
        ]
        entrypoint.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return True
