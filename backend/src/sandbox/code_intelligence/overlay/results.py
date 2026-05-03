"""NDJSON parsing for overlay commands."""

from __future__ import annotations

import base64
import json
from typing import Any

from sandbox.code_intelligence.overlay.types import (
    OverlayCapture,
    OverlayPolicyReject,
    OverlayRunError,
    UpperChange,
)


def parse_diff_ndjson(raw: str) -> OverlayCapture | OverlayPolicyReject:
    """Parse the ``diff.ndjson`` body produced by ``overlay_run.py``."""
    lines = [line for line in (raw or "").splitlines() if line.strip()]
    if not lines:
        raise OverlayRunError("empty diff.ndjson payload")

    try:
        first = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise OverlayRunError(f"invalid diff.ndjson meta line: {exc}") from exc

    if isinstance(first, dict) and "_reject" in first:
        return _parse_reject(first["_reject"])
    if not (isinstance(first, dict) and "_meta" in first):
        raise OverlayRunError(
            f"diff.ndjson first line must be _meta or _reject: {first!r}"
        )
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


def _parse_reject(reject_meta: Any) -> OverlayPolicyReject:
    if not isinstance(reject_meta, dict):
        raise OverlayRunError(f"_reject block must be a dict, got {reject_meta!r}")
    return OverlayPolicyReject(
        reason=str(reject_meta.get("reason") or ""),
        paths=tuple(str(p) for p in reject_meta.get("paths") or ()),
        run_timings=parse_timing_dict(reject_meta.get("run_timings") or {}),
    )


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
        base_bytes=_decode_bytes(entry.get("base_bytes_b64")),
        upper_bytes=_decode_bytes(entry.get("upper_bytes_b64")),
        base_existed=bool(entry.get("base_existed")),
    )


def _decode_bytes(value: Any) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise OverlayRunError(f"byte field must be base64 string or null: {value!r}")
    try:
        return base64.b64decode(value.encode("ascii"))
    except Exception as exc:
        raise OverlayRunError(f"invalid base64 byte field: {exc}") from exc


def parse_timing_dict(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): round(float(value), 6)
        for key, value in raw.items()
        if isinstance(value, (int, float))
    }


__all__ = ["parse_diff_ndjson", "parse_timing_dict"]
