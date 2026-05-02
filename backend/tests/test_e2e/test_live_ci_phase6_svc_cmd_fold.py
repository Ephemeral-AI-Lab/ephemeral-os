"""Phase 6 — daemon-local svc.cmd overlay fold live performance E2E.

Runs 1x/5x/10x full audited ``svc.cmd`` overlay batches against the real
Daytona ``dask__dask_2023.3.2_2023.4.0`` fixture. The test prints per-op,
per-stage, daemon-log, resource, and baseline-comparison timings so latency
bottlenecks are diagnosable from a single ``-s`` run.

Run with:
    .venv/bin/pytest backend/tests/test_e2e/test_live_ci_phase6_svc_cmd_fold.py -m live -v -s
"""

from __future__ import annotations

import base64
import json
import os
import shlex
from pathlib import Path

import pytest

from ._timing_harness import TimingHarness
from .test_live_ci_phase3_5_concurrent_perf import (
    _DaemonLogTailer,
    _SVC_CMD_CONCURRENCY_LEVELS,
    _SyncSandboxTransport,
    _flush,
    _orchestrator_pid,
    _run_svc_cmd_concurrency_batch,
    _trace,
    live_phase35_env,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live]
_ = live_phase35_env

_BASELINE = (
    Path(__file__).resolve().parent
    / "_timings"
    / "phase_3.5_svc_cmd_overlay_concurrency_1_5_10_2026-05-02T19-12-20Z.json"
)
_PHASE6_10X_P50_CEILING_S = 2.0


@pytest.mark.asyncio
async def test_phase6_svc_cmd_fold_live_perf(live_phase35_env) -> None:
    """Measure Phase 6 daemon-local ``svc.cmd`` latency and structure."""
    h = TimingHarness(phase=6, test_name="svc_cmd_fold_concurrency_1_5_10")
    env = live_phase35_env
    svc = env.make_ci_service()
    failures: list[str] = []
    old_log_level = os.environ.get("EOS_CI_DAEMON_LOG_LEVEL")
    os.environ["EOS_CI_DAEMON_LOG_LEVEL"] = "DEBUG"

    try:
        with _trace(h, "ci_service_construct"):
            svc.ensure_initialized(wait=True)

        pid = env.daemon_pid()
        sampler_pid = pid if pid is not None else _orchestrator_pid()
        sampler_transport = _SyncSandboxTransport(env.raw_sandbox)
        h.sample_rss_mb(
            "rss_at_start",
            sampler_transport,
            env.sandbox_id,
            sampler_pid,
        )
        h.sample_fds(
            "fds_at_start",
            sampler_transport,
            env.sandbox_id,
            sampler_pid,
        )

        from sandbox.client.async_ import get_async_sandbox

        async_sandbox = await get_async_sandbox(env.sandbox_id)
        load_root = f"{env.root_dir}/_phase6_svc_cmd_fold"
        _flush(f"  [phase6] preparing live load root: {load_root}")
        code, output = env.exec(f"mkdir -p {shlex.quote(load_root)}", timeout=60)
        assert code == 0, output

        daemon_log_path = f"{env.daemon_state_dir()}/daemon.log"
        capture = _DaemonLogCapture(env, daemon_log_path)
        capture.seek_end()

        for level in _SVC_CMD_CONCURRENCY_LEVELS:
            daemon_log = _DaemonLogTailer(env, daemon_log_path)
            daemon_log.seek_end()
            with _trace(h, f"svc_cmd_{level}x_wall"):
                results = await _run_svc_cmd_concurrency_batch(
                    h,
                    svc,
                    async_sandbox,
                    load_root=load_root,
                    concurrency=level,
                    daemon_log=daemon_log,
                )
            errors = [r for r in results if r.error is not None]
            bad_statuses = [
                r
                for r in results
                if r.error is None
                and (
                    r.exit_code != 0
                    or r.git_commit_status != "committed"
                    or r.changed_paths < 1
                )
            ]
            if errors:
                failures.append(
                    f"{level}x errors: {[r.error for r in errors[:3]]}"
                )
            if bad_statuses:
                failures.append(
                    f"{level}x unexpected statuses: "
                    f"{[(r.op_index, r.exit_code, r.git_commit_status, r.changed_paths) for r in bad_statuses[:5]]}"
                )

        h.sample_rss_mb(
            "rss_at_end",
            sampler_transport,
            env.sandbox_id,
            sampler_pid,
        )
        h.sample_fds(
            "fds_at_end",
            sampler_transport,
            env.sandbox_id,
            sampler_pid,
        )
        h.values["rss_growth_mb"] = h.values["rss_at_end"] - h.values["rss_at_start"]
        h.values["fd_growth"] = h.values["fds_at_end"] - h.values["fds_at_start"]

        phase6_log = capture.read_new()
        unshare_count = phase6_log.count(
            "overlay daemon-local subprocess.run start: kind=unshare"
        )
        git_snapshot_stage_count = phase6_log.count(
            "overlay command stage start: stage=git_snapshot"
        )
        expected_unshare_count = sum(_SVC_CMD_CONCURRENCY_LEVELS)
        h.values["daemon_local_unshare_subprocess_count"] = float(unshare_count)
        h.values["daemon_local_git_snapshot_stage_count"] = float(
            git_snapshot_stage_count
        )
        _flush(
            "  [phase6] structural subprocess check "
            f"unshare_count={unshare_count} expected={expected_unshare_count} "
            f"git_snapshot_stage_count={git_snapshot_stage_count}"
        )

        if unshare_count != expected_unshare_count:
            failures.append(
                f"expected exactly {expected_unshare_count} daemon-local unshare "
                f"subprocess calls, saw {unshare_count}"
            )
        if git_snapshot_stage_count != 0:
            failures.append(
                f"daemon-local path still logged {git_snapshot_stage_count} "
                "separate git_snapshot stages"
            )

        ten_x = h.distributions.get("svc_cmd_10x_latency", {})
        ten_x_p50 = float(ten_x.get("p50", 999.0))
        _flush(
            f"  [phase6] headline svc_cmd_10x_latency.p50={ten_x_p50:.3f}s "
            f"ceiling={_PHASE6_10X_P50_CEILING_S:.3f}s"
        )
        if ten_x_p50 >= _PHASE6_10X_P50_CEILING_S:
            failures.append(
                f"svc_cmd_10x_latency.p50 {ten_x_p50:.3f}s exceeded "
                f"{_PHASE6_10X_P50_CEILING_S:.3f}s"
            )

        _flush("\n" + h.report())
        if _BASELINE.exists():
            _flush("\n" + h.compare_to(_BASELINE))
        if failures:
            pytest.fail("; ".join(failures))
    finally:
        if old_log_level is None:
            os.environ.pop("EOS_CI_DAEMON_LOG_LEVEL", None)
        else:
            os.environ["EOS_CI_DAEMON_LOG_LEVEL"] = old_log_level
        path = h.dump_json()
        _flush(f"  [phase6] timing json: {path}")
        svc.dispose()


