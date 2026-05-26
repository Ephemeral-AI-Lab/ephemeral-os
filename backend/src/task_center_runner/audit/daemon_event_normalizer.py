"""Normalize pulled daemon audit events for the canonical JSONL sink.

This is the **only** writer of ``payload["daemon_event"]`` — the forensic raw
field, env-gated by ``EOS_AUDIT_FORENSIC_RAW_ENABLED=true`` (default off). A
CI grep test (`test_daemon_event_writer_module_boundary`) fails if any other
file outside this module references that key.

See ``docs/daemon-audit-pull-consolidation-v3/README.md#dual-write-authoritativeness``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

FORENSIC_RAW_ENV = "EOS_AUDIT_FORENSIC_RAW_ENABLED"


def forensic_raw_enabled() -> bool:
    return os.environ.get(FORENSIC_RAW_ENV, "").strip().lower() == "true"


_SECTION_KEYS = frozenset(
    {
        "daemon",
        "layer_stack",
        "overlay_workspace",
        "occ",
        "isolated_workspace",
        "os_resource",
        "plugin",
        "background_tool",
        "tool_call",
    }
)


def _section_keys_of(payload: dict[str, Any]) -> list[str]:
    return [key for key in payload if key in _SECTION_KEYS]


def normalize_pulled_event(
    raw: dict[str, Any],
    *,
    boot_epoch_id: int | None = None,
    task_center_run_id: str = "",
) -> dict[str, Any]:
    """Promote subsystem sections to ``payload[<section>]``; optionally retain raw.

    The pulled event already carries the promoted sections (emitters construct
    them via dataclass helpers in :mod:`sandbox.daemon.audit_schema`). This
    function reshapes the wire format into the JSONL row schema
    (``ts``, ``event_type``, ``seq``, ``payload``) and conditionally tacks on
    ``payload["daemon_event"]`` when forensic raw is enabled.
    """
    inner = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    event_type = str(raw.get("type") or raw.get("event_type") or "daemon.unknown")
    seq_value = raw.get("seq")
    seq = int(seq_value) if isinstance(seq_value, int) else None
    lane = str(raw.get("lane") or "")

    payload: dict[str, Any] = {}
    for key in _section_keys_of(inner):
        payload[key] = inner[key]

    if forensic_raw_enabled():
        payload["daemon_event"] = dict(raw)

    if boot_epoch_id is not None:
        payload.setdefault("daemon", {})["boot_epoch_id"] = boot_epoch_id

    row: dict[str, Any] = {
        "event_type": event_type,
        "schema": raw.get("schema") or "sandbox.daemon.audit.pull.v1",
        "lane": lane,
        "payload": payload,
    }
    if seq is not None:
        row["seq"] = seq
    if task_center_run_id:
        row["task_center_run_id"] = task_center_run_id
    return row


def dedupe_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Stable key used to dedupe pull-derived vs stream-derived events.

    Per V3 README §Dual-write authoritativeness: ``seq`` first, then
    ``(operation_id, event_type, operation_step, tool_id)`` for non-pulled rows
    that lack a seq.
    """
    seq = row.get("seq")
    if isinstance(seq, int):
        return ("seq", seq)
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    op_id = payload.get("operation_id")
    op_step = payload.get("operation_step")
    tool_id = payload.get("tool_id")
    return (
        "logical",
        row.get("event_type"),
        op_id,
        op_step,
        tool_id,
    )


def merge_streams(
    pulled: Iterable[dict[str, Any]],
    streamed: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge pull-derived and stream-derived rows; pull is authoritative on collision."""
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in streamed:
        merged.setdefault(dedupe_key(row), row)
    for row in pulled:
        # Pull supersedes stream — overwrite unconditionally.
        merged[dedupe_key(row)] = row
    return list(merged.values())


__all__ = [
    "FORENSIC_RAW_ENV",
    "dedupe_key",
    "forensic_raw_enabled",
    "merge_streams",
    "normalize_pulled_event",
]
