"""Hook executor factory."""

from __future__ import annotations

from pathlib import Path

from config import Settings
from hooks.executor import HookExecutionContext, HookExecutor
from hooks.loader import load_hook_registry
from providers.types import SupportsStreamingMessages


def make_hook_executor(
    settings: Settings,
    cwd: str,
    api_client: SupportsStreamingMessages,
) -> HookExecutor:
    """Build a hook executor from settings + the active model registration."""
    from config.model_config import try_get_active_model_kwargs

    kwargs = try_get_active_model_kwargs() or {}
    default_model = str(kwargs.get("model") or "")

    return HookExecutor(
        load_hook_registry(settings, []),
        HookExecutionContext(
            cwd=Path(cwd).resolve(),
            api_client=api_client,
            default_model=default_model,
        ),
    )
