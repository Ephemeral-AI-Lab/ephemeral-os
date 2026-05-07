"""Host transport for sandbox-local guarded daemon operations."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sandbox.contracts import ConflictInfo
from sandbox.host.runtime_bundle import BUNDLE_REMOTE_DIR
from sandbox.host.daemon_client import _call_daemon
from sandbox.provider.registry import get_adapter

DEFAULT_LAYER_STACK_ROOT = f"{BUNDLE_REMOTE_DIR}/layer-stack"


async def call_daemon_api(
    sandbox_id: str,
    op: str,
    args: dict[str, Any],
    *,
    timeout: int = 60,
    layer_stack_root: str = DEFAULT_LAYER_STACK_ROOT,
) -> dict[str, Any]:
    """Call one guarded API operation inside the preinstalled daemon bundle."""
    daemon_args = {
        "layer_stack_root": layer_stack_root,
        **args,
    }
    return await _call_daemon(
        exec_fn=get_adapter(sandbox_id).exec,
        sandbox_id=sandbox_id,
        op=op,
        args=daemon_args,
        timeout=timeout,
    )


def conflict_from_payload(raw: object) -> ConflictInfo | None:
    if not isinstance(raw, dict):
        return None
    return ConflictInfo(
        reason=str(raw.get("reason", "")),
        conflict_file=(
            str(raw.get("conflict_file"))
            if raw.get("conflict_file") is not None
            else None
        ),
        message=str(raw.get("message", "")),
    )


def paths_from_payload(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(str(path) for path in raw if str(path or "").strip())


def timings_from_payload(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): float(value) for key, value in raw.items()}


def int_from_payload(value: object, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, (str, int, float)):
        return int(value)
    raise TypeError(f"expected integer value, got {type(value).__name__}")


__all__ = [
    "DEFAULT_LAYER_STACK_ROOT",
    "call_daemon_api",
    "conflict_from_payload",
    "int_from_payload",
    "paths_from_payload",
    "timings_from_payload",
]
