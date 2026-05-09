"""Unit tests for plugins.core.manifest."""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.core.manifest import (
    PluginManifestError,
    parse_plugin_manifest,
)


def _write_plugin(
    tmp_path: Path,
    name: str,
    *,
    frontmatter: str,
    extra_files: dict[str, str] | None = None,
) -> Path:
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.md").write_text(
        f"---\n{frontmatter}---\n\n# {name}\n",
        encoding="utf-8",
    )
    for relative, contents in (extra_files or {}).items():
        target = plugin_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
    return plugin_dir


def _frontmatter(
    *,
    name: str = "demo",
    description: str = "demo plugin",
    tools: list[dict[str, str]] | None = None,
    setup: str | None = None,
    runtime: str | None = None,
) -> str:
    if tools is None:
        tools = [{"name": f"{name}.run", "module": "tools/run.py"}]
    lines = [f"name: {name}", f"description: {description}", "tools:"]
    for tool in tools:
        lines.append(f"  - name: {tool['name']}")
        lines.append(f"    module: {tool['module']}")
    if setup is not None:
        lines.append(f"setup: {setup}")
    if runtime is not None:
        lines.append(f"runtime: {runtime}")
    return "\n".join(lines) + "\n"


def test_parse_happy_path(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(
        tmp_path,
        "demo",
        frontmatter=_frontmatter(
            tools=[{"name": "demo.run", "module": "tools/run.py"}],
            setup="setup.sh",
            runtime="runtime/server.py",
        ),
        extra_files={
            "tools/run.py": "from typing import Any\n",
            "setup.sh": "#!/bin/sh\nexit 0\n",
            "runtime/server.py": "from typing import Any\n",
        },
    )

    manifest = parse_plugin_manifest(plugin_dir)

    assert manifest.name == "demo"
    assert manifest.description == "demo plugin"
    assert len(manifest.tools) == 1
    assert manifest.tools[0].name == "demo.run"
    assert manifest.tools[0].module == (plugin_dir / "tools/run.py").resolve()
    assert manifest.setup == (plugin_dir / "setup.sh").resolve()
    assert manifest.runtime == (plugin_dir / "runtime/server.py").resolve()
    assert manifest.source_dir == plugin_dir.resolve()
    assert "demo" in manifest.body


def test_setup_defaults_to_setup_sh_when_present(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(
        tmp_path,
        "demo",
        frontmatter=_frontmatter(),
        extra_files={
            "tools/run.py": "x = 1\n",
            "setup.sh": "#!/bin/sh\n",
        },
    )

    manifest = parse_plugin_manifest(plugin_dir)
    assert manifest.setup == (plugin_dir / "setup.sh").resolve()


def test_setup_omitted_and_no_default_means_install_free(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(
        tmp_path,
        "demo",
        frontmatter=_frontmatter(),
        extra_files={"tools/run.py": "x = 1\n"},
    )
    assert parse_plugin_manifest(plugin_dir).setup is None


def test_missing_plugin_md_raises(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    with pytest.raises(PluginManifestError, match="plugin.md missing"):
        parse_plugin_manifest(plugin_dir)


def test_missing_frontmatter_block(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.md").write_text(
        "no frontmatter here\n", encoding="utf-8"
    )
    with pytest.raises(
        PluginManifestError, match="must begin with a `---`-delimited"
    ):
        parse_plugin_manifest(plugin_dir)


def test_invalid_yaml(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.md").write_text(
        "---\n: : :\n---\n", encoding="utf-8"
    )
    with pytest.raises(PluginManifestError, match="not valid YAML"):
        parse_plugin_manifest(plugin_dir)


def test_name_must_match_directory(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(
        tmp_path,
        "demo",
        frontmatter=_frontmatter(name="other"),
        extra_files={"tools/run.py": "x = 1\n"},
    )
    with pytest.raises(
        PluginManifestError, match="does not match directory name"
    ):
        parse_plugin_manifest(plugin_dir)


def test_name_pattern_rejects_uppercase(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(
        tmp_path,
        "Demo",
        frontmatter=_frontmatter(name="Demo"),
        extra_files={"tools/run.py": "x = 1\n"},
    )
    with pytest.raises(PluginManifestError, match="must match"):
        parse_plugin_manifest(plugin_dir)


def test_tools_must_be_explicit_list(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.md").write_text(
        "---\nname: demo\ndescription: x\ntools: []\n---\n",
        encoding="utf-8",
    )
    with pytest.raises(PluginManifestError, match="non-empty list"):
        parse_plugin_manifest(plugin_dir)


def test_tool_name_must_be_prefixed(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(
        tmp_path,
        "demo",
        frontmatter=_frontmatter(
            tools=[{"name": "other.run", "module": "tools/run.py"}]
        ),
        extra_files={"tools/run.py": "x = 1\n"},
    )
    with pytest.raises(PluginManifestError, match="must start with"):
        parse_plugin_manifest(plugin_dir)


def test_duplicate_tool_names_rejected(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(
        tmp_path,
        "demo",
        frontmatter=_frontmatter(
            tools=[
                {"name": "demo.run", "module": "tools/run.py"},
                {"name": "demo.run", "module": "tools/run.py"},
            ]
        ),
        extra_files={"tools/run.py": "x = 1\n"},
    )
    with pytest.raises(PluginManifestError, match="duplicate tool name"):
        parse_plugin_manifest(plugin_dir)


def test_tool_module_path_escape_rejected(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(
        tmp_path,
        "demo",
        frontmatter=_frontmatter(
            tools=[{"name": "demo.run", "module": "../escape.py"}]
        ),
    )
    (tmp_path / "escape.py").write_text("x=1\n", encoding="utf-8")
    with pytest.raises(PluginManifestError, match="escapes plugin dir"):
        parse_plugin_manifest(plugin_dir)


def test_tool_module_must_exist(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(
        tmp_path,
        "demo",
        frontmatter=_frontmatter(),
    )
    with pytest.raises(PluginManifestError, match="module path does not exist"):
        parse_plugin_manifest(plugin_dir)


def test_setup_path_must_exist_when_set(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(
        tmp_path,
        "demo",
        frontmatter=_frontmatter(setup="setup.sh"),
        extra_files={"tools/run.py": "x = 1\n"},
    )
    with pytest.raises(PluginManifestError, match="setup path does not exist"):
        parse_plugin_manifest(plugin_dir)


def test_runtime_path_must_exist_when_set(tmp_path: Path) -> None:
    plugin_dir = _write_plugin(
        tmp_path,
        "demo",
        frontmatter=_frontmatter(runtime="runtime/server.py"),
        extra_files={"tools/run.py": "x = 1\n"},
    )
    with pytest.raises(PluginManifestError, match="runtime path does not exist"):
        parse_plugin_manifest(plugin_dir)


def test_description_required(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.md").write_text(
        "---\nname: demo\ntools:\n  - name: demo.run\n    module: tools/run.py\n---\n",
        encoding="utf-8",
    )
    (plugin_dir / "tools").mkdir()
    (plugin_dir / "tools" / "run.py").write_text("x=1\n", encoding="utf-8")
    with pytest.raises(PluginManifestError, match="description"):
        parse_plugin_manifest(plugin_dir)
