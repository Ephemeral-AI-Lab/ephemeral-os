"""Public tools facade.

Runtime code should import tool primitives, factories, and execution helpers
from this module. Subpackages under ``tools.*`` are implementation modules.
"""

from __future__ import annotations

from typing import Any

from tools._framework.core import (
    BaseTool,
    HookResult,
    TextToolOutput,
    ToolExecutionContextService,
    ToolRegistry,
    ToolResult,
    tool,
)
from tools._framework.core.runtime import ExecutionMetadata

_LAZY_EXPORTS = {
    "ToolCatalogEntry": "tools._framework.introspection.catalog",
    "ToolFactoryContext": "tools._framework.factory",
    "_count_tool_dispatch": "tools._framework.execution.tool_call",
    "collect_schema_tools": "tools._framework.introspection.schema_summary",
    "collect_tool_catalog": "tools._framework.introspection.catalog",
    "create_tool": "tools._framework.factory",
    "execute_tool_call": "tools._framework.execution.tool_call",
    "execute_tool_call_streaming": "tools._framework.execution.tool_call",
    "execute_tool_once": "tools._framework.execution.tool_call",
    "format_tool_schema_summary": "tools._framework.introspection.schema_summary",
    "has_tool": "tools._framework.factory",
    "list_available_tools": "tools._framework.factory",
    "make_ask_helper_tools": "tools.ask_helper",
    "make_sandbox_tools": "tools.sandbox",
    "make_subagent_tool_from_context": "tools.subagent",
    "make_submission_tools": "tools.submission",
    "make_workflow_tools": "tools.workflow",
    "register_tool_factory": "tools._framework.factory",
    "resolve_harness_notification_triggers": "tools.submission.notification_triggers",
    "SANDBOX_CONTEXT": "tools.sandbox._lib.context",
}


def create_default_tool_registry() -> ToolRegistry:
    """Return an empty tool registry. Tools are registered during agent setup."""
    return ToolRegistry()


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    from importlib import import_module

    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


__all__ = [
    "BaseTool",
    "ExecutionMetadata",
    "HookResult",
    "SANDBOX_CONTEXT",
    "TextToolOutput",
    "ToolCatalogEntry",
    "ToolExecutionContextService",
    "ToolFactoryContext",
    "ToolRegistry",
    "ToolResult",
    "_count_tool_dispatch",
    "collect_schema_tools",
    "collect_tool_catalog",
    "create_default_tool_registry",
    "create_tool",
    "execute_tool_call",
    "execute_tool_call_streaming",
    "execute_tool_once",
    "format_tool_schema_summary",
    "has_tool",
    "list_available_tools",
    "make_ask_helper_tools",
    "make_sandbox_tools",
    "make_subagent_tool_from_context",
    "make_submission_tools",
    "make_workflow_tools",
    "register_tool_factory",
    "resolve_harness_notification_triggers",
    "tool",
]
