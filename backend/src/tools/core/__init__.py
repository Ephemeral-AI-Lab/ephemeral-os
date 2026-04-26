"""Core tool abstractions and decorators."""

from tools.core.base import (
    BaseTool,
    TextToolOutput,
    ToolExecutionContextService,
    ToolRegistry,
    ToolResult,
)
from tools.core.decorator import tool
from tools.core.hooks import HookResult, HookStatus, ToolPostHook, ToolPreHook

__all__ = [
    "BaseTool",
    "TextToolOutput",
    "ToolExecutionContextService",
    "ToolRegistry",
    "ToolResult",
    "HookResult",
    "HookStatus",
    "ToolPostHook",
    "ToolPreHook",
    "tool",
]