class _DaemonLogCapture:
    def __init__(self, env, path: str) -> None:
        self._env = env
        self._path = path
        self._offset = 0

    def seek_end(self) -> None:
        self._offset = self._remote_size()
        _flush(
            f"  [phase6] structural daemon log capture starts at byte "
            f"{self._offset} path={self._path}"
        )

    def read_new(self) -> str:
        script = (
            "import base64,json,pathlib,sys; "
            "path=pathlib.Path(sys.argv[1]); "
            "offset=max(0,int(sys.argv[2])); "
            "data=path.read_bytes() if path.exists() else b''; "
            "chunk=data[offset:]; "
            "print(json.dumps({'size': len(data), "
            "'chunk': base64.b64encode(chunk).decode('ascii')}))"
        )
        cmd = (
            f"python3 -c {shlex.quote(script)} "
            f"{shlex.quote(self._path)} {self._offset}"
        )
        code, raw = self._env.exec(cmd, timeout=60)
        if code != 0:
            _flush(f"  [phase6] daemon log capture failed exit={code}: {raw[-300:]}")
            return ""
        payload = json.loads(raw or "{}")
        self._offset = int(payload.get("size") or self._offset)
        chunk_b64 = str(payload.get("chunk") or "")
        if not chunk_b64:
            return ""
        text = base64.b64decode(chunk_b64).decode("utf-8", "replace")
        _flush(f"  [phase6] captured {len(text)} daemon-log chars for structure")
        return text

    def _remote_size(self) -> int:
        code, raw = self._env.exec(f"wc -c < {shlex.quote(self._path)}", timeout=30)
        if code != 0:
            return 0
        try:
            return int(raw.strip().split()[0])
        except (IndexError, ValueError):
            return 0
