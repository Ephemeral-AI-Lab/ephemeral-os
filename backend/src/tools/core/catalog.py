"""Helpers for enumerating toolkits and tools exposed by the runtime."""

from __future__ import annotations

from dataclasses import dataclass

from tools.core.base import BaseToolkit, ToolRegistry
from tools.core.factory import ToolkitContext, create_toolkit, list_factories


@dataclass(frozen=True)
class ToolCatalogEntry:
    """UI/API-safe tool metadata."""

    name: str
    description: str


@dataclass(frozen=True)
class ToolkitCatalogEntry:
    """UI/API-safe toolkit metadata."""

    name: str
    description: str
    tools: list[str]


def _snapshot_contexts(factory_name: str) -> list[ToolkitContext]:
    if factory_name == "submission":
        return [
            ToolkitContext(metadata={"role": "planner"}),
            ToolkitContext(metadata={"role": "replanner"}),
            ToolkitContext(metadata={"role": "developer"}),
        ]
    return [ToolkitContext()]


def _iter_factory_toolkits() -> list[BaseToolkit]:
    toolkits: list[BaseToolkit] = []
    for factory_name in list_factories():
        for ctx in _snapshot_contexts(factory_name):
            toolkits.append(create_toolkit(factory_name, ctx))
    return toolkits


def _background_tool_names() -> list[str]:
    return sorted(
        {
            tool.name
            for toolkit in _iter_factory_toolkits()
            for tool in toolkit.list_tools()
            if getattr(tool, "background", "forbidden") != "forbidden"
        }
    )


def collect_toolkit_catalog(
    tool_registry: ToolRegistry | None = None,
    *,
    include_runtime_toolkits: bool = False,
    cwd: str | None = None,
) -> list[ToolkitCatalogEntry]:
    """Return deduplicated toolkit snapshots suitable for APIs and UI."""

    by_name: dict[str, ToolkitCatalogEntry] = {}

    def _merge(toolkit: BaseToolkit) -> None:
        current = by_name.get(toolkit.name)
        merged_tools = set(current.tools if current is not None else [])
        merged_tools.update(toolkit.tool_names())
        by_name[toolkit.name] = ToolkitCatalogEntry(
            name=toolkit.name,
            description=(current.description if current is not None else toolkit.description),
            tools=sorted(merged_tools),
        )

    for toolkit in (tool_registry.list_toolkits() if tool_registry is not None else []):
        _merge(toolkit)
    for toolkit in _iter_factory_toolkits():
        _merge(toolkit)

    if include_runtime_toolkits:
        from skills.core.loader import load_skill_registry
        from tools.builtins.background import make_background_toolkit
        from tools.builtins.skills import make_skills_toolkit

        skills_toolkit = make_skills_toolkit(load_skill_registry(cwd))
        if skills_toolkit.list_tools():
            _merge(skills_toolkit)

        background_tool_names = _background_tool_names()
        if background_tool_names:
            _merge(make_background_toolkit(background_tool_names))

    return sorted(by_name.values(), key=lambda entry: entry.name)


def collect_tool_catalog(
    tool_registry: ToolRegistry | None = None,
    *,
    include_runtime_tools: bool = False,
    cwd: str | None = None,
) -> list[ToolCatalogEntry]:
    """Return deduplicated tool metadata suitable for API responses."""

    by_name: dict[str, ToolCatalogEntry] = {}

    def _merge_tool(name: str, description: str) -> None:
        if name not in by_name:
            by_name[name] = ToolCatalogEntry(name=name, description=description)

    for toolkit in (tool_registry.list_toolkits() if tool_registry is not None else []):
        for tool in toolkit.list_tools():
            _merge_tool(tool.name, tool.description)
    for toolkit in _iter_factory_toolkits():
        for tool in toolkit.list_tools():
            _merge_tool(tool.name, tool.description)

    if include_runtime_tools:
        from skills.core.loader import load_skill_registry
        from tools.builtins.background import make_background_toolkit
        from tools.builtins.skills import make_skills_toolkit

        skill_registry = load_skill_registry(cwd)
        skills_toolkit = make_skills_toolkit(skill_registry)
        for tool in skills_toolkit.list_tools():
            _merge_tool(tool.name, tool.description)

        background_tool_names = _background_tool_names()
        if background_tool_names:
            background_toolkit = make_background_toolkit(background_tool_names)
            for tool in background_toolkit.list_tools():
                _merge_tool(tool.name, tool.description)

    return sorted(by_name.values(), key=lambda entry: entry.name)
