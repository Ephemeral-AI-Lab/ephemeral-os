"""Walk ``plugins/catalog/*/plugin.md`` and parse each manifest.

Discovery is deterministic (sorted by plugin name) and silently ignores
folders without a ``plugin.md``. All other validation errors surface from
:mod:`plugins.core.manifest`. Duplicate plugin names raise loudly.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from plugins.core.manifest import (
    PluginManifest,
    PluginManifestError,
    parse_plugin_manifest,
)

__all__ = [
    "DEFAULT_CATALOG_DIR",
    "DuplicatePluginError",
    "default_catalog_dir",
    "discover_plugins",
]


class DuplicatePluginError(PluginManifestError):
    """Raised when two catalog folders declare the same plugin name."""


DEFAULT_CATALOG_DIR: Path = (
    Path(__file__).resolve().parent.parent / "catalog"
).resolve()


def default_catalog_dir() -> Path:
    """Return the absolute path of the bundled catalog directory."""
    return DEFAULT_CATALOG_DIR


def discover_plugins(catalog_dir: Path | None = None) -> list[PluginManifest]:
    """Discover and parse every plugin under *catalog_dir*."""
    base = (catalog_dir or DEFAULT_CATALOG_DIR).resolve()
    if not base.is_dir():
        return []

    manifests: dict[str, PluginManifest] = {}
    for child in _candidate_dirs(base.iterdir()):
        manifest_path = child / "plugin.md"
        if not manifest_path.is_file():
            continue
        manifest = parse_plugin_manifest(child)
        if manifest.name in manifests:
            existing = manifests[manifest.name]
            raise DuplicatePluginError(
                f"plugin name {manifest.name!r} declared in both "
                f"{existing.source_dir} and {manifest.source_dir}"
            )
        manifests[manifest.name] = manifest
    return sorted(manifests.values(), key=lambda m: m.name)


def _candidate_dirs(entries: Iterable[Path]) -> list[Path]:
    return sorted(
        (
            entry
            for entry in entries
            if entry.is_dir()
            and not entry.name.startswith(".")
            and entry.name != "__pycache__"
        ),
        key=lambda entry: entry.name,
    )
