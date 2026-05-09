"""Unit tests for plugins.core.loader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from plugins.core import loader as loader_mod
from plugins.core.loader import (
    PluginToolBindingError,
    PluginToolImportError,
    register_plugin_tools,
)


def _seed_plugin(
    catalog: Path,
    name: str,
    *,
    tool_module_body: str,
    extra_tools: dict[str, str] | None = None,
) -> Path:
    plugin_dir = catalog / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.md").write_text(
        f"---\nname: {name}\ndescription: {name} plugin\ntools:\n"
        f"  - name: {name}.run\n    module: tools/run.py\n---\n",
        encoding="utf-8",
    )
    tools_dir = plugin_dir / "tools"
    tools_dir.mkdir()
    (tools_dir / "run.py").write_text(tool_module_body, encoding="utf-8")
    for relative, body in (extra_tools or {}).items():
        (tools_dir / relative).write_text(body, encoding="utf-8")
    return plugin_dir


def _valid_tool_body(name: str) -> str:
    return textwrap.dedent(
        f"""
        from pydantic import BaseModel
        from tools.core.base import BaseTool, ToolResult


        class _Input(BaseModel):
            payload: str = ""


        class _RunTool(BaseTool):
            name = "{name}"
            description = "test plugin tool"
            input_model = _Input

            async def execute(self, arguments, context):  # type: ignore[override]
                return ToolResult(output="ok")


        run = _RunTool()
        """
    ).strip()


@pytest.fixture(autouse=True)
def _clear_loader_cache() -> None:
    import sys

    loader_mod._LOAD_CACHE.clear()
    for mod_name in [
        name
        for name in sys.modules
        if name.startswith("plugins.catalog.")
    ]:
        sys.modules.pop(mod_name, None)
    yield
    loader_mod._LOAD_CACHE.clear()
    for mod_name in [
        name
        for name in sys.modules
        if name.startswith("plugins.catalog.")
    ]:
        sys.modules.pop(mod_name, None)


def test_register_plugin_tools_happy_path(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    _seed_plugin(catalog, "demo", tool_module_body=_valid_tool_body("demo.run"))

    tools = register_plugin_tools(catalog)
    assert len(tools) == 1
    assert tools[0].name == "demo.run"


def test_register_plugin_tools_is_idempotent(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    _seed_plugin(catalog, "demo", tool_module_body=_valid_tool_body("demo.run"))

    first = register_plugin_tools(catalog)
    second = register_plugin_tools(catalog)
    assert [t.name for t in first] == [t.name for t in second]
    assert first[0] is second[0]


def test_module_name_mismatch_raises(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    _seed_plugin(
        catalog,
        "demo",
        tool_module_body=_valid_tool_body("demo.something_else"),
    )
    with pytest.raises(PluginToolBindingError, match="does not match manifest"):
        register_plugin_tools(catalog)


def test_module_with_zero_base_tools_raises(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    _seed_plugin(
        catalog,
        "demo",
        tool_module_body="x = 1\n",
    )
    with pytest.raises(PluginToolBindingError, match="exports no BaseTool"):
        register_plugin_tools(catalog)


def test_module_with_two_base_tools_raises(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    body = textwrap.dedent(
        """
        from pydantic import BaseModel
        from tools.core.base import BaseTool, ToolResult


        class _Input(BaseModel):
            payload: str = ""


        class _Tool(BaseTool):
            name = "demo.run"
            description = "x"
            input_model = _Input

            async def execute(self, arguments, context):  # type: ignore[override]
                return ToolResult(output="ok")


        a = _Tool()
        b = _Tool()
        """
    ).strip()
    _seed_plugin(catalog, "demo", tool_module_body=body)
    with pytest.raises(PluginToolBindingError, match="exports 2 BaseTools"):
        register_plugin_tools(catalog)


def test_import_failure_surfaces_with_path(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    _seed_plugin(
        catalog,
        "demo",
        tool_module_body="raise RuntimeError('boom')\n",
    )
    with pytest.raises(PluginToolImportError, match="failed to import"):
        register_plugin_tools(catalog)


def test_factory_integration_via_create_tool(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    _seed_plugin(catalog, "demo", tool_module_body=_valid_tool_body("demo.run"))

    from tools.factory import (
        ToolFactoryContext,
        _register_many,
        create_tool,
        has_tool,
    )

    _register_many(register_plugin_tools(catalog))
    assert has_tool("demo.run")
    instance = create_tool("demo.run", ToolFactoryContext())
    assert instance.name == "demo.run"
