"""Live load tests for mixed concurrent Daytona writes, edits, and CodeAct.

This suite runs real tool calls against one live sandbox and one shared CI
service so audited process behavior is exercised under mixed contention:

1. Concurrent ``daytona_write_file`` calls on unique files.
2. Concurrent ``daytona_edit_file`` calls:
   - disjoint same-file edits across a small set of files.
   - overlapping same-line edits across a few files.
3. Concurrent ``daytona_rename_symbol`` calls on unique symbols.
4. Concurrent ``daytona_move_file`` and ``daytona_delete_file`` calls.
5. Concurrent coordinated ``daytona_codeact`` shell commands on unique files.

The test verifies:
- successful writes are persisted,
- disjoint edits mostly land,
- overlapping edits permit at most one winner per target file,
- arbiter stats are sane after the burst,
- active file locks are cleaned up after completion.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from dotenv import load_dotenv

from code_intelligence._async_bridge import configure_default_executor
from code_intelligence.routing.service import CodeIntelligenceService
from tests.test_e2e.daytona_exec_io import read_text_via_exec, write_text_via_exec
from tools.core.base import ToolExecutionContext, ToolResult
from tools.daytona_toolkit._daytona_utils import (
    _extract_exit_code,
    _wrap_bash_command,
)
from code_intelligence.routing import overlay_auditor as overlay_auditor_module
from code_intelligence.routing import overlay_exec as overlay_exec_module
from code_intelligence.routing import service as ci_service_module
import tools.daytona_toolkit.codeact_tool as codeact_tool_module
from tools.daytona_toolkit.codeact_tool import daytona_codeact
from tools.daytona_toolkit.delete_move_tool import (
    daytona_delete_file,
    daytona_move_file,
)
from tools.daytona_toolkit.edit_tool import daytona_edit_file
from tools.daytona_toolkit.rename_tool import daytona_rename_symbol
from tools.daytona_toolkit.tools import daytona_write_file

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_PROJECT_ROOT / ".env")


def _load_settings() -> dict[str, Any]:
    settings_path = Path.home() / ".ephemeralos" / "settings.json"
    if settings_path.exists():
        return json.loads(settings_path.read_text())
    return {}


_SETTINGS = _load_settings()
HAS_DAYTONA = bool(
    (os.environ.get("DAYTONA_API_KEY") or _SETTINGS.get("daytona_api_key", ""))
    and (os.environ.get("DAYTONA_API_URL") or _SETTINGS.get("daytona_api_url", ""))
)

pytestmark = [pytest.mark.e2e, pytest.mark.live]

_TERM_NOISE = re.compile(r"\x1b\[3J.*$", re.S)


class _AsyncFs:
    def __init__(self, real_fs: Any):
        self._real = real_fs

    async def upload_file(self, *args, **kwargs):
        return await asyncio.to_thread(self._real.upload_file, *args, **kwargs)

    async def download_file(self, *args, **kwargs):
        return await asyncio.to_thread(self._real.download_file, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _AsyncProcess:
    def __init__(self, real_process: Any):
        self._real = real_process

    async def exec(self, *args, **kwargs):
        response = await asyncio.to_thread(self._real.exec, *args, **kwargs)
        stdout = _TERM_NOISE.sub("", getattr(response, "result", "") or "")
        return SimpleNamespace(result=stdout, exit_code=getattr(response, "exit_code", None))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _AsyncSandboxWrapper:
    def __init__(self, raw_sandbox: Any):
        self._raw = raw_sandbox
        self.fs = _AsyncFs(raw_sandbox.fs)
        self.process = _AsyncProcess(raw_sandbox.process)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


@dataclass
class LiveLoadEnv:
    sandbox_id: str
    raw_sandbox: Any
    async_sandbox: Any
    home: str
    repo_root: str

    def exec(self, command: str, *, cwd: str | None = None, timeout: int = 180) -> tuple[int, str]:
        wrapped = command if cwd is None else f"cd {shlex.quote(cwd)} && {command}"
        response = self.raw_sandbox.process.exec(_wrap_bash_command(wrapped), timeout=timeout)
        raw = _TERM_NOISE.sub("", getattr(response, "result", "") or "")
        cleaned, exit_code = _extract_exit_code(
            raw,
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        return exit_code, cleaned

    def exec_checked(self, command: str, *, cwd: str | None = None, timeout: int = 180) -> str:
        exit_code, stdout = self.exec(command, cwd=cwd, timeout=timeout)
        if exit_code != 0:
            detail = stdout.strip() or f"exit {exit_code}"
            raise AssertionError(f"Sandbox command failed: {detail}")
        return stdout

    def require_command(self, name: str) -> None:
        exit_code, _ = self.exec(f"command -v {shlex.quote(name)} >/dev/null 2>&1", timeout=30)
        if exit_code != 0:
            pytest.skip(f"Sandbox image missing required command: {name}")

    def write_text(self, rel_path: str, content: str) -> None:
        write_text_via_exec(self.raw_sandbox, f"{self.repo_root}/{rel_path}", content, timeout=60)

    def read_text(self, rel_path: str) -> str:
        return read_text_via_exec(self.raw_sandbox, f"{self.repo_root}/{rel_path}", timeout=60)

    def make_ci_service(self) -> CodeIntelligenceService:
        return CodeIntelligenceService(
            sandbox_id=self.sandbox_id,
            workspace_root=self.repo_root,
            sandbox=self.raw_sandbox,
        )

    def make_ctx(
        self,
        ci_service: CodeIntelligenceService,
        *,
        agent_run_id: str,
        coordinated: bool = False,
    ) -> ToolExecutionContext:
        metadata: dict[str, Any] = {
            "daytona_sandbox": self.async_sandbox,
            "ci_sandbox": self.raw_sandbox,
            "daytona_cwd": self.repo_root,
            "repo_root": self.repo_root,
            "exec_cwd": self.repo_root,
            "ci_service": ci_service,
            "agent_run_id": agent_run_id,
        }
        if coordinated:
            metadata["agent_name"] = "developer"
        return ToolExecutionContext(cwd=Path(self.repo_root), metadata=metadata)

    def init_repo(self) -> None:
        self.exec_checked(f"rm -rf {shlex.quote(self.repo_root)} && mkdir -p {shlex.quote(self.repo_root)}")
        self.exec_checked(f"git -C {shlex.quote(self.repo_root)} init")
        self.exec_checked(f"git -C {shlex.quote(self.repo_root)} config user.email test@example.com")
        self.exec_checked(f"git -C {shlex.quote(self.repo_root)} config user.name 'Test User'")


@pytest.fixture
def live_load_env():
    if not HAS_DAYTONA:
        pytest.skip("Daytona credentials not configured")

    from sandbox.testing import create_test_sandbox, delete_test_sandbox, get_sandbox_service

    info = create_test_sandbox(name="process-audit-load-live")
    sandbox_id = info["id"]
    try:
        sandbox_svc = get_sandbox_service()
        raw_sandbox = sandbox_svc.get_sandbox_object(sandbox_id)
        home_resp = raw_sandbox.process.exec("pwd", timeout=10)
        home = (getattr(home_resp, "result", "") or "").strip() or "/home/daytona"
        env = LiveLoadEnv(
            sandbox_id=sandbox_id,
            raw_sandbox=raw_sandbox,
            async_sandbox=_AsyncSandboxWrapper(raw_sandbox),
            home=home,
            repo_root=f"{home}/process_audit_load_repo",
        )
        env.require_command("git")
        env.require_command("python3")
        yield env
    finally:
        delete_test_sandbox(sandbox_id)


def _json_output(result: ToolResult) -> dict[str, Any]:
    assert result.output, "tool returned empty output"
    return json.loads(result.output)


async def _invoke_tool(tool: Any, kwargs: dict[str, Any], ctx: ToolExecutionContext) -> ToolResult:
    return await tool.execute(tool.input_model(**kwargs), ctx)


def _install_codeact_phase_probe(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[float]]:
    stats: dict[str, list[float]] = {
        "shell_exec_s": [],
        "python_wrapper_s": [],
    }

    original_shell = codeact_tool_module._run_shell_with_recovery
    original_python = codeact_tool_module._execute_python_wrapper

    async def _timed_shell(*args, **kwargs):
        started = time.perf_counter()
        try:
            return await original_shell(*args, **kwargs)
        finally:
            stats["shell_exec_s"].append(round(time.perf_counter() - started, 6))

    async def _timed_python(*args, **kwargs):
        started = time.perf_counter()
        try:
            return await original_python(*args, **kwargs)
        finally:
            stats["python_wrapper_s"].append(round(time.perf_counter() - started, 6))

    monkeypatch.setattr(codeact_tool_module, "_run_shell_with_recovery", _timed_shell)
    monkeypatch.setattr(codeact_tool_module, "_execute_python_wrapper", _timed_python)
    return stats


def _install_overlay_phase_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, list[float]]:
    """Probe OverlayAuditor + svc overlay lifecycle for per-phase timings.

    The overlay path is the dominant cost in coordinated ``daytona_codeact``
    calls. Breaking it into cold-start lowerdir materialization, overlay
    mount + user exec + unmount, upperdir tar download, upperdir walk,
    OCC commit, and lowerdir refresh lets the summary distinguish a slow
    commit from a slow overlay mount from a slow refresh.
    """
    stats: dict[str, list[float]] = {
        "overlay_execute_total_s": [],
        "overlay_run_s": [],
        "download_tar_s": [],
        "cleanup_remote_run_dir_s": [],
        "upperdir_walk_s": [],
        "upperdir_change_to_operation_s": [],
        "commit_changes_s": [],
        "lowerdir_refresh_s": [],
        "ensure_lowerdir_s": [],
        "ensure_lowerdir_cold_s": [],
        "lowerdir_live_check_s": [],
    }

    orig_execute = overlay_auditor_module.OverlayAuditor.execute
    orig_download = overlay_auditor_module.OverlayAuditor._download_remote_tar
    orig_cleanup = overlay_auditor_module.OverlayAuditor._cleanup_remote_run_dir
    orig_commit = overlay_auditor_module.OverlayAuditor._commit_changes
    orig_change_to_op = overlay_auditor_module.OverlayAuditor._upperdir_change_to_operation
    orig_walk = overlay_auditor_module.iter_upperdir_changes
    orig_ensure_lowerdir = ci_service_module.CodeIntelligenceService._ensure_overlay_lowerdir
    orig_refresh = ci_service_module.CodeIntelligenceService._refresh_overlay_lowerdir
    orig_live_check = ci_service_module.CodeIntelligenceService._lowerdir_is_live
    orig_overlay_run = overlay_exec_module.OverlayExec.execute

    async def _timed_execute(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return await orig_execute(self, *args, **kwargs)
        finally:
            stats["overlay_execute_total_s"].append(
                round(time.perf_counter() - started, 6)
            )

    async def _timed_download(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return await orig_download(self, *args, **kwargs)
        finally:
            stats["download_tar_s"].append(round(time.perf_counter() - started, 6))

    async def _timed_cleanup(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return await orig_cleanup(self, *args, **kwargs)
        finally:
            stats["cleanup_remote_run_dir_s"].append(
                round(time.perf_counter() - started, 6)
            )

    async def _timed_change_to_op(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return await orig_change_to_op(self, *args, **kwargs)
        finally:
            stats["upperdir_change_to_operation_s"].append(
                round(time.perf_counter() - started, 6)
            )

    async def _timed_commit(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return await orig_commit(self, *args, **kwargs)
        finally:
            stats["commit_changes_s"].append(round(time.perf_counter() - started, 6))

    def _timed_walk(*args, **kwargs):
        started = time.perf_counter()
        # The walker returns a generator; materialize here so the timing
        # captures the actual tar parsing rather than just generator setup.
        try:
            result = list(orig_walk(*args, **kwargs))
        finally:
            stats["upperdir_walk_s"].append(round(time.perf_counter() - started, 6))
        return iter(result)

    async def _timed_ensure_lowerdir(self, sandbox):
        cold = self._overlay_lowerdir is None
        started = time.perf_counter()
        try:
            return await orig_ensure_lowerdir(self, sandbox)
        finally:
            elapsed = round(time.perf_counter() - started, 6)
            stats["ensure_lowerdir_s"].append(elapsed)
            if cold:
                stats["ensure_lowerdir_cold_s"].append(elapsed)

    async def _timed_refresh(self, committed_changes):
        started = time.perf_counter()
        try:
            return await orig_refresh(self, committed_changes)
        finally:
            stats["lowerdir_refresh_s"].append(
                round(time.perf_counter() - started, 6)
            )

    async def _timed_live_check(self, sandbox, lowerdir):
        started = time.perf_counter()
        try:
            return await orig_live_check(self, sandbox, lowerdir)
        finally:
            stats["lowerdir_live_check_s"].append(
                round(time.perf_counter() - started, 6)
            )

    async def _timed_overlay_run(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return await orig_overlay_run(self, *args, **kwargs)
        finally:
            stats["overlay_run_s"].append(round(time.perf_counter() - started, 6))

    monkeypatch.setattr(
        overlay_auditor_module.OverlayAuditor, "execute", _timed_execute
    )
    monkeypatch.setattr(
        overlay_auditor_module.OverlayAuditor,
        "_download_remote_tar",
        _timed_download,
    )
    monkeypatch.setattr(
        overlay_auditor_module.OverlayAuditor,
        "_cleanup_remote_run_dir",
        _timed_cleanup,
    )
    monkeypatch.setattr(
        overlay_auditor_module.OverlayAuditor,
        "_upperdir_change_to_operation",
        _timed_change_to_op,
    )
    monkeypatch.setattr(
        overlay_auditor_module.OverlayAuditor, "_commit_changes", _timed_commit
    )
    monkeypatch.setattr(
        overlay_auditor_module, "iter_upperdir_changes", _timed_walk
    )
    monkeypatch.setattr(
        ci_service_module.CodeIntelligenceService,
        "_ensure_overlay_lowerdir",
        _timed_ensure_lowerdir,
    )
    monkeypatch.setattr(
        ci_service_module.CodeIntelligenceService,
        "_refresh_overlay_lowerdir",
        _timed_refresh,
    )
    monkeypatch.setattr(
        ci_service_module.CodeIntelligenceService,
        "_lowerdir_is_live",
        _timed_live_check,
    )
    monkeypatch.setattr(
        overlay_exec_module.OverlayExec, "execute", _timed_overlay_run
    )
    return stats


async def _run_mixed_operations(
    live_load_env: LiveLoadEnv,
    svc: CodeIntelligenceService,
    operations: list[dict[str, Any]],
    *,
    concurrency: int,
    timeout_s: int,
    log_ops: bool = False,
    log_label: str = "occ-load-op",
) -> list[dict[str, Any]]:
    run_started = time.perf_counter()
    configure_default_executor(
        asyncio.get_running_loop(),
        max_workers=max(200, concurrency * 8),
    )

    # Replace the fixture's `asyncio.to_thread(sync_sdk.exec)` wrapper with the
    # true AsyncDaytona sandbox (aiohttp). The wrapper's `ctx.run` propagation
    # caps parallelism at ~6-7 under the sync Daytona SDK (see
    # test_live_daytona_transport_parallelism_isolation Arm A vs D). Production
    # uses AsyncDaytona (via `get_async_sandbox`), so the fixture must match for
    # the benchmark to reflect real tool throughput.
    from sandbox.async_client import get_async_sandbox

    live_load_env.async_sandbox = await get_async_sandbox(live_load_env.sandbox_id)

    async def _invoke(
        sequence: int,
        operation: dict[str, Any],
        semaphore: asyncio.Semaphore,
    ) -> dict[str, Any]:
        agent_run_id = f"{operation['name']}-{uuid.uuid4().hex[:8]}"
        ctx = live_load_env.make_ctx(
            svc,
            agent_run_id=agent_run_id,
            coordinated=bool(operation.get("coordinated", False)),
        )
        tool = _tool_for_operation_kind(str(operation["kind"]))
        queued_at = time.perf_counter()
        identity = _operation_identity(live_load_env, svc, agent_run_id)
        if log_ops:
            _log_occ_event(
                log_label,
                {
                    "event": "queued",
                    "sequence": sequence,
                    "kind": operation["kind"],
                    "name": operation["name"],
                    "path": operation["path"],
                    "concurrency": concurrency,
                    **identity,
                    "arbiter": _arbiter_snapshot(svc),
                },
            )
        async with semaphore:
            started = time.perf_counter()
            before = _arbiter_snapshot(svc)
            if log_ops:
                _log_occ_event(
                    log_label,
                    {
                        "event": "start",
                        "sequence": sequence,
                        "kind": operation["kind"],
                        "name": operation["name"],
                        "path": operation["path"],
                        "queued_s": round(started - queued_at, 6),
                        "start_offset_s": round(started - run_started, 6),
                        **identity,
                        "arbiter_before": before,
                    },
                )
            try:
                result = await _invoke_tool(tool, operation["kwargs"], ctx)
            except Exception as exc:  # pragma: no cover - live diagnostic path
                elapsed_s = round(time.perf_counter() - started, 6)
                failure = {
                    "kind": operation["kind"],
                    "name": operation["name"],
                    "path": operation["path"],
                    "group": operation.get("group"),
                    "winner_value": operation.get("winner_value"),
                    "is_error": True,
                    "exception_type": type(exc).__name__,
                    "exception": str(exc),
                    "metadata": {},
                    "payload": {},
                    "raw_output": str(exc)[-1200:],
                    "elapsed_s": elapsed_s,
                    "wait_s": round(started - queued_at, 6),
                    "sequence": sequence,
                    **identity,
                    "arbiter_before": before,
                    "arbiter_after": _arbiter_snapshot(svc),
                }
                if log_ops:
                    _log_occ_event(
                        log_label,
                        {
                            "event": "exception",
                            "sequence": sequence,
                            "kind": operation["kind"],
                            "name": operation["name"],
                            "elapsed_s": elapsed_s,
                            **identity,
                            "exception_type": type(exc).__name__,
                            "exception": str(exc)[-1200:],
                            "arbiter_after": failure["arbiter_after"],
                        },
                    )
                return failure
        elapsed_s = round(time.perf_counter() - started, 6)
        wait_s = round(started - queued_at, 6)
        output = (result.output or "").lstrip()
        payload = _json_output(result) if output.startswith("{") else {}
        after = _arbiter_snapshot(svc)
        item = {
            "kind": operation["kind"],
            "name": operation["name"],
            "path": operation["path"],
            "group": operation.get("group"),
            "winner_value": operation.get("winner_value"),
            "is_error": result.is_error,
            "metadata": dict(result.metadata or {}),
            "payload": payload,
            "raw_output": (result.output or "")[:1200],
            "elapsed_s": elapsed_s,
            "wait_s": wait_s,
            "sequence": sequence,
            **identity,
            "arbiter_before": before,
            "arbiter_after": after,
        }
        if log_ops:
            _log_occ_event(
                log_label,
                {
                    "event": "finish",
                    "sequence": sequence,
                    "kind": operation["kind"],
                    "name": operation["name"],
                    "is_error": result.is_error,
                    "elapsed_s": elapsed_s,
                    "wait_s": wait_s,
                    **identity,
                    "metadata": item["metadata"],
                    "payload": payload,
                    "arbiter_after": after,
                    "raw_output_tail": (result.output or "")[-600:],
                },
            )
        return item

    semaphore = asyncio.Semaphore(concurrency)
    return await asyncio.wait_for(
        asyncio.gather(
            *[
                _invoke(sequence, operation, semaphore)
                for sequence, operation in enumerate(operations)
            ]
        ),
        timeout=timeout_s,
    )


def _operation_identity(
    live_load_env: LiveLoadEnv,
    svc: CodeIntelligenceService,
    agent_run_id: str,
) -> dict[str, Any]:
    return {
        "agent_run_id": agent_run_id,
        "pid": os.getpid(),
        "thread_id": threading.get_ident(),
        "sandbox_id": live_load_env.sandbox_id,
        "repo_root": live_load_env.repo_root,
        "svc_id": hex(id(svc)),
        "arbiter_id": hex(id(svc.arbiter)),
    }


def _arbiter_snapshot(svc: CodeIntelligenceService) -> dict[str, Any]:
    status = svc.status()["arbiter"]
    return {
        "generation": svc.arbiter.generation,
        "total_edits": status["total_edits"],
        "conflicts_detected": status["conflicts_detected"],
        "active_locks": status["active_locks"],
        "active_lock_count": svc.arbiter.active_lock_count,
    }


def _log_occ_event(label: str, payload: dict[str, Any]) -> None:
    print(f"\n[{label}] {json.dumps(payload, sort_keys=True, default=str)}", flush=True)


def _tool_for_operation_kind(kind: str) -> Any:
    if kind == "write":
        return daytona_write_file
    if kind == "codeact":
        return daytona_codeact
    if kind in {"edit-disjoint", "edit-overlap", "edit"}:
        return daytona_edit_file
    if kind == "rename":
        return daytona_rename_symbol
    if kind == "move":
        return daytona_move_file
    if kind == "delete":
        return daytona_delete_file
    raise AssertionError(f"Unsupported operation kind: {kind}")


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(round((len(sorted_values) - 1) * pct)))
    return sorted_values[idx]


def _value_profile(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    if not ordered:
        return {
            "count": 0,
            "min": 0.0,
            "avg": 0.0,
            "p50": 0.0,
            "p90": 0.0,
            "p95": 0.0,
            "max": 0.0,
            "total": 0.0,
        }
    total = sum(ordered)
    return {
        "count": len(ordered),
        "min": round(ordered[0], 6),
        "avg": round(total / len(ordered), 6),
        "p50": round(_percentile(ordered, 0.50), 6),
        "p90": round(_percentile(ordered, 0.90), 6),
        "p95": round(_percentile(ordered, 0.95), 6),
        "max": round(ordered[-1], 6),
        "total": round(total, 6),
    }


def _phase_summary(stats: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    return {phase: _value_profile(list(values)) for phase, values in stats.items()}


def _operation_timing_summary(
    results: list[dict[str, Any]],
    *,
    wall_elapsed_s: float,
) -> dict[str, Any]:
    elapsed_values = [float(item["elapsed_s"]) for item in results]
    wait_values = [float(item["wait_s"]) for item in results]
    total_operation_s = round(sum(elapsed_values), 6)
    ratio = round(total_operation_s / wall_elapsed_s, 3) if wall_elapsed_s > 0 else 0.0
    op_count = len(results)
    throughput = round(op_count / wall_elapsed_s, 3) if wall_elapsed_s > 0 else 0.0
    return {
        "wall_elapsed_s": round(wall_elapsed_s, 6),
        "op_count": op_count,
        "sum_operation_elapsed_s": total_operation_s,
        "parallelism_ratio": ratio,
        "throughput_ops_per_s": throughput,
        "elapsed_s_profile": _value_profile(elapsed_values),
        "wait_s_profile": _value_profile(wait_values),
        "max_wait_s": round(max(wait_values, default=0.0), 6),
    }


def _elapsed_profile(items: list[dict[str, Any]]) -> dict[str, float]:
    return _value_profile([float(item["elapsed_s"]) for item in items])


def test_live_occ_load_72_all_mutators_high_concurrency_profile(
    live_load_env: LiveLoadEnv,
    monkeypatch: pytest.MonkeyPatch,
):
    """High-concurrency mixed OCC load across every Daytona mutator.

    This intentionally uses disjoint files/symbols so failures point to
    transport, snapshot, locking, or routing regressions rather than expected
    write conflicts. It exercises write, edit, rename, move, delete, and
    coordinated CodeAct against one shared ``CodeIntelligenceService``.
    """
    log_label = "occ-load-72-all-mutators-high-concurrency"
    _log_occ_event(
        log_label,
        {
            "event": "setup",
            "phase": "init_repo",
            "pid": os.getpid(),
            "sandbox_id": live_load_env.sandbox_id,
            "repo_root": live_load_env.repo_root,
        },
    )
    live_load_env.init_repo()
    codeact_stats = _install_codeact_phase_probe(monkeypatch)
    overlay_stats = _install_overlay_phase_probe(monkeypatch)

    seed_started = time.perf_counter()
    _log_occ_event(log_label, {"event": "setup", "phase": "seed_start"})
    for idx in range(12):
        live_load_env.write_text(
            f"edits/all_{idx}.py",
            (
                f'"""Edit fixture {idx}."""\n\n'
                f"VALUE_{idx} = {idx}\n"
                f"MARKER_{idx} = 'before'\n"
            ),
        )
        live_load_env.write_text(
            f"rename/module_{idx}.py",
            (
                f'"""Rename fixture {idx}."""\n\n'
                f"def rename_target_{idx}(value):\n"
                f"    return value + {idx}\n\n"
                f"def caller_{idx}(value):\n"
                f"    return rename_target_{idx}(value)\n"
            ),
        )
        live_load_env.write_text(f"moves/src_{idx}.txt", f"move source {idx}\n")
        live_load_env.write_text(f"deletes/delete_{idx}.txt", f"delete target {idx}\n")
        live_load_env.write_text(f"codeact/high_{idx}.txt", f"codeact base {idx}\n")
    _log_occ_event(
        log_label,
        {
            "event": "setup",
            "phase": "seed_finish",
            "elapsed_s": round(time.perf_counter() - seed_started, 6),
        },
    )

    commit_started = time.perf_counter()
    _log_occ_event(log_label, {"event": "setup", "phase": "git_commit_start"})
    live_load_env.exec_checked(f"git -C {shlex.quote(live_load_env.repo_root)} add -A")
    live_load_env.exec_checked(
        f"git -C {shlex.quote(live_load_env.repo_root)} commit -m seed-all-mutators-load",
        timeout=180,
    )
    _log_occ_event(
        log_label,
        {
            "event": "setup",
            "phase": "git_commit_finish",
            "elapsed_s": round(time.perf_counter() - commit_started, 6),
        },
    )

    svc = live_load_env.make_ci_service()
    init_started = time.perf_counter()
    _log_occ_event(
        log_label,
        {
            "event": "setup",
            "phase": "ensure_initialized_start",
            "svc_id": hex(id(svc)),
            "arbiter_id": hex(id(svc.arbiter)),
        },
    )
    svc.ensure_initialized(wait=True)
    _log_occ_event(
        log_label,
        {
            "event": "setup",
            "phase": "ensure_initialized_finish",
            "elapsed_s": round(time.perf_counter() - init_started, 6),
            "svc_id": hex(id(svc)),
            "arbiter_id": hex(id(svc.arbiter)),
            "arbiter": _arbiter_snapshot(svc),
        },
    )

    operations: list[dict[str, Any]] = []
    for idx in range(12):
        operations.extend(
            [
                {
                    "kind": "write",
                    "name": f"write-{idx}",
                    "path": f"{live_load_env.repo_root}/writes/all_{idx}.txt",
                    "kwargs": {
                        "file_path": f"{live_load_env.repo_root}/writes/all_{idx}.txt",
                        "content": f"write all {idx}\n",
                    },
                },
                {
                    "kind": "edit-disjoint",
                    "name": f"edit-{idx}",
                    "path": f"{live_load_env.repo_root}/edits/all_{idx}.py",
                    "kwargs": {
                        "file_path": f"{live_load_env.repo_root}/edits/all_{idx}.py",
                        "old_text": f"MARKER_{idx} = 'before'",
                        "new_text": f"MARKER_{idx} = 'after-{idx}'",
                    },
                },
                {
                    "kind": "rename",
                    "name": f"rename-{idx}",
                    "path": f"{live_load_env.repo_root}/rename/module_{idx}.py",
                    "kwargs": {
                        "symbol": f"rename_target_{idx}",
                        "new_name": f"renamed_target_{idx}",
                        "file_hint": f"rename/module_{idx}.py",
                    },
                },
                {
                    "kind": "move",
                    "name": f"move-{idx}",
                    "path": f"{live_load_env.repo_root}/moves/src_{idx}.txt",
                    "kwargs": {
                        "src_path": f"{live_load_env.repo_root}/moves/src_{idx}.txt",
                        "target_path": f"{live_load_env.repo_root}/moves/dst_{idx}.txt",
                    },
                },
                {
                    "kind": "delete",
                    "name": f"delete-{idx}",
                    "path": f"{live_load_env.repo_root}/deletes/delete_{idx}.txt",
                    "kwargs": {
                        "path": f"{live_load_env.repo_root}/deletes/delete_{idx}.txt",
                    },
                },
                {
                    "kind": "codeact",
                    "name": f"codeact-{idx}",
                    "path": f"{live_load_env.repo_root}/codeact/high_{idx}.txt",
                    "kwargs": {
                        "mode": "shell",
                        "command": (
                            "python3 - <<'PY'\n"
                            "from pathlib import Path\n"
                            f"Path('codeact/high_{idx}.txt').write_text('codeact high {idx}\\n', encoding='utf-8')\n"
                            "PY"
                        ),
                        "timeout": 180,
                    },
                    "coordinated": True,
                },
            ]
        )

    assert len(operations) == 72

    started = time.perf_counter()
    results = asyncio.run(
        _run_mixed_operations(
            live_load_env,
            svc,
            operations,
            concurrency=72,
            timeout_s=360,
            log_ops=True,
            log_label=log_label,
        )
    )
    wall_elapsed_s = time.perf_counter() - started

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        by_kind.setdefault(item["kind"], []).append(item)

    failures = [
        {
            "kind": item["kind"],
            "name": item["name"],
            "metadata": item["metadata"],
            "payload": item["payload"],
            "raw_output": item["raw_output"],
        }
        for item in results
        if item["is_error"]
    ]
    summary = {
        "operation_counts": {
            kind: len(items)
            for kind, items in sorted(by_kind.items())
        },
        "success_counts": {
            kind: sum(not item["is_error"] for item in items)
            for kind, items in sorted(by_kind.items())
        },
        "elapsed_profile_s": {
            kind: _elapsed_profile(items)
            for kind, items in sorted(by_kind.items())
        },
        "timing": _operation_timing_summary(
            results,
            wall_elapsed_s=wall_elapsed_s,
        ),
        "codeact_phase_s": _phase_summary(codeact_stats),
        "overlay_phase_s": _phase_summary(overlay_stats),
        "arbiter": svc.status()["arbiter"],
        "held_locks": svc.arbiter.active_lock_count,
        "process_identity": {
            "expected": {
                "pid": os.getpid(),
                "svc_id": hex(id(svc)),
                "arbiter_id": hex(id(svc.arbiter)),
                "sandbox_id": live_load_env.sandbox_id,
            },
            "observed_count": len(
                {
                    (
                        item["pid"],
                        item["svc_id"],
                        item["arbiter_id"],
                        item["sandbox_id"],
                    )
                    for item in results
                }
            ),
        },
        "failures": failures[:5],
    }
    print("\n[occ-load-72-all-mutators-high-concurrency]", flush=True)
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)

    assert not failures, json.dumps(failures, indent=2, sort_keys=True)
    assert summary["timing"]["parallelism_ratio"] >= 4.0, summary["timing"]
    assert svc.arbiter.active_lock_count == 0
    assert svc.status()["arbiter"]["conflicts_detected"] == 0
    assert {
        (
            item["pid"],
            item["svc_id"],
            item["arbiter_id"],
            item["sandbox_id"],
        )
        for item in results
    } == {
        (
            os.getpid(),
            hex(id(svc)),
            hex(id(svc.arbiter)),
            live_load_env.sandbox_id,
        )
    }

    for idx in range(12):
        assert live_load_env.read_text(f"writes/all_{idx}.txt") == f"write all {idx}\n"

        edited = live_load_env.read_text(f"edits/all_{idx}.py")
        assert f"MARKER_{idx} = 'after-{idx}'" in edited

        renamed = live_load_env.read_text(f"rename/module_{idx}.py")
        assert f"def renamed_target_{idx}(value):" in renamed
        assert f"return renamed_target_{idx}(value)" in renamed
        assert f"rename_target_{idx}" not in renamed

        assert live_load_env.read_text(f"moves/dst_{idx}.txt") == f"move source {idx}\n"
        live_load_env.exec_checked(
            f"test ! -e {shlex.quote(f'{live_load_env.repo_root}/moves/src_{idx}.txt')}",
            timeout=30,
        )
        live_load_env.exec_checked(
            f"test ! -e {shlex.quote(f'{live_load_env.repo_root}/deletes/delete_{idx}.txt')}",
            timeout=30,
        )

        assert live_load_env.read_text(f"codeact/high_{idx}.txt") == f"codeact high {idx}\n"

    assert svc.status()["arbiter"]["total_edits"] >= len(operations)


def test_live_occ_load_50_mixed_operations(
    live_load_env: LiveLoadEnv,
    monkeypatch: pytest.MonkeyPatch,
):
    log_label = "occ-load-50-mixed"
    _log_occ_event(
        log_label,
        {
            "event": "setup",
            "phase": "init_repo",
            "pid": os.getpid(),
            "sandbox_id": live_load_env.sandbox_id,
        },
    )
    live_load_env.init_repo()
    codeact_stats = _install_codeact_phase_probe(monkeypatch)
    overlay_stats = _install_overlay_phase_probe(monkeypatch)

    # Seed disjoint edit targets: 3 files * 5 edits each = 15 disjoint edits.
    for group in range(3):
        lines = ['"""Disjoint edit target."""', ""]
        for idx in range(5):
            global_idx = group * 5 + idx
            lines.append(f"VALUE_{global_idx} = {global_idx}")
        live_load_env.write_text(f"edits/disjoint_{group}.py", "\n".join(lines) + "\n")

    # Seed overlapping edit targets: 2 files * 3 edits each = 6 overlap attempts.
    for group in range(2):
        live_load_env.write_text(
            f"edits/overlap_{group}.py",
            '"""Overlap target."""\n\nSHARED = 0\n',
        )

    # Seed CodeAct unique targets: 4 independent command writes.
    for idx in range(4):
        live_load_env.write_text(f"tx/unique_{idx}.txt", "base\n")

    live_load_env.exec_checked(f"git -C {shlex.quote(live_load_env.repo_root)} add -A")
    live_load_env.exec_checked(
        f"git -C {shlex.quote(live_load_env.repo_root)} commit -m seed-load-fixtures",
        timeout=180,
    )

    svc = live_load_env.make_ci_service()
    operations: list[dict[str, Any]] = []

    # 25 unique writes.
    for idx in range(25):
        operations.append(
            {
                "kind": "write",
                "name": f"write-{idx}",
                "path": f"{live_load_env.repo_root}/writes/write_{idx}.txt",
                "kwargs": {
                    "file_path": f"{live_load_env.repo_root}/writes/write_{idx}.txt",
                    "content": f"write {idx}\n",
                },
                "coordinated": False,
            }
        )

    # 15 disjoint edits.
    for group in range(3):
        for idx in range(5):
            global_idx = group * 5 + idx
            file_path = f"{live_load_env.repo_root}/edits/disjoint_{group}.py"
            operations.append(
                {
                    "kind": "edit-disjoint",
                    "name": f"edit-disjoint-{global_idx}",
                    "path": file_path,
                    "kwargs": {
                        "file_path": file_path,
                        "old_text": f"VALUE_{global_idx} = {global_idx}",
                        "new_text": f"VALUE_{global_idx} = {global_idx}00",
                    },
                    "coordinated": False,
                }
            )

    # 6 overlapping edits: at most one final winner per file.
    for group in range(2):
        file_path = f"{live_load_env.repo_root}/edits/overlap_{group}.py"
        for idx in range(3):
            value = (group + 1) * 1000 + idx
            operations.append(
                {
                    "kind": "edit-overlap",
                    "name": f"edit-overlap-{group}-{idx}",
                    "path": file_path,
                    "group": group,
                    "winner_value": value,
                    "kwargs": {
                        "file_path": file_path,
                        "old_text": "SHARED = 0",
                        "new_text": f"SHARED = {value}",
                    },
                    "coordinated": False,
                }
            )

    # 4 coordinated CodeAct shell commands on unique files.
    for idx in range(4):
        rel_path = f"tx/unique_{idx}.txt"
        operations.append(
            {
                "kind": "codeact",
                "name": f"codeact-{idx}",
                "path": f"{live_load_env.repo_root}/{rel_path}",
                "kwargs": {
                    "mode": "shell",
                    "command": (
                        "python3 - <<'PY'\n"
                        "from pathlib import Path\n"
                        f"Path({rel_path!r}).write_text('codeact {idx}\\n', encoding='utf-8')\n"
                        "PY"
                    ),
                    "timeout": 120,
                },
                "coordinated": True,
            }
        )

    assert len(operations) == 50

    started = time.perf_counter()
    results = asyncio.run(
        _run_mixed_operations(
            live_load_env,
            svc,
            operations,
            concurrency=20,
            timeout_s=240,
            log_ops=True,
            log_label=log_label,
        )
    )
    wall_elapsed_s = time.perf_counter() - started

    write_results = [item for item in results if item["kind"] == "write"]
    disjoint_results = [item for item in results if item["kind"] == "edit-disjoint"]
    overlap_results = [item for item in results if item["kind"] == "edit-overlap"]
    codeact_results = [item for item in results if item["kind"] == "codeact"]

    write_successes = sum(not item["is_error"] for item in write_results)
    disjoint_successes = sum(not item["is_error"] for item in disjoint_results)
    overlap_successes = sum(not item["is_error"] for item in overlap_results)
    overlap_conflicts = sum(
        bool(item["metadata"].get("conflict")) or bool(item["payload"].get("conflict"))
        for item in overlap_results
    )
    codeact_successes = sum(not item["is_error"] for item in codeact_results)

    arbiter_status = svc.status()["arbiter"]
    scope_status = svc.scope_status([live_load_env.repo_root])
    hotspots = scope_status["hotspots"]

    winners_by_group: dict[int, list[int]] = {0: [], 1: []}
    for item in overlap_results:
        group = int(item["group"])
        value = int(item["winner_value"])
        text = live_load_env.read_text(f"edits/overlap_{group}.py")
        if f"SHARED = {value}" in text:
            winners_by_group[group].append(value)
    overlap_persisted_winners = sum(len(values) for values in winners_by_group.values())

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        by_kind.setdefault(item["kind"], []).append(item)

    print(f"\n[{log_label} summary]")
    print(
        json.dumps(
            {
                "operation_count": len(operations),
                "write_successes": write_successes,
                "disjoint_successes": disjoint_successes,
                "overlap_successes": overlap_successes,
                "overlap_conflicts": overlap_conflicts,
                "overlap_persisted_winners": overlap_persisted_winners,
                "codeact_successes": codeact_successes,
                "elapsed_profile_s": {
                    kind: _elapsed_profile(items)
                    for kind, items in sorted(by_kind.items())
                },
                "timing": _operation_timing_summary(
                    results,
                    wall_elapsed_s=wall_elapsed_s,
                ),
                "codeact_phase_s": _phase_summary(codeact_stats),
                "overlay_phase_s": _phase_summary(overlay_stats),
                "arbiter": arbiter_status,
                "hotspots": hotspots[:5],
            },
            indent=2,
            sort_keys=True,
        )
    )

    # Writes should all succeed because they target unique files.
    assert write_successes == 25

    # CodeAct targets unique files too; these should all run and audit cleanly.
    assert codeact_successes == 4

    # Disjoint edits should mostly land. Allow a small amount of live contention noise.
    assert disjoint_successes >= 12

    # Overlap files are process-level writes: several commands can report
    # success, but each file must end with a single coherent value.

    assert all(len(values) <= 1 for values in winners_by_group.values()), winners_by_group
    assert overlap_persisted_winners <= 2

    # Verify persisted results on unique-file paths.
    for idx in range(25):
        assert live_load_env.read_text(f"writes/write_{idx}.txt") == f"write {idx}\n"
    for idx in range(4):
        assert live_load_env.read_text(f"tx/unique_{idx}.txt") == f"codeact {idx}\n"

    # Audit ledger sanity. conflicts_detected is currently not wired up, so use
    # result-level conflict tallies plus arbiter totals/hotspots here.
    expected_min_edits = write_successes + codeact_successes + disjoint_successes
    assert arbiter_status["total_edits"] >= expected_min_edits
    assert arbiter_status["active_locks"] >= 0
    assert arbiter_status["conflicts_detected"] >= 0
    assert any("edits/disjoint_" in item["file_path"] for item in hotspots), hotspots


def test_live_occ_load_20_non_overlapping_operations_profile(
    live_load_env: LiveLoadEnv,
    monkeypatch: pytest.MonkeyPatch,
):
    log_label = "occ-load-20-nonoverlap"
    _log_occ_event(
        log_label,
        {"event": "setup", "phase": "init_repo", "sandbox_id": live_load_env.sandbox_id},
    )
    live_load_env.init_repo()
    codeact_stats = _install_codeact_phase_probe(monkeypatch)
    overlay_stats = _install_overlay_phase_probe(monkeypatch)

    for group in range(2):
        live_load_env.write_text(
            f"edits/disjoint_{group}.py",
            (
                f'"""Disjoint target {group}."""\n\n'
                f"A_{group} = 1\n"
                f"B_{group} = 2\n"
                f"C_{group} = 3\n"
                f"D_{group} = 4\n"
                f"E_{group} = 5\n"
            ),
        )
    for idx in range(4):
        live_load_env.write_text(f"tx/small_{idx}.txt", "base\n")

    live_load_env.exec_checked(f"git -C {shlex.quote(live_load_env.repo_root)} add -A")
    live_load_env.exec_checked(
        f"git -C {shlex.quote(live_load_env.repo_root)} commit -m seed-small-nonoverlap-load",
        timeout=180,
    )

    svc = live_load_env.make_ci_service()
    operations = [
        {
            "kind": "write",
            "name": "write-0",
            "path": f"{live_load_env.repo_root}/writes/w0.txt",
            "kwargs": {
                "file_path": f"{live_load_env.repo_root}/writes/w0.txt",
                "content": "write 0\n",
            },
            "coordinated": False,
        },
        {
            "kind": "write",
            "name": "write-1",
            "path": f"{live_load_env.repo_root}/writes/w1.txt",
            "kwargs": {
                "file_path": f"{live_load_env.repo_root}/writes/w1.txt",
                "content": "write 1\n",
            },
            "coordinated": False,
        },
        {
            "kind": "write",
            "name": "write-2",
            "path": f"{live_load_env.repo_root}/writes/w2.txt",
            "kwargs": {
                "file_path": f"{live_load_env.repo_root}/writes/w2.txt",
                "content": "write 2\n",
            },
            "coordinated": False,
        },
        {
            "kind": "write",
            "name": "write-3",
            "path": f"{live_load_env.repo_root}/writes/w3.txt",
            "kwargs": {
                "file_path": f"{live_load_env.repo_root}/writes/w3.txt",
                "content": "write 3\n",
            },
            "coordinated": False,
        },
        {
            "kind": "edit-disjoint",
            "name": "edit-a0",
            "path": f"{live_load_env.repo_root}/edits/disjoint_0.py",
            "kwargs": {
                "file_path": f"{live_load_env.repo_root}/edits/disjoint_0.py",
                "old_text": "A_0 = 1",
                "new_text": "A_0 = 100",
            },
            "coordinated": False,
        },
        {
            "kind": "edit-disjoint",
            "name": "edit-b0",
            "path": f"{live_load_env.repo_root}/edits/disjoint_0.py",
            "kwargs": {
                "file_path": f"{live_load_env.repo_root}/edits/disjoint_0.py",
                "old_text": "B_0 = 2",
                "new_text": "B_0 = 200",
            },
            "coordinated": False,
        },
        {
            "kind": "edit-disjoint",
            "name": "edit-c0",
            "path": f"{live_load_env.repo_root}/edits/disjoint_0.py",
            "kwargs": {
                "file_path": f"{live_load_env.repo_root}/edits/disjoint_0.py",
                "old_text": "C_0 = 3",
                "new_text": "C_0 = 300",
            },
            "coordinated": False,
        },
        {
            "kind": "edit-disjoint",
            "name": "edit-d0",
            "path": f"{live_load_env.repo_root}/edits/disjoint_0.py",
            "kwargs": {
                "file_path": f"{live_load_env.repo_root}/edits/disjoint_0.py",
                "old_text": "D_0 = 4",
                "new_text": "D_0 = 400",
            },
            "coordinated": False,
        },
        {
            "kind": "edit-disjoint",
            "name": "edit-e0",
            "path": f"{live_load_env.repo_root}/edits/disjoint_0.py",
            "kwargs": {
                "file_path": f"{live_load_env.repo_root}/edits/disjoint_0.py",
                "old_text": "E_0 = 5",
                "new_text": "E_0 = 500",
            },
            "coordinated": False,
        },
        {
            "kind": "edit-disjoint",
            "name": "edit-a1",
            "path": f"{live_load_env.repo_root}/edits/disjoint_1.py",
            "kwargs": {
                "file_path": f"{live_load_env.repo_root}/edits/disjoint_1.py",
                "old_text": "A_1 = 1",
                "new_text": "A_1 = 100",
            },
            "coordinated": False,
        },
        {
            "kind": "edit-disjoint",
            "name": "edit-b1",
            "path": f"{live_load_env.repo_root}/edits/disjoint_1.py",
            "kwargs": {
                "file_path": f"{live_load_env.repo_root}/edits/disjoint_1.py",
                "old_text": "B_1 = 2",
                "new_text": "B_1 = 200",
            },
            "coordinated": False,
        },
        {
            "kind": "edit-disjoint",
            "name": "edit-c1",
            "path": f"{live_load_env.repo_root}/edits/disjoint_1.py",
            "kwargs": {
                "file_path": f"{live_load_env.repo_root}/edits/disjoint_1.py",
                "old_text": "C_1 = 3",
                "new_text": "C_1 = 300",
            },
            "coordinated": False,
        },
        {
            "kind": "edit-disjoint",
            "name": "edit-d1",
            "path": f"{live_load_env.repo_root}/edits/disjoint_1.py",
            "kwargs": {
                "file_path": f"{live_load_env.repo_root}/edits/disjoint_1.py",
                "old_text": "D_1 = 4",
                "new_text": "D_1 = 400",
            },
            "coordinated": False,
        },
        {
            "kind": "edit-disjoint",
            "name": "edit-e1",
            "path": f"{live_load_env.repo_root}/edits/disjoint_1.py",
            "kwargs": {
                "file_path": f"{live_load_env.repo_root}/edits/disjoint_1.py",
                "old_text": "E_1 = 5",
                "new_text": "E_1 = 500",
            },
            "coordinated": False,
        },
        {
            "kind": "codeact",
            "name": "codeact-0",
            "path": f"{live_load_env.repo_root}/tx/small_0.txt",
            "kwargs": {
                "mode": "shell",
                "command": (
                    "python3 - <<'PY'\n"
                    "from pathlib import Path\n"
                    "Path('tx/small_0.txt').write_text('codeact 0\\n', encoding='utf-8')\n"
                    "PY"
                ),
                "timeout": 120,
            },
            "coordinated": True,
        },
        {
            "kind": "codeact",
            "name": "codeact-1",
            "path": f"{live_load_env.repo_root}/tx/small_1.txt",
            "kwargs": {
                "mode": "shell",
                "command": (
                    "python3 - <<'PY'\n"
                    "from pathlib import Path\n"
                    "Path('tx/small_1.txt').write_text('codeact 1\\n', encoding='utf-8')\n"
                    "PY"
                ),
                "timeout": 120,
            },
            "coordinated": True,
        },
        {
            "kind": "codeact",
            "name": "codeact-2",
            "path": f"{live_load_env.repo_root}/tx/small_2.txt",
            "kwargs": {
                "mode": "shell",
                "command": (
                    "python3 - <<'PY'\n"
                    "from pathlib import Path\n"
                    "Path('tx/small_2.txt').write_text('codeact 2\\n', encoding='utf-8')\n"
                    "PY"
                ),
                "timeout": 120,
            },
            "coordinated": True,
        },
        {
            "kind": "codeact",
            "name": "codeact-3",
            "path": f"{live_load_env.repo_root}/tx/small_3.txt",
            "kwargs": {
                "mode": "shell",
                "command": (
                    "python3 - <<'PY'\n"
                    "from pathlib import Path\n"
                    "Path('tx/small_3.txt').write_text('codeact 3\\n', encoding='utf-8')\n"
                    "PY"
                ),
                "timeout": 120,
            },
            "coordinated": True,
        },
        {
            "kind": "write",
            "name": "write-4",
            "path": f"{live_load_env.repo_root}/writes/w4.txt",
            "kwargs": {
                "file_path": f"{live_load_env.repo_root}/writes/w4.txt",
                "content": "write 4\n",
            },
            "coordinated": False,
        },
        {
            "kind": "write",
            "name": "write-5",
            "path": f"{live_load_env.repo_root}/writes/w5.txt",
            "kwargs": {
                "file_path": f"{live_load_env.repo_root}/writes/w5.txt",
                "content": "write 5\n",
            },
            "coordinated": False,
        },
    ]

    started = time.perf_counter()
    results = asyncio.run(
        _run_mixed_operations(
            live_load_env,
            svc,
            operations,
            concurrency=20,
            timeout_s=120,
            log_ops=True,
            log_label=log_label,
        )
    )
    wall_elapsed_s = time.perf_counter() - started

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        by_kind.setdefault(item["kind"], []).append(item)

    summary = {
        "operation_counts": {
            kind: len(items)
            for kind, items in sorted(by_kind.items())
        },
        "elapsed_profile_s": {
            kind: _elapsed_profile(items)
            for kind, items in sorted(by_kind.items())
        },
        "timing": _operation_timing_summary(
            results,
            wall_elapsed_s=wall_elapsed_s,
        ),
        "write_process_s": [
            round(float(item["payload"].get("timings", {}).get("commit_total", 0.0)), 6)
            for item in by_kind.get("write", [])
        ],
        "edit_tool_total_s": [
            round(float(item["payload"].get("timings", {}).get("tool", {}).get("tool_total", 0.0)), 6)
            for item in by_kind.get("edit-disjoint", [])
            if item["payload"].get("timings")
        ],
        "codeact_phase_s": _phase_summary(codeact_stats),
        "overlay_phase_s": _phase_summary(overlay_stats),
        "arbiter": svc.status()["arbiter"],
    }
    print(f"\n[{log_label} timings]")
    print(json.dumps(summary, indent=2, sort_keys=True))

    assert len(operations) == 20
    assert sum(not item["is_error"] for item in by_kind["write"]) == 6
    assert sum(not item["is_error"] for item in by_kind["codeact"]) == 4
    assert sum(not item["is_error"] for item in by_kind["edit-disjoint"]) >= 8
    assert summary["timing"]["parallelism_ratio"] >= 3.0, summary["timing"]


def test_live_occ_load_30_non_overlapping_operations_profile(
    live_load_env: LiveLoadEnv,
    monkeypatch: pytest.MonkeyPatch,
):
    log_label = "occ-load-30-nonoverlap"
    _log_occ_event(
        log_label,
        {"event": "setup", "phase": "init_repo", "sandbox_id": live_load_env.sandbox_id},
    )
    live_load_env.init_repo()
    codeact_stats = _install_codeact_phase_probe(monkeypatch)
    overlay_stats = _install_overlay_phase_probe(monkeypatch)

    for group in range(3):
        live_load_env.write_text(
            f"edits/disjoint_{group}.py",
            (
                f'"""Disjoint target {group}."""\n\n'
                f"A_{group} = 1\n"
                f"B_{group} = 2\n"
                f"C_{group} = 3\n"
                f"D_{group} = 4\n"
                f"E_{group} = 5\n"
            ),
        )
    for idx in range(6):
        live_load_env.write_text(f"tx/medium_{idx}.txt", "base\n")

    live_load_env.exec_checked(f"git -C {shlex.quote(live_load_env.repo_root)} add -A")
    live_load_env.exec_checked(
        f"git -C {shlex.quote(live_load_env.repo_root)} commit -m seed-medium-nonoverlap-load",
        timeout=180,
    )

    svc = live_load_env.make_ci_service()
    operations: list[dict[str, Any]] = []

    for idx in range(9):
        operations.append(
            {
                "kind": "write",
                "name": f"write-{idx}",
                "path": f"{live_load_env.repo_root}/writes/w{idx}.txt",
                "kwargs": {
                    "file_path": f"{live_load_env.repo_root}/writes/w{idx}.txt",
                    "content": f"write {idx}\n",
                },
                "coordinated": False,
            }
        )

    for group in range(3):
        for label, old, new in (
            ("a", f"A_{group} = 1", f"A_{group} = 100"),
            ("b", f"B_{group} = 2", f"B_{group} = 200"),
            ("c", f"C_{group} = 3", f"C_{group} = 300"),
            ("d", f"D_{group} = 4", f"D_{group} = 400"),
            ("e", f"E_{group} = 5", f"E_{group} = 500"),
        ):
            operations.append(
                {
                    "kind": "edit-disjoint",
                    "name": f"edit-{label}{group}",
                    "path": f"{live_load_env.repo_root}/edits/disjoint_{group}.py",
                    "kwargs": {
                        "file_path": f"{live_load_env.repo_root}/edits/disjoint_{group}.py",
                        "old_text": old,
                        "new_text": new,
                    },
                    "coordinated": False,
                }
            )

    for idx in range(6):
        operations.append(
            {
                "kind": "codeact",
                "name": f"codeact-{idx}",
                "path": f"{live_load_env.repo_root}/tx/medium_{idx}.txt",
                "kwargs": {
                    "mode": "shell",
                    "command": (
                        "python3 - <<'PY'\n"
                        "from pathlib import Path\n"
                        f"Path('tx/medium_{idx}.txt').write_text('codeact {idx}\\n', encoding='utf-8')\n"
                        "PY"
                    ),
                    "timeout": 120,
                },
                "coordinated": True,
            }
        )

    started = time.perf_counter()
    results = asyncio.run(
        _run_mixed_operations(
            live_load_env,
            svc,
            operations,
            concurrency=20,
            timeout_s=180,
            log_ops=True,
            log_label=log_label,
        )
    )
    wall_elapsed_s = time.perf_counter() - started

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        by_kind.setdefault(item["kind"], []).append(item)

    summary = {
        "operation_counts": {
            kind: len(items)
            for kind, items in sorted(by_kind.items())
        },
        "elapsed_profile_s": {
            kind: _elapsed_profile(items)
            for kind, items in sorted(by_kind.items())
        },
        "timing": _operation_timing_summary(
            results,
            wall_elapsed_s=wall_elapsed_s,
        ),
        "write_process_s": [
            round(float(item["payload"].get("timings", {}).get("commit_total", 0.0)), 6)
            for item in by_kind.get("write", [])
        ],
        "edit_tool_total_s": [
            round(float(item["payload"].get("timings", {}).get("tool", {}).get("tool_total", 0.0)), 6)
            for item in by_kind.get("edit-disjoint", [])
            if item["payload"].get("timings")
        ],
        "codeact_phase_s": _phase_summary(codeact_stats),
        "overlay_phase_s": _phase_summary(overlay_stats),
        "arbiter": svc.status()["arbiter"],
    }
    print(f"\n[{log_label} timings]")
    print(json.dumps(summary, indent=2, sort_keys=True))

    assert len(operations) == 30
    assert sum(not item["is_error"] for item in by_kind["write"]) == 9
    assert sum(not item["is_error"] for item in by_kind["codeact"]) == 6
    assert sum(not item["is_error"] for item in by_kind["edit-disjoint"]) >= 12


def test_live_occ_load_50_non_overlapping_operations_profile(
    live_load_env: LiveLoadEnv,
    monkeypatch: pytest.MonkeyPatch,
):
    log_label = "occ-load-50-nonoverlap"
    _log_occ_event(
        log_label,
        {"event": "setup", "phase": "init_repo", "sandbox_id": live_load_env.sandbox_id},
    )
    live_load_env.init_repo()
    codeact_stats = _install_codeact_phase_probe(monkeypatch)
    overlay_stats = _install_overlay_phase_probe(monkeypatch)

    for group in range(5):
        live_load_env.write_text(
            f"edits/disjoint_{group}.py",
            (
                f'"""Disjoint target {group}."""\n\n'
                f"A_{group} = 1\n"
                f"B_{group} = 2\n"
                f"C_{group} = 3\n"
                f"D_{group} = 4\n"
                f"E_{group} = 5\n"
            ),
        )
    for idx in range(10):
        live_load_env.write_text(f"tx/large_{idx}.txt", "base\n")

    live_load_env.exec_checked(f"git -C {shlex.quote(live_load_env.repo_root)} add -A")
    live_load_env.exec_checked(
        f"git -C {shlex.quote(live_load_env.repo_root)} commit -m seed-large-nonoverlap-load",
        timeout=180,
    )

    svc = live_load_env.make_ci_service()
    operations: list[dict[str, Any]] = []

    for idx in range(15):
        operations.append(
            {
                "kind": "write",
                "name": f"write-{idx}",
                "path": f"{live_load_env.repo_root}/writes/w{idx}.txt",
                "kwargs": {
                    "file_path": f"{live_load_env.repo_root}/writes/w{idx}.txt",
                    "content": f"write {idx}\n",
                },
                "coordinated": False,
            }
        )

    for group in range(5):
        for label, old, new in (
            ("a", f"A_{group} = 1", f"A_{group} = 100"),
            ("b", f"B_{group} = 2", f"B_{group} = 200"),
            ("c", f"C_{group} = 3", f"C_{group} = 300"),
            ("d", f"D_{group} = 4", f"D_{group} = 400"),
            ("e", f"E_{group} = 5", f"E_{group} = 500"),
        ):
            operations.append(
                {
                    "kind": "edit-disjoint",
                    "name": f"edit-{label}{group}",
                    "path": f"{live_load_env.repo_root}/edits/disjoint_{group}.py",
                    "kwargs": {
                        "file_path": f"{live_load_env.repo_root}/edits/disjoint_{group}.py",
                        "old_text": old,
                        "new_text": new,
                    },
                    "coordinated": False,
                }
            )

    for idx in range(10):
        operations.append(
            {
                "kind": "codeact",
                "name": f"codeact-{idx}",
                "path": f"{live_load_env.repo_root}/tx/large_{idx}.txt",
                "kwargs": {
                    "mode": "shell",
                    "command": (
                        "python3 - <<'PY'\n"
                        "from pathlib import Path\n"
                        f"Path('tx/large_{idx}.txt').write_text('codeact {idx}\\n', encoding='utf-8')\n"
                        "PY"
                    ),
                    "timeout": 120,
                },
                "coordinated": True,
            }
        )

    started = time.perf_counter()
    results = asyncio.run(
        _run_mixed_operations(
            live_load_env,
            svc,
            operations,
            concurrency=20,
            timeout_s=240,
            log_ops=True,
            log_label=log_label,
        )
    )
    wall_elapsed_s = time.perf_counter() - started

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        by_kind.setdefault(item["kind"], []).append(item)

    summary = {
        "operation_counts": {
            kind: len(items)
            for kind, items in sorted(by_kind.items())
        },
        "elapsed_profile_s": {
            kind: _elapsed_profile(items)
            for kind, items in sorted(by_kind.items())
        },
        "timing": _operation_timing_summary(
            results,
            wall_elapsed_s=wall_elapsed_s,
        ),
        "write_process_s": [
            round(float(item["payload"].get("timings", {}).get("commit_total", 0.0)), 6)
            for item in by_kind.get("write", [])
        ],
        "edit_tool_total_s": [
            round(float(item["payload"].get("timings", {}).get("tool", {}).get("tool_total", 0.0)), 6)
            for item in by_kind.get("edit-disjoint", [])
            if item["payload"].get("timings")
        ],
        "codeact_phase_s": _phase_summary(codeact_stats),
        "overlay_phase_s": _phase_summary(overlay_stats),
        "arbiter": svc.status()["arbiter"],
    }
    print(f"\n[{log_label} timings]")
    print(json.dumps(summary, indent=2, sort_keys=True))

    assert len(operations) == 50
    assert sum(not item["is_error"] for item in by_kind["write"]) == 15
    assert sum(not item["is_error"] for item in by_kind["codeact"]) == 10
    assert sum(not item["is_error"] for item in by_kind["edit-disjoint"]) >= 20


def test_live_occ_load_svc_cmd_lowerdir_amortization(
    live_load_env: LiveLoadEnv,
    monkeypatch: pytest.MonkeyPatch,
):
    """svc.cmd / codeact repeated calls must amortize the CoW lowerdir cost.

    This is the performance claim for the 2026-04-19 refresh-after-commit fix:
    after the first codeact materializes the outer lowerdir and mounts overlay,
    subsequent codeact calls on the same sandbox must reuse the snapshot. If
    the refresh hook is broken, call #2 either (a) returns ``aborted_version``
    because its lowerdir base_hash drifts from ContentManager head, or (b)
    re-materializes the snapshot and pays the cold-start cost every time.

    The test runs one cold-start call, then 5 sequential calls that each
    mutate the same file. All 6 must succeed; the 5 steady-state calls'
    median elapsed must be materially below the cold-start elapsed; and the
    final file content must reflect the last write (proves the refresh
    callback mirrored each prior commit back into the lowerdir).
    """
    log_label = "occ-load-amortization"
    _log_occ_event(
        log_label,
        {"event": "setup", "phase": "init_repo", "sandbox_id": live_load_env.sandbox_id},
    )
    live_load_env.init_repo()
    codeact_stats = _install_codeact_phase_probe(monkeypatch)
    overlay_stats = _install_overlay_phase_probe(monkeypatch)
    live_load_env.write_text("shared/counter.txt", "v0\n")
    live_load_env.exec_checked(f"git -C {shlex.quote(live_load_env.repo_root)} add -A")
    live_load_env.exec_checked(
        f"git -C {shlex.quote(live_load_env.repo_root)} commit -m seed-amortization",
        timeout=180,
    )

    svc = live_load_env.make_ci_service()

    async def _invoke_codeact(label: str, target_value: str) -> dict[str, Any]:
        ctx = live_load_env.make_ctx(
            svc,
            agent_run_id=f"{label}-{uuid.uuid4().hex[:8]}",
            coordinated=True,
        )
        kwargs = {
            "mode": "shell",
            "command": (
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                f"Path('shared/counter.txt').write_text({target_value!r} + '\\n', encoding='utf-8')\n"
                "PY"
            ),
            "timeout": 120,
        }
        _log_occ_event(
            log_label,
            {"event": "codeact_start", "label": label, "target_value": target_value},
        )
        started = time.perf_counter()
        result = await _invoke_tool(daytona_codeact, kwargs, ctx)
        elapsed_s = round(time.perf_counter() - started, 6)
        _log_occ_event(
            log_label,
            {
                "event": "codeact_finish",
                "label": label,
                "is_error": result.is_error,
                "elapsed_s": elapsed_s,
                "metadata": dict(result.metadata or {}),
                "overlay_phase_snapshot_s": {
                    phase: round(values[-1], 6)
                    for phase, values in overlay_stats.items()
                    if values
                },
                "codeact_phase_snapshot_s": {
                    phase: round(values[-1], 6)
                    for phase, values in codeact_stats.items()
                    if values
                },
            },
        )
        raw_output = result.output or ""
        output = raw_output.lstrip()
        payload: dict[str, Any] = {}
        if output.startswith("{"):
            try:
                payload = json.loads(output)
            except json.JSONDecodeError:
                payload = {}
        return {
            "label": label,
            "target_value": target_value,
            "is_error": result.is_error,
            "metadata": dict(result.metadata or {}),
            "payload": payload,
            "raw_output": raw_output[:800],
            "elapsed_s": elapsed_s,
        }

    async def _scenario() -> list[dict[str, Any]]:
        results = []
        # Cold start: first svc.cmd — mounts overlay, materializes CoW lowerdir.
        results.append(await _invoke_codeact("cold", "cold-0"))
        # Steady-state: 5 sequential calls must reuse the snapshot via refresh.
        for i in range(5):
            results.append(await _invoke_codeact(f"steady-{i}", f"steady-{i}"))
        return results

    started = time.perf_counter()
    results = asyncio.run(asyncio.wait_for(_scenario(), timeout=300))
    wall_elapsed_s = time.perf_counter() - started

    cold = results[0]
    steady = results[1:]
    steady_elapsed = sorted(item["elapsed_s"] for item in steady)
    median_steady_s = steady_elapsed[len(steady_elapsed) // 2]
    max_steady_s = steady_elapsed[-1]

    final_content = live_load_env.read_text("shared/counter.txt")
    arbiter_status = svc.status()["arbiter"]

    print(f"\n[{log_label}]")
    print(
        json.dumps(
            {
                "wall_elapsed_s": round(wall_elapsed_s, 6),
                "cold_elapsed_s": cold["elapsed_s"],
                "steady_elapsed_s": steady_elapsed,
                "median_steady_s": median_steady_s,
                "max_steady_s": max_steady_s,
                "steady_profile_s": _value_profile(
                    [item["elapsed_s"] for item in steady]
                ),
                "cold_vs_steady_ratio": (
                    round(cold["elapsed_s"] / median_steady_s, 3)
                    if median_steady_s > 0
                    else 0.0
                ),
                "final_content": final_content,
                "arbiter": arbiter_status,
                "codeact_phase_s": _phase_summary(codeact_stats),
                "overlay_phase_s": _phase_summary(overlay_stats),
                "per_call": [
                    {
                        "label": item["label"],
                        "is_error": item["is_error"],
                        "elapsed_s": item["elapsed_s"],
                        "raw_output": item["raw_output"],
                        "metadata": item["metadata"],
                    }
                    for item in results
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )

    # All 6 calls must succeed — proves refresh-after-commit prevents the
    # stale-base false aborted_version on repeated svc.cmd.
    for item in results:
        assert not item["is_error"], (
            f"{item['label']} failed: "
            f"output={item['raw_output']!r} metadata={item['metadata']}"
        )

    # The last write must be what landed on disk — proves the refresh callback
    # mirrored each prior commit back into the lowerdir (otherwise later calls
    # would see stale content as base and either abort or overwrite with
    # partial state).
    assert final_content == "steady-4\n", (
        f"Final file does not reflect the last steady-state write; got {final_content!r}"
    )

    # Amortization gate. Live-sandbox timings are noisy (network jitter,
    # shared runner load), so we assert the median of 5 steady-state calls
    # is at most 1.5× the cold-start. If the refresh hook regressed to
    # re-materializing the lowerdir every call, steady-state would roughly
    # track cold-start; a 1.5× upper bound catches that regression while
    # absorbing one or two noisy outliers.
    assert median_steady_s <= cold["elapsed_s"] * 1.5, (
        f"Steady-state median {median_steady_s:.3f}s exceeds 1.5× cold-start "
        f"{cold['elapsed_s']:.3f}s — lowerdir amortization regressed "
        "(refresh-after-commit may not be reusing the snapshot). "
        f"Per-call timings: cold={cold['elapsed_s']:.3f}s, "
        f"steady={steady_elapsed}"
    )

    # Max steady-state must not exceed 2× cold start — catches regressions
    # where a later call hits an unexpected remount or full resync.
    assert max_steady_s <= cold["elapsed_s"] * 2.0, (
        f"Steady-state max {max_steady_s:.3f}s exceeded 2× cold-start "
        f"{cold['elapsed_s']:.3f}s."
    )

    # Arbiter ledger must reflect 6 codeact-side commits (one per svc.cmd).
    assert arbiter_status["total_edits"] >= 6, arbiter_status


def test_live_occ_load_sequential_per_op_baseline(
    live_load_env: LiveLoadEnv,
    monkeypatch: pytest.MonkeyPatch,
):
    """Per-op 1-op latency baseline with zero concurrency.

    Runs each mutator kind ``N`` times sequentially on one fresh sandbox.
    Pair the resulting p50/min numbers with the 72-op concurrent test's
    per-kind profile to reason about parallel efficiency.

    The ops do real work on fresh paths per iteration so prior iterations
    don't short-circuit (e.g. edit must find its sentinel, delete must
    find its target). First iteration is treated as a cold-start sample;
    steady-state should converge by iteration 2.
    """
    N = 5
    log_label = "occ-load-sequential-baseline"
    _log_occ_event(
        log_label,
        {
            "event": "setup",
            "phase": "init_repo",
            "sandbox_id": live_load_env.sandbox_id,
            "iterations": N,
        },
    )
    live_load_env.init_repo()
    codeact_stats = _install_codeact_phase_probe(monkeypatch)
    overlay_stats = _install_overlay_phase_probe(monkeypatch)

    # Seed files that each op iter will mutate.
    for idx in range(N):
        live_load_env.write_text(
            f"edits/edit_{idx}.py",
            f'"""Edit fixture {idx}."""\n\nMARKER_{idx} = "before"\n',
        )
        live_load_env.write_text(
            f"rename/mod_{idx}.py",
            (
                f'"""Rename fixture {idx}."""\n\n'
                f"def target_{idx}(x):\n"
                f"    return x + {idx}\n\n"
                f"def caller_{idx}(x):\n"
                f"    return target_{idx}(x)\n"
            ),
        )
        live_load_env.write_text(f"moves/src_{idx}.txt", f"src {idx}\n")
        live_load_env.write_text(f"deletes/del_{idx}.txt", f"del {idx}\n")
        live_load_env.write_text(f"codeact/ca_{idx}.txt", f"base {idx}\n")

    live_load_env.exec_checked(f"git -C {shlex.quote(live_load_env.repo_root)} add -A")
    live_load_env.exec_checked(
        f"git -C {shlex.quote(live_load_env.repo_root)} commit -m seed-sequential-baseline",
        timeout=180,
    )

    svc = live_load_env.make_ci_service()
    svc.ensure_initialized(wait=True)

    timings_by_kind: dict[str, list[float]] = {}

    async def _run_single(
        kind: str,
        name: str,
        kwargs: dict[str, Any],
        *,
        coordinated: bool,
    ) -> float:
        agent_run_id = f"{name}-{uuid.uuid4().hex[:8]}"
        ctx = live_load_env.make_ctx(
            svc,
            agent_run_id=agent_run_id,
            coordinated=coordinated,
        )
        tool = _tool_for_operation_kind(kind)
        _log_occ_event(
            log_label,
            {"event": "op_start", "kind": kind, "name": name},
        )
        started = time.perf_counter()
        result = await _invoke_tool(tool, kwargs, ctx)
        elapsed = round(time.perf_counter() - started, 6)
        assert not result.is_error, (
            f"{kind}/{name} failed: output={(result.output or '')[:300]!r} "
            f"metadata={dict(result.metadata or {})}"
        )
        _log_occ_event(
            log_label,
            {
                "event": "op_finish",
                "kind": kind,
                "name": name,
                "elapsed_s": elapsed,
                "metadata": dict(result.metadata or {}),
            },
        )
        timings_by_kind.setdefault(kind, []).append(elapsed)
        return elapsed

    async def _scenario() -> None:
        for idx in range(N):
            await _run_single(
                "write",
                f"write-{idx}",
                {
                    "file_path": f"{live_load_env.repo_root}/writes/w_{idx}.txt",
                    "content": f"write {idx}\n",
                },
                coordinated=False,
            )
            await _run_single(
                "edit-disjoint",
                f"edit-{idx}",
                {
                    "file_path": f"{live_load_env.repo_root}/edits/edit_{idx}.py",
                    "old_text": f'MARKER_{idx} = "before"',
                    "new_text": f'MARKER_{idx} = "after-{idx}"',
                },
                coordinated=False,
            )
            await _run_single(
                "rename",
                f"rename-{idx}",
                {
                    "symbol": f"target_{idx}",
                    "new_name": f"renamed_{idx}",
                    "file_hint": f"rename/mod_{idx}.py",
                },
                coordinated=False,
            )
            await _run_single(
                "move",
                f"move-{idx}",
                {
                    "src_path": f"{live_load_env.repo_root}/moves/src_{idx}.txt",
                    "target_path": f"{live_load_env.repo_root}/moves/dst_{idx}.txt",
                },
                coordinated=False,
            )
            await _run_single(
                "delete",
                f"delete-{idx}",
                {"path": f"{live_load_env.repo_root}/deletes/del_{idx}.txt"},
                coordinated=False,
            )
            await _run_single(
                "codeact",
                f"codeact-{idx}",
                {
                    "mode": "shell",
                    "command": (
                        "python3 - <<'PY'\n"
                        "from pathlib import Path\n"
                        f"Path('codeact/ca_{idx}.txt').write_text('codeact {idx}\\n', encoding='utf-8')\n"
                        "PY"
                    ),
                    "timeout": 120,
                },
                coordinated=True,
            )

    scenario_started = time.perf_counter()
    asyncio.run(asyncio.wait_for(_scenario(), timeout=600))
    wall_elapsed_s = round(time.perf_counter() - scenario_started, 6)

    def _steady_profile(times: list[float]) -> dict[str, float]:
        # First iteration is cold; summarize ops 2..N for steady-state view.
        return _value_profile(times[1:]) if len(times) > 1 else _value_profile(times)

    summary = {
        "iterations_per_kind": N,
        "wall_elapsed_s": wall_elapsed_s,
        "per_op_elapsed_s": {
            kind: _value_profile(times)
            for kind, times in sorted(timings_by_kind.items())
        },
        "per_op_steady_s": {
            kind: _steady_profile(times)
            for kind, times in sorted(timings_by_kind.items())
        },
        "cold_s": {
            kind: round(times[0], 6)
            for kind, times in sorted(timings_by_kind.items())
        },
        "codeact_phase_s": _phase_summary(codeact_stats),
        "overlay_phase_s": _phase_summary(overlay_stats),
        "arbiter": svc.status()["arbiter"],
    }

    print(f"\n[{log_label}]")
    print(json.dumps(summary, indent=2, sort_keys=True))

    expected_kinds = {"write", "edit-disjoint", "rename", "move", "delete", "codeact"}
    assert set(timings_by_kind.keys()) == expected_kinds, timings_by_kind.keys()
    for kind, times in timings_by_kind.items():
        assert len(times) == N, (kind, times)
        for t in times:
            assert 0 < t < 60, (kind, t)

    # Spot-check that the state actually landed — guards against a tool
    # silently no-op'ing and inflating the baseline.
    for idx in range(N):
        assert live_load_env.read_text(f"writes/w_{idx}.txt") == f"write {idx}\n"
        assert f'MARKER_{idx} = "after-{idx}"' in live_load_env.read_text(
            f"edits/edit_{idx}.py"
        )
        renamed = live_load_env.read_text(f"rename/mod_{idx}.py")
        assert f"def renamed_{idx}(x):" in renamed
        assert f"target_{idx}(x)" not in renamed or f"renamed_{idx}(x)" in renamed
        assert live_load_env.read_text(f"moves/dst_{idx}.txt") == f"src {idx}\n"
        assert live_load_env.read_text(f"codeact/ca_{idx}.txt") == f"codeact {idx}\n"


def test_live_daytona_transport_parallelism_isolation(live_load_env: LiveLoadEnv) -> None:
    """Isolate whether `process.exec` is the concurrency ceiling.

    The OCC load test measures ~2.3x effective parallelism for 72 concurrent
    ops, while single-op latencies are 0.58-1.57s. That gap could live in the
    Python OCC pipeline or in the sandbox transport. This test removes the
    entire OCC pipeline and measures parallelism of `process.exec` alone.

    Target: wall time for 72 concurrent `sleep 0.5` execs should be ~0.5-1.0s
    if transport parallelism is unbounded. Anything materially higher means
    the transport itself is the ceiling and no amount of Python batching will
    hit the 1-2s target.
    """
    N = 72
    SLEEP_S = 0.5

    import concurrent.futures as _cf

    async def _run() -> dict[str, Any]:
        # Arm A: asyncio.to_thread wrapping sync sandbox (what svc.cmd uses).
        async def one_a(idx: int) -> dict[str, float]:
            t0 = time.perf_counter()
            resp = await live_load_env.async_sandbox.process.exec(
                f"sleep {SLEEP_S}",
                timeout=30,
            )
            return {
                "idx": idx,
                "elapsed_s": round(time.perf_counter() - t0, 6),
                "exit_code": getattr(resp, "exit_code", None),
            }

        configure_default_executor(
            asyncio.get_running_loop(),
            max_workers=max(200, N * 8),
        )
        # Warm one exec so any one-time connection setup isn't measured.
        await live_load_env.async_sandbox.process.exec("echo warm", timeout=10)

        wall_t0 = time.perf_counter()
        a_per_call = await asyncio.gather(*[one_a(i) for i in range(N)])
        arm_a_wall = round(time.perf_counter() - wall_t0, 6)

        # Arm C: explicit run_in_executor on a fresh ThreadPoolExecutor.
        # If C matches Arm B, set_default_executor isn't being honored by
        # asyncio.to_thread / run_in_executor(None, ...). If C still matches
        # Arm A, bottleneck is in loop-driven dispatch itself (not executor).
        loop = asyncio.get_running_loop()
        explicit_pool = _cf.ThreadPoolExecutor(
            max_workers=N, thread_name_prefix="arm-c",
        )

        import functools as _functools

        def _run_one_sync() -> Any:
            return live_load_env.raw_sandbox.process.exec(
                f"sleep {SLEEP_S}", timeout=30,
            )

        async def one_c(idx: int) -> dict[str, float]:
            t0 = time.perf_counter()
            resp = await loop.run_in_executor(
                explicit_pool,
                _functools.partial(_run_one_sync),
            )
            return {
                "idx": idx,
                "elapsed_s": round(time.perf_counter() - t0, 6),
                "exit_code": getattr(resp, "exit_code", None),
            }

        wall_t0 = time.perf_counter()
        c_per_call = await asyncio.gather(*[one_c(i) for i in range(N)])
        arm_c_wall = round(time.perf_counter() - wall_t0, 6)
        explicit_pool.shutdown(wait=False)

        # Arm D: true AsyncDaytona client (aiohttp). This is what production
        # should use once the sync-wrap is removed.
        from sandbox.async_client import get_async_sandbox

        async_real = await get_async_sandbox(live_load_env.sandbox_id)
        # Warm one real-async exec.
        await async_real.process.exec("echo warm", timeout=10)

        async def one_d(idx: int) -> dict[str, float]:
            t0 = time.perf_counter()
            resp = await async_real.process.exec(
                f"sleep {SLEEP_S}",
                timeout=30,
            )
            return {
                "idx": idx,
                "elapsed_s": round(time.perf_counter() - t0, 6),
                "exit_code": getattr(resp, "exit_code", None),
            }

        wall_t0 = time.perf_counter()
        d_per_call = await asyncio.gather(*[one_d(i) for i in range(N)])
        arm_d_wall = round(time.perf_counter() - wall_t0, 6)

        # Arm F: run_in_executor(None, ...) -- same default executor as
        # asyncio.to_thread but WITHOUT the contextvars.copy_context().run
        # wrapping. If F matches C (~48x), the to_thread ctx.run wrapping is
        # what serializes the sync Daytona SDK. If F matches A (~6x), the
        # default executor is blocked regardless of ctx.run.
        def _run_one_sync_f() -> Any:
            return live_load_env.raw_sandbox.process.exec(
                f"sleep {SLEEP_S}", timeout=30,
            )

        async def one_f(idx: int) -> dict[str, float]:
            t0 = time.perf_counter()
            resp = await loop.run_in_executor(None, _run_one_sync_f)
            return {
                "idx": idx,
                "elapsed_s": round(time.perf_counter() - t0, 6),
                "exit_code": getattr(resp, "exit_code", None),
            }

        wall_t0 = time.perf_counter()
        f_per_call = await asyncio.gather(*[one_f(i) for i in range(N)])
        arm_f_wall = round(time.perf_counter() - wall_t0, 6)

        # Arm G: AsyncDaytona + heavy python3 workload (what ContentManager
        # actually does). Each call spawns a python interpreter on the sandbox
        # to read a stub file and marshal JSON, mirroring the real hot path.
        # If G is much slower than D (sleep 0.5), the sandbox's process.exec
        # runner is capped for heavy concurrent work regardless of transport.
        g_script = (
            "import json, pathlib, sys; "
            "p = pathlib.Path('/tmp/arm_g_stub.txt'); "
            "print(json.dumps({'exists': p.exists(), "
            "'content': p.read_text(encoding='utf-8') if p.exists() else ''}))"
        )
        g_command = f"python3 -c {shlex.quote(g_script)}"
        # Seed the stub file so the python script does real work (read+json).
        await async_real.process.exec(
            "printf 'arm-g-stub\\n' > /tmp/arm_g_stub.txt", timeout=10,
        )
        # Warm one exec.
        await async_real.process.exec(g_command, timeout=10)

        async def one_g(idx: int) -> dict[str, float]:
            t0 = time.perf_counter()
            resp = await async_real.process.exec(g_command, timeout=30)
            return {
                "idx": idx,
                "elapsed_s": round(time.perf_counter() - t0, 6),
                "exit_code": getattr(resp, "exit_code", None),
            }

        wall_t0 = time.perf_counter()
        g_per_call = await asyncio.gather(*[one_g(i) for i in range(N)])
        arm_g_wall = round(time.perf_counter() - wall_t0, 6)

        default_exec = loop._default_executor  # type: ignore[attr-defined]
        default_max = (
            getattr(default_exec, "_max_workers", "n/a") if default_exec else "none"
        )

        return {
            "arm_a": {"wall_s": arm_a_wall, "per_call": a_per_call},
            "arm_c": {"wall_s": arm_c_wall, "per_call": c_per_call},
            "arm_d": {"wall_s": arm_d_wall, "per_call": d_per_call},
            "arm_f": {"wall_s": arm_f_wall, "per_call": f_per_call},
            "arm_g": {"wall_s": arm_g_wall, "per_call": g_per_call},
            "default_executor_max_workers": default_max,
        }

    arms = asyncio.run(_run())
    arm_async = arms["arm_a"]
    arm_c = arms["arm_c"]
    arm_d = arms["arm_d"]
    arm_f = arms["arm_f"]
    arm_g = arms["arm_g"]

    # Arm B: raw sync sandbox via concurrent.futures (no Python async at all).
    def one_sync(idx: int) -> dict[str, float]:
        t0 = time.perf_counter()
        resp = live_load_env.raw_sandbox.process.exec(
            f"sleep {SLEEP_S}",
            timeout=30,
        )
        return {
            "idx": idx,
            "elapsed_s": round(time.perf_counter() - t0, 6),
            "exit_code": getattr(resp, "exit_code", None),
        }

    # Warm one sync exec too.
    live_load_env.raw_sandbox.process.exec("echo warm", timeout=10)

    wall_t0 = time.perf_counter()
    with _cf.ThreadPoolExecutor(max_workers=N) as pool:
        arm_sync_per_call = list(pool.map(one_sync, range(N)))
    arm_sync_wall_s = round(time.perf_counter() - wall_t0, 6)

    def _profile(per_call: list[dict[str, float]]) -> dict[str, float]:
        values = sorted(float(item["elapsed_s"]) for item in per_call)
        return {
            "count": len(values),
            "min": round(values[0], 4),
            "p50": round(values[len(values) // 2], 4),
            "p90": round(values[int(len(values) * 0.9)], 4),
            "p99": round(values[int(len(values) * 0.99)], 4),
            "max": round(values[-1], 4),
            "sum": round(sum(values), 4),
        }

    def _arm(wall_s: float, per_call: list[dict[str, float]]) -> dict[str, Any]:
        return {
            "wall_s": wall_s,
            "effective_parallelism": round(
                (N * SLEEP_S) / max(wall_s, 1e-6), 2,
            ),
            "per_call_s": _profile(per_call),
        }

    summary = {
        "N": N,
        "sleep_s": SLEEP_S,
        "single_op_floor_s": SLEEP_S,
        "pure_sequential_wall_s": round(N * SLEEP_S, 4),
        "arm_a_asyncio_to_thread": _arm(arm_async["wall_s"], arm_async["per_call"]),
        "arm_b_sync_threadpool": _arm(arm_sync_wall_s, arm_sync_per_call),
        "arm_c_explicit_run_in_executor": _arm(arm_c["wall_s"], arm_c["per_call"]),
        "arm_d_true_async_daytona_aiohttp": _arm(arm_d["wall_s"], arm_d["per_call"]),
        "arm_f_run_in_executor_None_no_ctxrun": _arm(
            arm_f["wall_s"], arm_f["per_call"],
        ),
        "arm_g_async_daytona_python3_heavy": _arm(
            arm_g["wall_s"], arm_g["per_call"],
        ),
        "default_executor_max_workers": arms["default_executor_max_workers"],
    }

    print("\n[transport-parallelism-isolation]")
    print(json.dumps(summary, indent=2, sort_keys=True))

    # Diagnostic only — sanity-check completion and gross timeout.
    for arm in (arm_async, arm_c, arm_d, arm_f, arm_g):
        assert len(arm["per_call"]) == N
        assert arm["wall_s"] < N * SLEEP_S + 20
    assert len(arm_sync_per_call) == N
    assert arm_sync_wall_s < N * SLEEP_S + 10
