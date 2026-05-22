"""Background-shell probes that drive ``shell(background=True)`` through
the mock-agent tool framework so the scenario harness records full
``sandbox_events.jsonl`` / ``performance_report.json`` artifacts under
``.sweevo_runs/scenario_logs/...``.

One async probe function per scenario action; each one writes a JSON
summary to a known workspace path that the matching test reads back via
``sandbox_api.read_file`` after the scenario report returns.

Background mode is enabled by passing ``background_task_id`` through
``call_tool`` — the shell tool reads ``context.background_task_id`` at
``backend/src/tools/sandbox/shell/shell.py:154`` and routes through the
daemon's launch/poll/cancel/reap surface. Cancel propagation matches the
production engine path: ``asyncio.wait_for`` raises ``CancelledError``
into ``_shell_background_dispatch._send_cancel_then_reap``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from message.stream_events import StreamEvent
from tools._framework.core.results import ToolResult
from tools._framework.core.runtime import ExecutionMetadata
from tools.sandbox.read_file import read_file as read_file_tool
from tools.sandbox.shell import shell as shell_tool


WORKSPACE_ROOT = "/testbed"
ROOT = f"{WORKSPACE_ROOT}/.ephemeralos/sweevo-mock/background_shell"
GOLDEN_SUMMARY = f"{ROOT}/golden/summary.json"
CANCEL_SUMMARY = f"{ROOT}/cancel/summary.json"
INTERLEAVE_SUMMARY = f"{ROOT}/interleave/summary.json"
EXHAUSTION_SUMMARY = f"{ROOT}/exhaustion/summary.json"
PARTIAL_WRITE_SUMMARY = f"{ROOT}/partial_write/summary.json"
MAINTENANCE_SUMMARY = f"{ROOT}/maintenance/summary.json"
LATE_CANCEL_SUMMARY = f"{ROOT}/late_cancel/summary.json"

SUMMARY_SCHEMA = "task_center_runner.background_shell.v1"

EmitStreamEvent = Callable[[StreamEvent], Awaitable[None]]
# call_tool signature with the new background_task_id parameter we plumbed
# through ``runner.py:_call_tool``.
CallTool = Callable[..., Awaitable[ToolResult]]
RecordToolCheck = Callable[[str, ToolResult], None]


# ---- shared helpers --------------------------------------------------------


def _bg_id(label: str) -> str:
    return f"bg-{label}-{uuid4().hex[:8]}"


def _shell_payload(result: ToolResult) -> dict[str, Any]:
    """Decode the JSON body the shell tool writes into ``ToolResult.output``."""
    try:
        return json.loads(result.output or "{}")
    except json.JSONDecodeError:
        return {}


def _shell_metadata(result: ToolResult) -> dict[str, Any]:
    meta = dict(result.metadata or {})
    return {
        "timings": dict(meta.get("timings") or {}),
        "changed_paths": list(meta.get("changed_paths") or ()),
        "status": meta.get("status"),
        "conflict_reason": meta.get("conflict_reason"),
    }


async def _write_summary(
    *,
    path: str,
    payload: dict[str, Any],
    metadata: ExecutionMetadata,
    emit: EmitStreamEvent,
    call_tool: CallTool,
    record_tool_check: RecordToolCheck,
) -> str:
    # ``write_file_tool`` would shape this cleaner, but a shell here keeps
    # the probe focused on the background-shell surface; the foreground
    # heredoc is intentional and not load-bearing for the assertions.
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    write_cmd = (
        f"mkdir -p $(dirname {path}) && "
        f"cat <<'__BG_SHELL_PROBE__' > {path}\n{body}__BG_SHELL_PROBE__"
    )
    written = await call_tool(
        shell_tool,
        {"command": write_cmd, "timeout": 60},
        metadata,
        emit,
    )
    record_tool_check(f"tool.shell.background_shell.summary.{path}", written)
    if written.is_error:
        raise RuntimeError(
            f"background_shell summary write failed for {path}: "
            f"{_shell_payload(written).get('stderr', '')[:200]}"
        )
    return path


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    samples = sorted(values)
    if len(samples) == 1:
        return samples[0]
    rank = (pct / 100.0) * (len(samples) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(samples) - 1)
    frac = rank - lo
    return samples[lo] * (1 - frac) + samples[hi] * frac


# ---- T1 golden -------------------------------------------------------------


GOLDEN_LAUNCH_COUNT = 3
GOLDEN_SLEEP_S = 5


async def run_background_shell_golden_probe(
    *,
    metadata: ExecutionMetadata,
    emit: EmitStreamEvent,
    call_tool: CallTool,
    record_tool_check: RecordToolCheck,
) -> str:
    """T1: N concurrent background launches; wait for natural exit."""
    started = time.perf_counter()

    async def _one(index: int) -> dict[str, Any]:
        t0 = time.perf_counter()
        result = await call_tool(
            shell_tool,
            {
                "command": f"sleep {GOLDEN_SLEEP_S}; echo done-{index}",
                "timeout": 120,
            },
            metadata,
            emit,
            background_task_id=_bg_id(f"golden-{index}"),
        )
        record_tool_check(f"tool.shell.background_shell.golden.{index}", result)
        payload = _shell_payload(result)
        return {
            "index": index,
            "duration_s": time.perf_counter() - t0,
            "exit_code": int(payload.get("exit_code", -1)),
            "status": str(payload.get("status") or ""),
            "stdout_excerpt": str(payload.get("stdout") or "")[:200],
            "is_error": bool(result.is_error),
            "shell_metadata": _shell_metadata(result),
        }

    results = await asyncio.gather(
        *(_one(i) for i in range(GOLDEN_LAUNCH_COUNT)),
        return_exceptions=False,
    )
    summary = {
        "schema": SUMMARY_SCHEMA,
        "mode": "golden",
        "launch_count": GOLDEN_LAUNCH_COUNT,
        "sleep_s": GOLDEN_SLEEP_S,
        "duration_s": time.perf_counter() - started,
        "launches": results,
    }
    return await _write_summary(
        path=GOLDEN_SUMMARY,
        payload=summary,
        metadata=metadata,
        emit=emit,
        call_tool=call_tool,
        record_tool_check=record_tool_check,
    )


# ---- T2 cancel -------------------------------------------------------------


CANCEL_LAUNCH_COUNT = 3
CANCEL_AFTER_S = 1.0
CANCEL_SLEEP_S = 30


async def run_background_shell_cancel_probe(
    *,
    metadata: ExecutionMetadata,
    emit: EmitStreamEvent,
    call_tool: CallTool,
    record_tool_check: RecordToolCheck,
) -> str:
    """T2: launch + cancel mid-flight via asyncio.wait_for."""
    started = time.perf_counter()

    async def _one(index: int) -> dict[str, Any]:
        t0 = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                call_tool(
                    shell_tool,
                    {
                        "command": (
                            f"sleep {CANCEL_SLEEP_S}; echo done-{index}"
                        ),
                        "timeout": 120,
                    },
                    metadata,
                    emit,
                    background_task_id=_bg_id(f"cancel-{index}"),
                ),
                timeout=CANCEL_AFTER_S,
            )
            record_tool_check(
                f"tool.shell.background_shell.cancel.{index}", result
            )
            payload = _shell_payload(result)
            return {
                "index": index,
                "duration_s": time.perf_counter() - t0,
                "cancelled": False,
                "exit_code": int(payload.get("exit_code", -1)),
                "status": str(payload.get("status") or ""),
                "is_error": bool(result.is_error),
                "shell_metadata": _shell_metadata(result),
            }
        except asyncio.TimeoutError:
            return {
                "index": index,
                "duration_s": time.perf_counter() - t0,
                "cancelled": True,
                "exit_code": None,
                "status": "cancelled",
                "is_error": False,
                "shell_metadata": {},
            }

    cancel_results = await asyncio.gather(
        *(_one(i) for i in range(CANCEL_LAUNCH_COUNT)),
        return_exceptions=False,
    )

    # AC-3: post-cancel foreground shell mount latency budget.
    fg_t0 = time.perf_counter()
    fg_result = await call_tool(
        shell_tool,
        {"command": "echo post-cancel-ok", "timeout": 30},
        metadata,
        emit,
    )
    record_tool_check("tool.shell.background_shell.cancel.post_foreground", fg_result)
    post_fg = {
        "duration_s": time.perf_counter() - fg_t0,
        "is_error": bool(fg_result.is_error),
        "shell_metadata": _shell_metadata(fg_result),
    }

    summary = {
        "schema": SUMMARY_SCHEMA,
        "mode": "cancel",
        "launch_count": CANCEL_LAUNCH_COUNT,
        "cancel_after_s": CANCEL_AFTER_S,
        "sleep_s": CANCEL_SLEEP_S,
        "duration_s": time.perf_counter() - started,
        "launches": cancel_results,
        "post_cancel_foreground": post_fg,
    }
    return await _write_summary(
        path=CANCEL_SUMMARY,
        payload=summary,
        metadata=metadata,
        emit=emit,
        call_tool=call_tool,
        record_tool_check=record_tool_check,
    )


# ---- T3 interleave --------------------------------------------------------


INTERLEAVE_FOREGROUND_COUNT = 5
INTERLEAVE_BACKGROUND_SLEEP_S = 30


async def run_background_shell_interleave_probe(
    *,
    metadata: ExecutionMetadata,
    emit: EmitStreamEvent,
    call_tool: CallTool,
    record_tool_check: RecordToolCheck,
) -> str:
    """T3: 1 long background + M foreground shells; capture fg mount p95."""
    started = time.perf_counter()

    bg_task = asyncio.create_task(
        call_tool(
            shell_tool,
            {
                "command": (
                    f"sleep {INTERLEAVE_BACKGROUND_SLEEP_S}; echo bg-done"
                ),
                "timeout": 120,
            },
            metadata,
            emit,
            background_task_id=_bg_id("interleave-bg"),
        )
    )

    foreground_records: list[dict[str, Any]] = []
    try:
        for index in range(INTERLEAVE_FOREGROUND_COUNT):
            t0 = time.perf_counter()
            fg_result = await call_tool(
                shell_tool,
                {"command": f"echo fg-{index}", "timeout": 30},
                metadata,
                emit,
            )
            record_tool_check(
                f"tool.shell.background_shell.interleave.fg.{index}", fg_result
            )
            duration = time.perf_counter() - t0
            shell_meta = _shell_metadata(fg_result)
            mount_s = (
                float(shell_meta["timings"].get("command_exec.mount_workspace_s", 0.0))
                or duration
            )
            foreground_records.append(
                {
                    "index": index,
                    "wall_duration_s": duration,
                    "mount_s": mount_s,
                    "is_error": bool(fg_result.is_error),
                    "shell_metadata": shell_meta,
                }
            )
    finally:
        try:
            bg_result = await asyncio.wait_for(
                bg_task, timeout=INTERLEAVE_BACKGROUND_SLEEP_S + 30
            )
            bg_payload = _shell_payload(bg_result)
            bg_record = {
                "cancelled": False,
                "exit_code": int(bg_payload.get("exit_code", -1)),
                "status": str(bg_payload.get("status") or ""),
                "is_error": bool(bg_result.is_error),
                "shell_metadata": _shell_metadata(bg_result),
            }
        except asyncio.TimeoutError:
            bg_task.cancel()
            bg_record = {
                "cancelled": True,
                "exit_code": None,
                "status": "cancelled",
                "is_error": False,
                "shell_metadata": {},
            }

    p95_mount_s = _percentile(
        [r["mount_s"] for r in foreground_records], 95.0
    )

    summary = {
        "schema": SUMMARY_SCHEMA,
        "mode": "interleave",
        "foreground_count": INTERLEAVE_FOREGROUND_COUNT,
        "background_sleep_s": INTERLEAVE_BACKGROUND_SLEEP_S,
        "duration_s": time.perf_counter() - started,
        "foreground_p95_mount_s": p95_mount_s,
        "foreground": foreground_records,
        "background": bg_record,
    }
    return await _write_summary(
        path=INTERLEAVE_SUMMARY,
        payload=summary,
        metadata=metadata,
        emit=emit,
        call_tool=call_tool,
        record_tool_check=record_tool_check,
    )


# ---- T5 executor exhaustion -----------------------------------------------


EXHAUSTION_LAUNCH_COUNT = 80
EXHAUSTION_BACKGROUND_SLEEP_S = 60
EXHAUSTION_CANCEL_DEADLINE_S = 2.0


async def run_background_shell_exhaustion_probe(
    *,
    metadata: ExecutionMetadata,
    emit: EmitStreamEvent,
    call_tool: CallTool,
    record_tool_check: RecordToolCheck,
) -> str:
    """T5: N parallel launches cancelled in unison; assert AC-14 read budget."""
    started = time.perf_counter()

    async def _launch_then_cancel(index: int) -> str:
        try:
            await asyncio.wait_for(
                call_tool(
                    shell_tool,
                    {
                        "command": (
                            f"sleep {EXHAUSTION_BACKGROUND_SLEEP_S}; "
                            f"echo done-{index}"
                        ),
                        "timeout": EXHAUSTION_BACKGROUND_SLEEP_S + 30,
                    },
                    metadata,
                    emit,
                    background_task_id=_bg_id(f"exhaust-{index}"),
                ),
                timeout=EXHAUSTION_CANCEL_DEADLINE_S,
            )
            return "ok"
        except asyncio.TimeoutError:
            return "cancelled"
        except Exception as exc:  # noqa: BLE001 — capture-all for telemetry
            return f"error:{type(exc).__name__}"

    outcomes = await asyncio.gather(
        *(_launch_then_cancel(i) for i in range(EXHAUSTION_LAUNCH_COUNT)),
        return_exceptions=False,
    )

    # AC-14: a follow-up foreground read_file must complete in < 1 s, proving
    # the daemon RPC dispatcher executor is NOT shared with ShellExecutor.
    # Seed a target file (write via foreground shell) so the read doesn't
    # depend on SWE-EVO repo layout.
    seed_path = f"{ROOT}/exhaustion/probe.txt"
    seed_result = await call_tool(
        shell_tool,
        {
            "command": (
                f"mkdir -p $(dirname {seed_path}) && "
                f"echo probe-ok > {seed_path}"
            ),
            "timeout": 30,
        },
        metadata,
        emit,
    )
    record_tool_check("tool.shell.background_shell.exhaustion.seed", seed_result)
    fg_t0 = time.perf_counter()
    read_result = await call_tool(
        read_file_tool,
        {"file_path": seed_path},
        metadata,
        emit,
    )
    record_tool_check(
        "tool.read_file.background_shell.exhaustion.read", read_result
    )
    fg_elapsed = time.perf_counter() - fg_t0

    summary = {
        "schema": SUMMARY_SCHEMA,
        "mode": "exhaustion",
        "launch_count": EXHAUSTION_LAUNCH_COUNT,
        "cancel_deadline_s": EXHAUSTION_CANCEL_DEADLINE_S,
        "duration_s": time.perf_counter() - started,
        "outcomes": outcomes,
        "cancelled_count": sum(1 for o in outcomes if o == "cancelled"),
        "ok_count": sum(1 for o in outcomes if o == "ok"),
        "error_count": sum(1 for o in outcomes if o.startswith("error:")),
        "post_exhaustion_read_s": fg_elapsed,
        "post_exhaustion_read_error": bool(read_result.is_error),
    }
    return await _write_summary(
        path=EXHAUSTION_SUMMARY,
        payload=summary,
        metadata=metadata,
        emit=emit,
        call_tool=call_tool,
        record_tool_check=record_tool_check,
    )


# ---- T6 partial-write cancel ----------------------------------------------


PARTIAL_WRITE_DD_COUNT_MB = 800
PARTIAL_WRITE_CANCEL_S = 2.0


async def run_background_shell_partial_write_cancel_probe(
    *,
    metadata: ExecutionMetadata,
    emit: EmitStreamEvent,
    call_tool: CallTool,
    record_tool_check: RecordToolCheck,
) -> str:
    """T6: cancel a long ``dd`` mid-write; assert no leaked OCC publish."""
    started = time.perf_counter()
    target = f"{ROOT}/partial_write/tracked.bin"

    # Seed the parent directory via a separate foreground shell. We have to
    # also create a sentinel file inside the dir because OCC only persists
    # files, not empty directories — without the sentinel the dd shell would
    # land in a fresh lease whose snapshot has lost the dir.
    seed_result = await call_tool(
        shell_tool,
        {
            "command": (
                f"mkdir -p $(dirname {target}) && "
                f"touch $(dirname {target})/.sentinel"
            ),
            "timeout": 30,
        },
        metadata,
        emit,
    )
    record_tool_check(
        "tool.shell.background_shell.partial_write.seed_dir", seed_result
    )

    dd_command = (
        f"for _ in 1; do "
        f"dd if=/dev/urandom of={target} "
        f"bs=1M count={PARTIAL_WRITE_DD_COUNT_MB} status=none; "
        f"done"
    )
    dd_completed = False
    try:
        result = await asyncio.wait_for(
            call_tool(
                shell_tool,
                {"command": dd_command, "timeout": 180},
                metadata,
                emit,
                background_task_id=_bg_id("partial-write"),
            ),
            timeout=PARTIAL_WRITE_CANCEL_S,
        )
        record_tool_check(
            "tool.shell.background_shell.partial_write.dd", result
        )
        dd_completed = True
    except asyncio.TimeoutError:
        pass

    # ``read_file_tool`` raises is_error=True when the file doesn't exist;
    # pass allow_error so the probe can record the absence and assert on it
    # in the test instead of crashing the executor.
    read_result = await call_tool(
        read_file_tool,
        {"file_path": target},
        metadata,
        emit,
        allow_error=True,
    )

    summary = {
        "schema": SUMMARY_SCHEMA,
        "mode": "partial_write_cancel",
        "target": target,
        "dd_count_mb": PARTIAL_WRITE_DD_COUNT_MB,
        "cancel_deadline_s": PARTIAL_WRITE_CANCEL_S,
        "duration_s": time.perf_counter() - started,
        "dd_completed_before_cancel": dd_completed,
        "tracked_exists_after_cancel": not read_result.is_error,
        "read_is_error": bool(read_result.is_error),
    }
    return await _write_summary(
        path=PARTIAL_WRITE_SUMMARY,
        payload=summary,
        metadata=metadata,
        emit=emit,
        call_tool=call_tool,
        record_tool_check=record_tool_check,
    )


# ---- T7 cancel-during-maintenance -----------------------------------------


async def run_background_shell_maintenance_probe(
    *,
    metadata: ExecutionMetadata,
    emit: EmitStreamEvent,
    call_tool: CallTool,
    record_tool_check: RecordToolCheck,
) -> str:
    """T7: short shell + maintenance; verify OCC consistency after."""
    started = time.perf_counter()
    target = f"{ROOT}/maintenance/maint_test.txt"
    target_relative = target.removeprefix(f"{WORKSPACE_ROOT}/")
    result = await call_tool(
        shell_tool,
        {
            "command": (
                f"mkdir -p $(dirname {target}) && "
                f"echo 'maintenance-test' > {target} && "
                f"sleep 0.5"
            ),
            "timeout": 60,
        },
        metadata,
        emit,
        background_task_id=_bg_id("maintenance"),
    )
    record_tool_check("tool.shell.background_shell.maintenance.short_write", result)
    payload = _shell_payload(result)
    changed = list(_shell_metadata(result)["changed_paths"])

    read_result = await call_tool(
        read_file_tool,
        {"file_path": target},
        metadata,
        emit,
        allow_error=True,
    )
    record_tool_check(
        "tool.read_file.background_shell.maintenance.fg_check", read_result
    )

    summary = {
        "schema": SUMMARY_SCHEMA,
        "mode": "cancel_during_maintenance",
        "target": target,
        "target_relative": target_relative,
        "duration_s": time.perf_counter() - started,
        "shell_is_error": bool(result.is_error),
        "shell_exit_code": int(payload.get("exit_code", -1)),
        "changed_paths": changed,
        "target_in_changed_paths": (
            target_relative in changed or target in changed
        ),
        "read_exists": not read_result.is_error,
        "read_content_contains_marker": "maintenance-test"
        in str(read_result.output or ""),
    }
    return await _write_summary(
        path=MAINTENANCE_SUMMARY,
        payload=summary,
        metadata=metadata,
        emit=emit,
        call_tool=call_tool,
        record_tool_check=record_tool_check,
    )


# ---- T8 late-cancel race ---------------------------------------------------


async def run_background_shell_late_cancel_probe(
    *,
    metadata: ExecutionMetadata,
    emit: EmitStreamEvent,
    call_tool: CallTool,
    record_tool_check: RecordToolCheck,
) -> str:
    """T8: await full completion; late cancel must not mutate result."""
    started = time.perf_counter()
    result = await call_tool(
        shell_tool,
        {"command": "sleep 1; echo done-late-cancel", "timeout": 60},
        metadata,
        emit,
        background_task_id=_bg_id("late-cancel"),
    )
    record_tool_check("tool.shell.background_shell.late_cancel.short", result)
    payload = _shell_payload(result)

    summary = {
        "schema": SUMMARY_SCHEMA,
        "mode": "late_cancel_race",
        "duration_s": time.perf_counter() - started,
        "shell_is_error": bool(result.is_error),
        "exit_code": int(payload.get("exit_code", -1)),
        "status": str(payload.get("status") or ""),
        "stdout_contains_marker": "done-late-cancel"
        in str(payload.get("stdout") or ""),
        "shell_metadata": _shell_metadata(result),
    }
    return await _write_summary(
        path=LATE_CANCEL_SUMMARY,
        payload=summary,
        metadata=metadata,
        emit=emit,
        call_tool=call_tool,
        record_tool_check=record_tool_check,
    )


__all__ = [
    "GOLDEN_SUMMARY",
    "CANCEL_SUMMARY",
    "INTERLEAVE_SUMMARY",
    "EXHAUSTION_SUMMARY",
    "PARTIAL_WRITE_SUMMARY",
    "MAINTENANCE_SUMMARY",
    "LATE_CANCEL_SUMMARY",
    "SUMMARY_SCHEMA",
    "run_background_shell_golden_probe",
    "run_background_shell_cancel_probe",
    "run_background_shell_interleave_probe",
    "run_background_shell_exhaustion_probe",
    "run_background_shell_partial_write_cancel_probe",
    "run_background_shell_maintenance_probe",
    "run_background_shell_late_cancel_probe",
]
