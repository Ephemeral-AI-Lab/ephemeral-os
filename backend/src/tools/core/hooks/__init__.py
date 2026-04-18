"""Platform-owned in-process tool hooks."""

from __future__ import annotations

from tools.core.hooks.outcomes import (
    EmitStreamEvent,
    PostHookOutcome,
    PostToolHook,
    PreHookOutcome,
    PreHookPipelineResult,
    PreToolHook,
)
from tools.core.hooks.pipeline import run_post_hooks, run_pre_hooks
from tools.core.hooks.registry import HookEntry, Phase, ToolHookRegistry, default_registry
from tools.core.hooks.execution import execute_tool_with_hooks

__all__ = [
    "EmitStreamEvent",
    "HookEntry",
    "Phase",
    "PostHookOutcome",
    "PostToolHook",
    "PreHookOutcome",
    "PreHookPipelineResult",
    "PreToolHook",
    "ToolHookRegistry",
    "default_registry",
    "execute_tool_with_hooks",
    "run_post_hooks",
    "run_pre_hooks",
]
