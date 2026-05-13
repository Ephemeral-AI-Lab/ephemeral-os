"""Tool registry for context-aware tool instantiation."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tools._framework.core.base import BaseTool

logger = logging.getLogger(__name__)


@dataclass
class ToolFactoryContext:
    """Runtime context passed to tool factories during agent construction."""

    metadata: dict[str, Any] = field(default_factory=dict)


ToolFactory = Callable[[ToolFactoryContext], BaseTool]

_factories: dict[str, ToolFactory] = {}
_builtins_registered: bool = False


def register_tool_factory(
    name: str, factory: ToolFactory, *, override: bool = False
) -> None:
    """Register a factory for a named tool.

    Raises ``ValueError`` if *name* is already registered unless
    ``override=True`` is passed. This prevents plugins from silently
    shadowing builtin tools (or each other).
    """
    if name in _factories and not override:
        raise ValueError(
            f"Tool factory {name!r} already registered; "
            f"pass override=True to replace."
        )
    _factories[name] = factory
    logger.debug("Registered tool factory: %s", name)


def register_tool_instance(tool: BaseTool, *, override: bool = False) -> None:
    """Register a reusable stateless tool instance."""

    def factory(ctx: ToolFactoryContext) -> BaseTool:
        del ctx
        return tool

    register_tool_factory(tool.name, factory, override=override)


def create_tool(name: str, ctx: ToolFactoryContext) -> BaseTool:
    """Create a tool instance by name."""
    _ensure_builtins_registered()
    factory = _factories.get(name)
    if factory is None:
        raise KeyError(f"Tool '{name}' not registered. Tools: {list(_factories)}")
    tool = factory(ctx)
    if tool.name != name:
        raise ValueError(f"Tool factory for {name!r} returned {tool.name!r}")
    return tool


def create_tools(names: list[str], ctx: ToolFactoryContext) -> list[BaseTool]:
    """Create tool instances, deduplicating by tool name while preserving order."""
    tools: list[BaseTool] = []
    seen: set[str] = set()
    for name in names:
        clean_name = str(name).strip()
        if not clean_name or clean_name in seen:
            continue
        tools.append(create_tool(clean_name, ctx))
        seen.add(clean_name)
    return tools


def has_tool(name: str) -> bool:
    """Return True if a tool factory is registered for *name*."""
    _ensure_builtins_registered()
    return name in _factories


def list_available_tools() -> list[str]:
    """List all registered tool names."""
    _ensure_builtins_registered()
    return list(_factories.keys())


def _register_many(tools: list[BaseTool]) -> None:
    for tool in tools:
        register_tool_instance(tool)


def _register_builtins() -> None:
    """Register built-in tool factories.

    Note: ``make_skills_tools`` is intentionally NOT registered here. Skill
    tools require a ``SkillRegistry`` instance that cannot be resolved at
    static registration time; they are constructed per-agent at agent build
    time. As a consequence, ``collect_tool_catalog`` and
    ``collect_schema_tools`` will not enumerate skill tools — that is
    expected; skill tools are agent-scoped, not part of the global catalog.

    Set ``EOS_SKIP_PLUGIN_IMPORTS_FOR_TESTS=1`` to skip plugin discovery —
    useful for unit tests that want to exercise the framework in
    isolation without triggering transitive plugin imports.
    """
    from tools.ask_helper import make_ask_helper_tools
    from tools.sandbox import make_sandbox_tools
    from tools.submission import make_submission_tools
    from tools.subagent import make_subagent_tool_from_context

    _register_many(make_sandbox_tools())
    _register_many(make_submission_tools())
    _register_many(make_ask_helper_tools())
    register_tool_factory("run_subagent", make_subagent_tool_from_context)
    if not os.environ.get("EOS_SKIP_PLUGIN_IMPORTS_FOR_TESTS"):
        from plugins.core.loader import register_plugin_tools

        _register_many(register_plugin_tools())


def _ensure_builtins_registered() -> None:
    global _builtins_registered
    # If `_factories` was externally cleared (e.g. by a test fixture), the
    # flag is stale — fall through and re-register. This keeps the
    # idempotency guard from breaking test isolation.
    if _builtins_registered and _factories:
        return
    _register_builtins()
    _builtins_registered = True
