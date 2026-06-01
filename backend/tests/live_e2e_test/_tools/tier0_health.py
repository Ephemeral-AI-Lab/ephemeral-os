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

import os
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


def _resolve_provider_for_probe() -> str:
    raw = os.environ.get("EOS_SANDBOX_PROVIDER")
    if raw is not None:
        return raw.strip().lower()
    return "docker"


def _docker_run_flags() -> list[str]:
    """CLI flags equivalent to DockerProviderAdapter.create — single source of truth."""
    from sandbox.provider.docker.client import resolve_run_flags

    return list(resolve_run_flags())


def _resolve_docker_probe_image(image: str | None) -> str:
    return (image or os.environ.get("EOS_LIVE_E2E_IMAGE") or "").strip()


def probe_tier0_docker(
    *,
    image: str | None = None,
    timeout_s: float = 30.0,
) -> Tier0Result:
    """Tier-0 probe for the Docker provider.

    Four sub-checks (each recorded separately in notes):
      1. ``docker info``                                — daemon up
      2. ``docker image inspect <resolved image>``      — image local
      3. capability probe in a throwaway container     — git + /testbed + unshare -Urm
      4. EOS_DOCKER_PRIVILEGED value                   — captured for artifact diff
    """
    start = time.perf_counter()
    image = _resolve_docker_probe_image(image)
    notes: list[str] = []
    privileged_value = os.environ.get("EOS_DOCKER_PRIVILEGED", "")
    notes.append(f"eos_docker_privileged={privileged_value!r}")

    if shutil.which("docker") is None:
        return Tier0Result(
            passed=False,
            api_health="error",
            docker_available=False,
            elapsed_s=time.perf_counter() - start,
            notes="; ".join([*notes, "docker_info=docker_unavailable"]),
        )

    info = subprocess.run(  # noqa: S603 — fixed argv
        ["docker", "info"],
        capture_output=True, text=True, timeout=timeout_s, check=False,
    )
    if info.returncode != 0:
        notes.append(f"docker_info=fail rc={info.returncode}")
        return Tier0Result(
            passed=False, api_health="error", docker_available=True,
            elapsed_s=time.perf_counter() - start, notes="; ".join(notes),
        )
    notes.append("docker_info=ok")

    if not image:
        notes.append("image_inspect=missing_live_image_default")
        return Tier0Result(
            passed=False, api_health="error", docker_available=True,
            elapsed_s=time.perf_counter() - start, notes="; ".join(notes),
        )

    inspect = subprocess.run(  # noqa: S603 — fixed argv
        ["docker", "image", "inspect", image],
        capture_output=True, text=True, timeout=timeout_s, check=False,
    )
    if inspect.returncode != 0:
        notes.append(f"image_inspect=fail image={image!r}")
        return Tier0Result(
            passed=False, api_health="error", docker_available=True,
            elapsed_s=time.perf_counter() - start, notes="; ".join(notes),
        )
    notes.append(f"image_inspect=ok image={image!r}")

    cap_script = (
        "command -v git >/dev/null && test -w /testbed && "
        "mkdir -p /eos/mount && test -w /eos/mount && "
        "rm -rf /eos/mount/tier0-overlay-probe && "
        "mkdir -p /eos/mount/tier0-overlay-probe/lower "
        "/eos/mount/tier0-overlay-probe/upper "
        "/eos/mount/tier0-overlay-probe/work "
        "/eos/mount/tier0-overlay-probe/merged && "
        "echo ok >/eos/mount/tier0-overlay-probe/lower/probe.txt && "
        "unshare -Urm sh -c 'mount -t overlay overlay "
        "-o lowerdir=/eos/mount/tier0-overlay-probe/lower,"
        "upperdir=/eos/mount/tier0-overlay-probe/upper,"
        "workdir=/eos/mount/tier0-overlay-probe/work "
        "/eos/mount/tier0-overlay-probe/merged && "
        "cat /eos/mount/tier0-overlay-probe/merged/probe.txt && "
        "umount /eos/mount/tier0-overlay-probe/merged'"
    )
    cap_argv = ["docker", "run", "--rm", *_docker_run_flags(), image, "sh", "-c", cap_script]
    cap = subprocess.run(  # noqa: S603 — fixed argv
        cap_argv, capture_output=True, text=True, timeout=timeout_s, check=False,
    )
    if cap.returncode != 0:
        detail = (cap.stderr or cap.stdout).strip().replace("\n", " ")[:200]
        notes.append(f"capability_probe=fail rc={cap.returncode} detail={detail!r}")
        return Tier0Result(
            passed=False, api_health="error", docker_available=True,
            elapsed_s=time.perf_counter() - start, notes="; ".join(notes),
        )
    notes.append("capability_probe=ok")

    return Tier0Result(
        passed=True, api_health="ok", docker_available=True,
        elapsed_s=time.perf_counter() - start, notes="; ".join(notes),
    )


def probe_tier0(
    api_url: str = "http://localhost:3000/api",
    *,
    timeout_s: float = 5.0,
    auto_recover: bool = False,
) -> Tier0Result:
    """Run the tier-0 probe and return a structured result.

    Dispatches on ``EOS_SANDBOX_PROVIDER``: Docker is the default when unset;
    daytona keeps the HTTP-health +
    stuck-rows + runner-bootstrap probe stack; docker runs the four-sub-check
    capability probe in :func:`probe_tier0_docker`; unknown providers fail loud.

    By default this does NOT execute the destructive recovery SQL. Pass
    ``auto_recover=True`` only from the runner when the operator has
    confirmed they want stuck rows force-destroyed.
    """
    provider = _resolve_provider_for_probe()
    if provider == "docker":
        return probe_tier0_docker(timeout_s=max(timeout_s, 30.0))
    if provider != "daytona":
        return Tier0Result(
            passed=False, api_health="error",
            notes=f"unsupported provider for tier 0 probe: {provider!r}",
        )

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
    "probe_tier0_docker",
    "_check_api_health",
    "_detect_stuck_rows",
    "_detect_runner_bootstrap_issue",
    "_run_recovery",
    "_recover_runner_bootstrap",
]
