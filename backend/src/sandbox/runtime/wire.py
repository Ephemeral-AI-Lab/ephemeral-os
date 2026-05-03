"""Wire helpers for runtime pipeline results."""

from __future__ import annotations

from typing import Any

from sandbox.runtime.types import ConflictInfo, ShellResult


def conflict_info_to_dict(conflict: ConflictInfo | None) -> dict[str, Any] | None:
    if conflict is None:
        return None
    return {
        "reason": conflict.reason,
        "conflict_file": conflict.conflict_file,
        "message": conflict.message,
    }


def conflict_info_from_dict(d: dict[str, Any] | None) -> ConflictInfo | None:
    if d is None:
        return None
    return ConflictInfo(
        reason=str(d.get("reason") or ""),
        conflict_file=d.get("conflict_file"),
        message=str(d.get("message") or ""),
    )


def shell_result_to_dict(result: ShellResult) -> dict[str, Any]:
    return {
        "result": result.result,
        "exit_code": result.exit_code,
        "changed_paths": list(result.changed_paths),
        "warnings": list(result.warnings),
        "overlay_run_timings": dict(result.overlay_run_timings),
        "overlay_stage_timings": dict(result.overlay_stage_timings),
        "conflict": conflict_info_to_dict(result.conflict),
    }


def shell_result_from_dict(d: dict[str, Any]) -> ShellResult:
    return ShellResult(
        result=str(d.get("result") or ""),
        exit_code=int(d.get("exit_code") or 0),
        changed_paths=tuple(str(v) for v in (d.get("changed_paths") or ())),
        warnings=tuple(str(v) for v in (d.get("warnings") or ())),
        overlay_run_timings=_parse_timing_dict(d.get("overlay_run_timings") or {}),
        overlay_stage_timings=_parse_timing_dict(d.get("overlay_stage_timings") or {}),
        conflict=conflict_info_from_dict(d.get("conflict")),
    )


def _parse_timing_dict(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): round(float(value), 6)
        for key, value in raw.items()
        if isinstance(value, (int, float))
    }


__all__ = [
    "conflict_info_from_dict",
    "conflict_info_to_dict",
    "shell_result_from_dict",
    "shell_result_to_dict",
]
