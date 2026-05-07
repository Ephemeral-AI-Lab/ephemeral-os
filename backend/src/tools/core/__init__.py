"""Core tool abstractions and decorators."""

from tools.core.base import (
    BaseTool,
    TextToolOutput,
    ToolExecutionContextService,
    ToolResult,
)
from tools.core.decorator import tool
from tools.core.hooks import HookResult, HookStatus, ToolPostHook, ToolPreHook
from tools.core.registry import ToolRegistry

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
