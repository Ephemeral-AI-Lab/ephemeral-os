"""Tier-0 health probe for the progressive live-test runner.

Implements §6 of progressive-live-test-tiers-design-20260508.md as a
Python entry point. Re-orders the same logic that lives in
``daytona_probe.sh`` so the runner can call it without forking a shell:

1. Probe ``GET {api_url}/health``.
2. If ``docker`` is on PATH, query the Daytona runner container health.
   A healthy API with an unhealthy runner is not usable for live tests.
3. If the runner is wedged by a stale Docker-in-Docker ``containerd.pid``,
   surface ``tier0_runner_recovery_required``.
4. If ``docker`` is on PATH, query the daytona-db-1 Postgres for
   sandbox rows stuck in ``state='starting'`` or ``state='pending_build'``
   for >60s.
5. If stuck rows are found, return ``passed=False`` with notes that
   include ``tier0_manual_recovery_required`` so the runner knows to
   abort everything (per plan §3 cascade rules).

Recovery is intentionally NOT executed from this module — recovery is
a destructive operation that the operator must explicitly run via
``daytona_probe.sh``. The probe surfaces the symptom; the script applies
the workaround.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Literal


ApiHealth = Literal["ok", "timeout", "non_200", "error"]


@dataclass
class Tier0Result:
    """Structured outcome of one tier-0 probe."""

    passed: bool
    api_health: ApiHealth
    stuck_rows: list[str] = field(default_factory=list)
    docker_available: bool = False
    runner_healthy: bool | None = None
    stale_containerd_pid: str | None = None
    recovery_attempted: bool = False
    recovery_succeeded: bool | None = None
    elapsed_s: float = 0.0
    notes: str = ""


@dataclass(frozen=True)
class RunnerBootstrapIssue:
    """Daytona runner health evidence gathered from Docker."""

    docker_available: bool
    runner_healthy: bool | None
    stale_containerd_pid: str | None = None
    notes: str = ""


def _check_api_health(url: str, timeout_s: float) -> tuple[ApiHealth, str]:
    """Probe ``url`` once and classify the outcome."""
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            status = resp.status
            if status == 200:
                return "ok", f"http_code={status}"
            return "non_200", f"http_code={status}"
    except urllib.error.HTTPError as exc:
        return "non_200", f"http_code={exc.code}"
    except (TimeoutError, urllib.error.URLError) as exc:
        # urllib raises URLError for socket timeouts on some platforms;
        # surface that as a separate state from a clean timeout.
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError):
            return "timeout", "socket_timeout"
        msg = str(reason)
        if "timed out" in msg.lower():
            return "timeout", msg
        return "error", msg
    except Exception as exc:  # noqa: BLE001 — probe must not raise
        return "error", f"{type(exc).__name__}: {exc}"


def _detect_stuck_rows(timeout_s: float = 5.0) -> tuple[bool, list[str], str]:
    """Return (docker_available, stuck_row_ids, notes).

    Best-effort: missing docker is NOT an error condition; the caller
    decides whether health-endpoint pass is enough on its own.
    """
    if shutil.which("docker") is None:
        return False, [], "docker_unavailable"
    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [
                "docker",
                "exec",
                "daytona-db-1",
                "psql",
                "-U",
                "user",
                "-d",
                "daytona",
                "-t",
                "-A",
                "-c",
                "SELECT id FROM sandbox WHERE state IN ('starting', 'pending_build') "
                "AND \"updatedAt\" < NOW() - INTERVAL '60 seconds'",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return True, [], f"docker_probe_error={type(exc).__name__}"
    if completed.returncode != 0:
        return True, [], f"docker_probe_stderr={completed.stderr.strip()[:200]}"
    rows = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return True, rows, ""


def _detect_runner_bootstrap_issue(timeout_s: float = 5.0) -> RunnerBootstrapIssue:
    """Detect the runner-side Docker-in-Docker bootstrap wedge.

    The API health endpoint can be 200 while ``daytona-runner-1`` is unhealthy.
    The failure seen in practice is a stale
    ``/run/docker/containerd/containerd.pid`` inside the runner container: the
    bootstrap shell waits forever for ``docker info`` after dockerd exits.
    """
    if shutil.which("docker") is None:
        return RunnerBootstrapIssue(
            docker_available=False,
            runner_healthy=None,
            notes="runner_probe_skipped=docker_unavailable",
        )

    try:
        health = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [
                "docker",
                "inspect",
                "daytona-runner-1",
                "--format",
                "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return RunnerBootstrapIssue(
            docker_available=True,
            runner_healthy=None,
            notes=f"runner_probe_error={type(exc).__name__}",
        )

    if health.returncode != 0:
        return RunnerBootstrapIssue(
            docker_available=True,
            runner_healthy=None,
            notes=f"runner_probe_stderr={health.stderr.strip()[:200]}",
        )

    status = health.stdout.strip()
    if status == "healthy":
        return RunnerBootstrapIssue(
            docker_available=True,
            runner_healthy=True,
            notes="runner_health=healthy",
        )

    stale_pid, stale_note = _read_stale_runner_containerd_pid(timeout_s)
    notes = f"runner_health={status or 'unknown'}"
    if stale_note:
        notes = f"{notes}; {stale_note}"
    return RunnerBootstrapIssue(
        docker_available=True,
        runner_healthy=False,
        stale_containerd_pid=stale_pid,
        notes=notes,
    )


def _read_stale_runner_containerd_pid(
    timeout_s: float = 5.0,
) -> tuple[str | None, str]:
    """Return the stale containerd PID inside daytona-runner-1, if present."""
    script = (
        "pid_file=/run/docker/containerd/containerd.pid; "
        'if [ -s "$pid_file" ]; then '
        'pid="$(cat "$pid_file" 2>/dev/null || true)"; '
        'if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then '
        'printf "%s" "$pid"; '
        "fi; "
        "fi"
    )
    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv, no host shell
            ["docker", "exec", "daytona-runner-1", "sh", "-lc", script],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"stale_containerd_probe_error={type(exc).__name__}"
    if completed.returncode != 0:
        return None, f"stale_containerd_probe_stderr={completed.stderr.strip()[:200]}"
    stale_pid = completed.stdout.strip() or None
    if stale_pid is None:
        return None, "stale_containerd_pid=none"
    return stale_pid, f"stale_containerd_pid={stale_pid}"


def _run_recovery(timeout_s: float = 10.0) -> tuple[bool, str]:
    """Apply the §6 workaround. Caller must check docker availability first."""
    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [
                "docker",
                "exec",
                "daytona-db-1",
                "psql",
                "-U",
                "user",
                "-d",
                "daytona",
                "-c",
                "UPDATE sandbox SET state='destroyed', \"desiredState\"='destroyed' "
                "WHERE state IN ('starting', 'pending_build') "
                "AND \"updatedAt\" < NOW() - INTERVAL '60 seconds'",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"recovery_error={type(exc).__name__}"
    if completed.returncode != 0:
        return False, f"recovery_stderr={completed.stderr.strip()[:200]}"
    return True, ""


def _recover_runner_bootstrap(timeout_s: float = 30.0) -> tuple[bool, str]:
    """Clear a stale runner containerd PID and restart the runner container."""
    cleanup_script = (
        "set -eu; "
        "pid_file=/run/docker/containerd/containerd.pid; "
        'if [ ! -s "$pid_file" ]; then '
        'echo "stale_containerd_pid_missing"; exit 1; '
        "fi; "
        'pid="$(cat "$pid_file" 2>/dev/null || true)"; '
        'if [ -z "$pid" ]; then '
        'echo "stale_containerd_pid_empty"; exit 1; '
        "fi; "
        'if kill -0 "$pid" 2>/dev/null; then '
        'echo "containerd_pid_alive=$pid"; exit 1; '
        "fi; "
        'rm -f "$pid_file"; '
        'echo "stale_containerd_pid_removed=$pid"'
    )
    try:
        cleanup = subprocess.run(  # noqa: S603 — fixed argv, no host shell
            ["docker", "exec", "daytona-runner-1", "sh", "-lc", cleanup_script],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"runner_recovery_error={type(exc).__name__}"
    if cleanup.returncode != 0:
        detail = (cleanup.stdout or cleanup.stderr).strip()[:200]
        return False, f"runner_recovery_cleanup_failed={detail}"

    try:
        restart = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["docker", "restart", "daytona-runner-1"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"runner_restart_error={type(exc).__name__}"
    if restart.returncode != 0:
        return False, f"runner_restart_stderr={restart.stderr.strip()[:200]}"

    cleanup_note = cleanup.stdout.strip()
    return True, f"runner_restart_succeeded=true; {cleanup_note}"


def probe_tier0(
    api_url: str = "http://localhost:3000/api",
    *,
    timeout_s: float = 5.0,
    auto_recover: bool = False,
) -> Tier0Result:
    """Run the tier-0 probe and return a structured result.

    By default this does NOT execute the destructive recovery SQL. Pass
    ``auto_recover=True`` only from the runner when the operator has
    confirmed they want stuck rows force-destroyed.
    """
    start = time.perf_counter()
    health_url = api_url.rstrip("/") + "/health"
    api_health, health_note = _check_api_health(health_url, timeout_s)

    docker_available, stuck_rows, docker_note = _detect_stuck_rows(timeout_s)
    runner_issue = _detect_runner_bootstrap_issue(timeout_s)
    docker_available = docker_available or runner_issue.docker_available

    notes_parts: list[str] = []
    if health_note:
        notes_parts.append(f"health: {health_note}")
    if docker_note:
        notes_parts.append(docker_note)
    if runner_issue.notes:
        notes_parts.append(runner_issue.notes)

    recovery_attempted = False
    recovery_succeeded: bool | None = None
    recovery_failed = False
    stuck_rows_recovered = False
    runner_recovered = False

    if stuck_rows:
        if auto_recover and docker_available:
            recovery_attempted = True
            ok, rec_note = _run_recovery()
            stuck_rows_recovered = ok
            recovery_failed = recovery_failed or not ok
            if rec_note:
                notes_parts.append(rec_note)
        else:
            notes_parts.append("tier0_manual_recovery_required")

    if runner_issue.runner_healthy is False:
        if auto_recover and runner_issue.stale_containerd_pid:
            recovery_attempted = True
            ok, rec_note = _recover_runner_bootstrap()
            runner_recovered = ok
            recovery_failed = recovery_failed or not ok
            if rec_note:
                notes_parts.append(rec_note)
        else:
            notes_parts.append("tier0_runner_recovery_required")

    if recovery_attempted:
        recovery_succeeded = not recovery_failed

    if api_health != "ok":
        passed = False
    elif stuck_rows and not stuck_rows_recovered:
        passed = False
    elif runner_issue.runner_healthy is False and not runner_recovered:
        passed = False
    else:
        passed = True

    return Tier0Result(
        passed=passed,
        api_health=api_health,
        stuck_rows=stuck_rows,
        docker_available=docker_available,
        runner_healthy=runner_issue.runner_healthy,
        stale_containerd_pid=runner_issue.stale_containerd_pid,
        recovery_attempted=recovery_attempted,
        recovery_succeeded=recovery_succeeded,
        elapsed_s=time.perf_counter() - start,
        notes="; ".join(notes_parts),
    )


__all__ = [
    "Tier0Result",
    "probe_tier0",
    "_check_api_health",
    "_detect_stuck_rows",
    "_detect_runner_bootstrap_issue",
    "_run_recovery",
    "_recover_runner_bootstrap",
]
