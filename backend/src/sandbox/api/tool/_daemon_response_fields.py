"""Coerce individual fields returned by sandbox daemon operations."""

from __future__ import annotations

from collections.abc import Iterable

from sandbox._shared.models import ConflictInfo
from sandbox._shared.clock import normalize_timing_map

_DAEMON_INTERNAL_ERROR_PREFIX = "internal_error: "


def user_visible_error_message(error: BaseException) -> str:
    message = str(getattr(error, "message", "") or error)
    if message.startswith(_DAEMON_INTERNAL_ERROR_PREFIX):
        return message.removeprefix(_DAEMON_INTERNAL_ERROR_PREFIX)
    return message


def conflict_info_from_daemon_field(raw: object) -> ConflictInfo | None:
    if not isinstance(raw, dict):
        return None
    conflict_file = raw.get("conflict_file")
    return ConflictInfo(
        reason=str(raw.get("reason", "")),
        conflict_file=(
            str(conflict_file)
            if isinstance(conflict_file, (str, int, float, bytes))
            else None
        ),
        message=str(raw.get("message", "")),
    )


def path_tuple_from_daemon_field(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes, dict)):
        return ()
    return tuple(str(path) for path in raw if str(path or "").strip())


def timing_map_from_daemon_field(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    return normalize_timing_map(raw)


def int_from_daemon_field(value: object, *, default: int) -> int:
    """Return an integer boundary value without accepting bool-as-int."""
    if value is None:
        return default
    if isinstance(value, bool):
        raise TypeError(f"expected integer value, got bool ({value!r})")
    if isinstance(value, int):
        return value
    raise TypeError(f"expected integer value, got {type(value).__name__}")
