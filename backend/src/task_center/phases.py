"""Phase compiler — validate and compile a phased plan into a dep map.

Enforces every rule from
``docs/architecture/phased-executor-evaluator-tree.md`` § Phase Model.
"""

from __future__ import annotations

from typing import Any

from task_center.errors import PhaseValidationError


def compile_phases(
    phases: list[list[dict[str, Any]]],
    task_specs: dict[str, dict[str, Any]],
) -> dict[str, frozenset[str]]:
    """Validate a phased plan and compile it into a dep map."""
    if not isinstance(phases, list) or len(phases) == 0:
        raise PhaseValidationError("phases must be a non-empty list")
    if not isinstance(task_specs, dict) or len(task_specs) == 0:
        raise PhaseValidationError("task_specs must be a non-empty dict")

    phase_of: dict[str, int] = {}
    phase_ids: list[list[str]] = []

    for phase_idx, phase in enumerate(phases):
        if not isinstance(phase, list) or len(phase) == 0:
            raise PhaseValidationError(
                f"phase {phase_idx + 1} must be a non-empty list of entries"
            )
        ids_this_phase: list[str] = []
        for entry in phase:
            if not isinstance(entry, dict):
                raise PhaseValidationError(
                    f"phase {phase_idx + 1}: entries must be objects with 'id', got {entry!r}"
                )
            if "id" not in entry:
                raise PhaseValidationError(
                    f"phase {phase_idx + 1}: entry missing 'id': {entry!r}"
                )
            task_id = entry["id"]
            if not isinstance(task_id, str) or not task_id:
                raise PhaseValidationError(
                    f"phase {phase_idx + 1}: entry 'id' must be a non-empty string, got {task_id!r}"
                )
            if task_id in phase_of:
                raise PhaseValidationError(
                    f"duplicate task id {task_id!r} (in phases {phase_of[task_id] + 1} and {phase_idx + 1})"
                )
            if task_id not in task_specs:
                raise PhaseValidationError(
                    f"task id {task_id!r} is not a key in task_specs"
                )
            phase_of[task_id] = phase_idx
            ids_this_phase.append(task_id)
        phase_ids.append(ids_this_phase)

    deps: dict[str, frozenset[str]] = {}
    for phase_idx, phase in enumerate(phases):
        for entry in phase:
            task_id = entry["id"]
            raw_needs = entry.get("needs")

            if raw_needs is None:
                if phase_idx == 0:
                    deps[task_id] = frozenset()
                else:
                    deps[task_id] = frozenset(phase_ids[phase_idx - 1])
                continue

            if phase_idx == 0:
                raise PhaseValidationError(
                    f"phase 1 entry {task_id!r}: 'needs' is not allowed on phase 1"
                )
            if not isinstance(raw_needs, list):
                raise PhaseValidationError(
                    f"task {task_id!r}: 'needs' must be a list, got {type(raw_needs).__name__}"
                )
            if len(raw_needs) != len(set(raw_needs)):
                raise PhaseValidationError(
                    f"task {task_id!r}: 'needs' contains duplicate ids"
                )
            for need_id in raw_needs:
                if not isinstance(need_id, str):
                    raise PhaseValidationError(
                        f"task {task_id!r}: 'needs' entry must be a string, got {need_id!r}"
                    )
                if need_id == task_id:
                    raise PhaseValidationError(
                        f"task {task_id!r}: 'needs' may not contain the entry's own id"
                    )
                if need_id not in phase_of:
                    raise PhaseValidationError(
                        f"task {task_id!r}: 'needs' references unknown id {need_id!r}"
                    )
                need_phase = phase_of[need_id]
                if need_phase >= phase_idx:
                    raise PhaseValidationError(
                        f"task {task_id!r} (phase {phase_idx + 1}): 'needs' "
                        f"references {need_id!r} in phase {need_phase + 1} — must be strictly earlier"
                    )
            deps[task_id] = frozenset(raw_needs)

    _check_no_cycles(deps)
    return deps


def _check_no_cycles(deps: dict[str, frozenset[str]]) -> None:
    """DFS-based cycle detection (defensive — cycles can't arise from valid input)."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(deps, WHITE)

    def visit(tid: str, stack: list[str]) -> None:
        color[tid] = GRAY
        for dep in deps.get(tid, frozenset()):
            if color.get(dep) == GRAY:
                cycle_path = " -> ".join(stack[stack.index(dep):] + [dep])
                raise PhaseValidationError(f"cycle detected in compiled deps: {cycle_path}")
            if color.get(dep) == WHITE:
                visit(dep, stack + [dep])
        color[tid] = BLACK

    for tid in list(deps):
        if color[tid] == WHITE:
            visit(tid, [tid])
