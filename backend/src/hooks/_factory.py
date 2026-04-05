"""Hook executor factory."""

from __future__ import annotations

from pathlib import Path

from ephemeralos.config import Settings
from ephemeralos.hooks.executor import HookExecutionContext, HookExecutor
from ephemeralos.hooks.loader import load_hook_registry
from ephemeralos.models.types import SupportsStreamingMessages


def make_hook_executor(
    settings: Settings, cwd: str, api_client: SupportsStreamingMessages,
) -> HookExecutor:
    """Build a hook executor from settings."""
    return HookExecutor(
        load_hook_registry(settings, []),
        HookExecutionContext(
            cwd=Path(cwd).resolve(),
            api_client=api_client,
            default_model=settings.model,
        ),
    )
