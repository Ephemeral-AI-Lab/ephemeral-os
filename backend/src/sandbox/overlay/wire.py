"""Wire-format helpers for overlay runtime requests and responses."""

from __future__ import annotations

import base64
import json
from typing import Any

from sandbox.overlay.types import (
    OverlayCapture,
    OverlayRunError,
    OverlayRunOutcome,
    UpperChange,
)


def upper_change_to_dict(change: UpperChange) -> dict[str, Any]:
    return {
        "rel": change.rel,
        "kind": change.kind,
        "base_bytes": _bytes_to_wire(change.base_bytes),
        "upper_bytes": _bytes_to_wire(change.upper_bytes),
        "base_existed": change.base_existed,
    }


def upper_change_from_dict(d: dict[str, Any]) -> UpperChange:
    return UpperChange(
        rel=str(d["rel"]),
        kind=str(d.get("kind") or "regular"),  # type: ignore[arg-type]
        base_bytes=_bytes_from_wire(d.get("base_bytes")),
        upper_bytes=_bytes_from_wire(d.get("upper_bytes")),
        base_existed=bool(d.get("base_existed", True)),
    )


def overlay_outcome_to_dict(outcome: OverlayRunOutcome) -> dict[str, Any]:
    return {
        "exit_code": outcome.exit_code,
        "stdout": outcome.stdout,
        "upper_changes": [upper_change_to_dict(change) for change in outcome.upper_changes],
        "warnings": list(outcome.warnings),
        "overlay_run_timings": dict(outcome.overlay_run_timings),
        "overlay_stage_timings": dict(outcome.overlay_stage_timings),
    }


def overlay_outcome_from_dict(d: dict[str, Any]) -> OverlayRunOutcome:
    return OverlayRunOutcome(
        exit_code=int(d.get("exit_code") or 0),
        stdout=str(d.get("stdout") or ""),
        upper_changes=tuple(
            upper_change_from_dict(change) for change in (d.get("upper_changes") or ())
        ),
        warnings=tuple(str(w) for w in (d.get("warnings") or ())),
        overlay_run_timings=parse_timing_dict(d.get("overlay_run_timings") or {}),
        overlay_stage_timings=parse_timing_dict(d.get("overlay_stage_timings") or {}),
    )


def parse_diff_ndjson(raw: str) -> OverlayCapture:
    """Parse the ``diff.ndjson`` body produced by the overlay runtime."""
    lines = [line for line in (raw or "").splitlines() if line.strip()]
    if not lines:
        raise OverlayRunError("empty diff.ndjson payload")

    try:
        first = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise OverlayRunError(f"invalid diff.ndjson meta line: {exc}") from exc

    if not (isinstance(first, dict) and "_meta" in first):
        raise OverlayRunError(f"diff.ndjson first line must be _meta: {first!r}")
    meta = first["_meta"]
    if not isinstance(meta, dict):
        raise OverlayRunError(f"_meta block must be a dict, got {meta!r}")

    changes = tuple(_parse_change(idx, line) for idx, line in enumerate(lines[1:], start=1))
    return OverlayCapture(
        exit_code=int(meta.get("exit_code") or 0),
        upper_bytes=int(meta.get("upper_bytes") or 0),
        upper_files=int(meta.get("upper_files") or 0),
        upper_changes=changes,
        run_timings=parse_timing_dict(meta.get("run_timings") or {}),
        warnings=tuple(str(w) for w in meta.get("warnings") or ()),
    )


def parse_timing_dict(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): round(float(value), 6)
        for key, value in raw.items()
        if isinstance(value, (int, float))
    }


def _parse_change(idx: int, line: str) -> UpperChange:
    try:
        entry = json.loads(line)
    except json.JSONDecodeError as exc:
        raise OverlayRunError(f"invalid diff.ndjson entry at line {idx}: {exc}") from exc
    if not isinstance(entry, dict):
        raise OverlayRunError(
            f"diff.ndjson entry at line {idx} must be a dict: {entry!r}"
        )
    return UpperChange(
        rel=str(entry.get("rel") or ""),
        kind=str(entry.get("kind") or "regular"),  # type: ignore[arg-type]
        base_bytes=_bytes_from_wire(entry.get("base_bytes_b64")),
        upper_bytes=_bytes_from_wire(entry.get("upper_bytes_b64")),
        base_existed=bool(entry.get("base_existed")),
    )


def _bytes_to_wire(value: bytes | None) -> str | None:
    if value is None:
        return None
    return base64.b64encode(value).decode("ascii")


def _bytes_from_wire(value: Any) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise OverlayRunError(f"byte field must be base64 string or null: {value!r}")
    try:
        return base64.b64decode(value.encode("ascii"))
    except Exception as exc:
        raise OverlayRunError(f"invalid base64 byte field: {exc}") from exc


__all__ = [
    "overlay_outcome_from_dict",
    "overlay_outcome_to_dict",
    "parse_diff_ndjson",
    "parse_timing_dict",
    "upper_change_from_dict",
    "upper_change_to_dict",
]
