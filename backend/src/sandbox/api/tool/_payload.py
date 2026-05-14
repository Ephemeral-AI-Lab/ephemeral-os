"""Payload projection helpers for sandbox-local guarded daemon operations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import fields
import re

from sandbox.models import ConflictInfo, SandboxCaller
from sandbox.timing import normalize_timing_map

_CALLER_REQUIRED_FIELDS = frozenset({"agent_id", "run_id", "agent_run_id", "task_id"})
_INTERNAL_ERROR_PREFIX = "internal_error: "
_TRANSIENT_ERROR_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bdaytonaerror\b",
        r"\bfailed to execute command\b",
        r"\bconnection reset\b",
        r"\bconnection refused\b",
        r"\bserver disconnected\b",
        r"\btemporarily unavailable\b",
        r"\bruntimeexecfailed\b",
        r"\beos_daemon_io_failed\b",
    )
)


def caller_audit_fields(caller: SandboxCaller) -> dict[str, str]:
    """Project a SandboxCaller into daemon audit fields.

    Required daemon keys stay present even when empty. New dataclass fields are
    forwarded automatically when populated, so audit attribution can evolve
    without updating a hand-maintained allowlist.
    """
    envelope: dict[str, str] = {}
    for field in fields(caller):
        key = field.name
        value = getattr(caller, key)
        if key in _CALLER_REQUIRED_FIELDS or value:
            envelope[key] = str(value)
    return envelope


def normalize_overlay_cwd(cwd: str | None) -> str:
    """Normalize public shell cwd values for overlay execution."""
    normalized = (cwd or "").strip()
    return normalized or "."


def error_message(error: BaseException) -> str:
    message = str(getattr(error, "message", "") or error)
    if message.startswith(_INTERNAL_ERROR_PREFIX):
        return message.removeprefix(_INTERNAL_ERROR_PREFIX)
    return message


def is_transient_transport_error(error: BaseException) -> bool:
    message = error_message(error)
    return any(pattern.search(message) for pattern in _TRANSIENT_ERROR_PATTERNS)


def conflict_from_payload(raw: object) -> ConflictInfo | None:
    if not isinstance(raw, dict):
        return None
    conflict_file = raw.get("conflict_file")
    return ConflictInfo(
        reason=str(raw.get("reason", "")),
        # Only stringify the conflict_file when the daemon actually sent a
        # string-coercible primitive. Blindly stringifying arbitrary objects
        # (lists, dicts) yields nonsense paths like "['x', 'y']".
        conflict_file=(
            str(conflict_file)
            if isinstance(conflict_file, (str, int, float, bytes))
            else None
        ),
        message=str(raw.get("message", "")),
    )


def paths_from_payload(raw: object) -> tuple[str, ...]:
    # Reject dict — iterating yields keys with the same surface type as a
    # list of paths, which papers over upstream contract breakage. Bytes
    # and str are excluded to keep the API focused on iterables of paths.
    if (
        not isinstance(raw, Iterable)
        or isinstance(raw, (str, bytes, dict))
    ):
        return ()
    return tuple(str(path) for path in raw if str(path or "").strip())


def timings_from_payload(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    return normalize_timing_map(raw)


def int_from_payload(value: object, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise TypeError(f"expected integer value, got bool ({value!r})")
    if isinstance(value, int):
        return value
    raise TypeError(f"expected integer value, got {type(value).__name__}")


__all__ = [
    "caller_audit_fields",
    "conflict_from_payload",
    "error_message",
    "int_from_payload",
    "is_transient_transport_error",
    "normalize_overlay_cwd",
    "paths_from_payload",
    "timings_from_payload",
]
