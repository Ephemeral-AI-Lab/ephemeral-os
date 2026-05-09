"""Unit tests for plugins.core.discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.core.discovery import (
    DuplicatePluginError,
    discover_plugins,
)


def _seed_plugin(catalog: Path, name: str) -> Path:
    plugin_dir = catalog / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.md").write_text(
        f"---\nname: {name}\ndescription: {name} plugin\ntools:\n"
        f"  - name: {name}.run\n    module: tools/run.py\n---\n",
        encoding="utf-8",
    )
    (plugin_dir / "tools").mkdir()
    (plugin_dir / "tools" / "run.py").write_text("x = 1\n", encoding="utf-8")
    return plugin_dir


def test_discover_empty_catalog_returns_empty_list(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    assert discover_plugins(catalog) == []


def test_discover_missing_catalog_dir_returns_empty_list(tmp_path: Path) -> None:
    assert discover_plugins(tmp_path / "nope") == []


def test_discover_finds_plugins_sorted_by_name(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    _seed_plugin(catalog, "zeta")
    _seed_plugin(catalog, "alpha")
    _seed_plugin(catalog, "mu")

    manifests = discover_plugins(catalog)
    assert [m.name for m in manifests] == ["alpha", "mu", "zeta"]


def test_discover_ignores_folders_without_plugin_md(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    _seed_plugin(catalog, "alpha")
    (catalog / "not_a_plugin").mkdir()
    (catalog / "not_a_plugin" / "README.md").write_text(
        "no manifest here\n", encoding="utf-8"
    )
    (catalog / "__pycache__").mkdir()
    (catalog / ".hidden").mkdir()

    manifests = discover_plugins(catalog)
    assert [m.name for m in manifests] == ["alpha"]


def test_discover_rejects_duplicate_plugin_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The name-vs-dir check normally prevents two folders from producing the
    same parsed name. Exercise the duplicate-detection branch by patching
    parse_plugin_manifest to return the same name for two folders."""
    from plugins.core import discovery as discovery_mod
    from plugins.core.manifest import PluginManifest

    catalog = tmp_path / "catalog"
    catalog.mkdir()
    folder_a = catalog / "folder_a"
    folder_b = catalog / "folder_b"
    folder_a.mkdir()
    folder_b.mkdir()
    (folder_a / "plugin.md").write_text("placeholder\n", encoding="utf-8")
    (folder_b / "plugin.md").write_text("placeholder\n", encoding="utf-8")

    def fake_parse(path: Path) -> PluginManifest:
        return PluginManifest(
            name="dup",
            description="x",
            tools=(),
            setup=None,
            runtime=None,
            source_dir=path.resolve(),
            body="",
        )

    monkeypatch.setattr(discovery_mod, "parse_plugin_manifest", fake_parse)
    with pytest.raises(DuplicatePluginError, match="declared in both"):
        discover_plugins(catalog)
