"""Stage 3 — evaluation lifecycle + scoring + verdict.

Combines the legacy ``evaluation.py`` and ``lifecycle.py``. The materialize
call now lives inside ``SweevoLifecycle.after_run`` (immediately before
evaluation) so the lifecycle owns the workspace projection contract end
to end and the evaluation helper can assume the bytes are already on disk.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from test_runner.audit.io import atomic_write_json
from test_runner.benchmarks.sweevo._exec import _exec
from test_runner.benchmarks.sweevo.models import (
    SWEEvoInstance,
    SWEEvoResult,
    _CONDA_ACTIVATE,
    _DEFAULT_SANDBOX_SETUP_TIMEOUT,
    _DEFAULT_SWEEVO_TEST_TIMEOUT,
    _REPO_DIR,
)

if TYPE_CHECKING:
    from test_runner.audit.events import Event
    from test_runner.core.config import RunContext
    from test_runner.core.report import PipelineReport

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class _TestSetOutcome:
    passed: int
    runnable_total: int
    dropped_unfindable: int = 0


# ---------------------------------------------------------------------------
# Materialize: commit_to_workspace RPC wrapper
# ---------------------------------------------------------------------------


async def apply_layerstack_to_repo(
    sandbox_id: str,
    repo_dir: str = _REPO_DIR,
) -> None:
    """Project the active overlay onto ``repo_dir`` via the daemon RPC.

    Postcondition: ``repo_dir/.git`` exists inside the sandbox after the
    projection. The check catches a class of bugs where an overlay
    opaque-dir marker shadowed the original ``.git`` and the agent's
    edits silently lose history. The probe runs through ``_exec`` so it
    inspects the container, not the host (the host has no ``/testbed``).
    """
    from sandbox.host.daemon_client import call_daemon_api

    await call_daemon_api(
        sandbox_id,
        "api.commit_to_workspace",
        {"workspace_root": repo_dir},
        timeout=_DEFAULT_SANDBOX_SETUP_TIMEOUT,
    )
    try:
        await _exec(sandbox_id, f"test -d {repo_dir}/.git", check=True)
    except RuntimeError as exc:
        raise AssertionError(
            f"post-commit .git missing in {repo_dir} — overlay opaque-dir "
            f"shadowed the repo: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Test patch application (chunked-base64 stdin substitute)
# ---------------------------------------------------------------------------


async def ensure_sweevo_test_patch(
    instance: SWEEvoInstance,
    sandbox_id: str,
    repo_dir: str = _REPO_DIR,
) -> None:
    """Apply the SWE-EVO test patch so the grader uses the expected test surface.

    Uses chunked base64 over short raw-exec calls because ``sandbox_api.raw_exec``
    does not expose stdin and inline argv would overflow on large patches.
    """
    test_patch = instance.test_patch
    if not test_patch:
        logger.warning(
            "No test patch for %s — F2P tests may not exist",
            instance.instance_id,
        )
        return

    patch_path = f"/tmp/sweevo_test_{uuid4().hex}.patch"
    await _write_file_via_chunked_base64(
        sandbox_id, patch_path, test_patch.encode("utf-8")
    )

    patch_status = await _exec(
        sandbox_id,
        (
            f"cd {repo_dir} && "
            f"if git apply --check {patch_path} >/dev/null 2>&1; then "
            f"echo APPLYABLE; "
            f"elif git apply -R --check {patch_path} >/dev/null 2>&1; then "
            f"echo ALREADY_APPLIED; "
            f"else "
            f"git apply --check {patch_path} 2>&1; "
            f"fi"
        ),
        check=False,
    )
    normalized_status = patch_status.strip()
    if normalized_status == "APPLYABLE":
        out = await _exec(
            sandbox_id,
            f"cd {repo_dir} && git apply {patch_path} 2>&1",
            check=False,
        )
        lower = out.lower()
        if "error" in lower and "already applied" not in lower:
            logger.warning(
                "Test patch for %s had issues: %s",
                instance.instance_id,
                out[:300],
            )
        else:
            logger.info("Ensured test patch for %s", instance.instance_id)
    elif normalized_status == "ALREADY_APPLIED":
        logger.info("Test patch for %s already applied", instance.instance_id)
    else:
        logger.warning(
            "Test patch for %s had issues: %s",
            instance.instance_id,
            patch_status[:300],
        )


async def _write_file_via_chunked_base64(
    sandbox_id: str,
    path: str,
    content: bytes,
    *,
    chunk_size: int = 4096,
) -> None:
    """Write *content* to *path* via repeated short raw-exec calls.

    Pure helper for ``ensure_sweevo_test_patch`` — patches can exceed the
    safe inline-argv size on Linux, so we stage them in chunks instead.
    """
    import base64
    import shlex

    encoded = base64.b64encode(content).decode("ascii")
    encoded_path = f"{path}.b64"
    await _exec(sandbox_id, f": > {shlex.quote(encoded_path)}")
    for start in range(0, len(encoded), chunk_size):
        chunk = encoded[start:start + chunk_size]
        await _exec(
            sandbox_id,
            f"printf %s {shlex.quote(chunk)} >> {shlex.quote(encoded_path)}",
        )
    await _exec(
        sandbox_id,
        f"base64 -d {shlex.quote(encoded_path)} > {shlex.quote(path)} "
        f"&& rm -f {shlex.quote(encoded_path)}",
    )


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


async def _extract_combined_patch(sandbox_id: str, repo_dir: str) -> str:
    patch = await _exec(
        sandbox_id,
        f"cd {repo_dir} && git add -A && git diff HEAD 2>/dev/null || "
        f"git diff 2>/dev/null || echo ''",
    )
    return patch.strip()


async def evaluate_sweevo_result(
    instance: SWEEvoInstance,
    result: SWEEvoResult,
    sandbox_id: str,
    repo_dir: str = _REPO_DIR,
) -> SWEEvoResult:
    """Run FAIL_TO_PASS and PASS_TO_PASS tests to score the result.

    Assumes the active overlay has already been projected onto ``repo_dir``
    by the lifecycle hook before this is called.
    """
    result.agent_patch = await _extract_combined_patch(sandbox_id, repo_dir)
    await ensure_sweevo_test_patch(instance, sandbox_id, repo_dir)

    f2p_total = len(instance.fail_to_pass)
    f2p_passed = 0
    if f2p_total > 0:
        f2p_outcome = await _run_test_set_outcome(
            sandbox_id, repo_dir, instance.fail_to_pass, instance.test_cmds
        )
        f2p_passed = f2p_outcome.passed

    p2p_total = len(instance.pass_to_pass)
    p2p_passed = 0
    p2p_runnable_total = 0
    if p2p_total > 0:
        p2p_outcome = await _run_test_set_outcome(
            sandbox_id, repo_dir, instance.pass_to_pass, instance.test_cmds
        )
        p2p_passed = p2p_outcome.passed
        p2p_runnable_total = p2p_outcome.runnable_total
        if p2p_outcome.dropped_unfindable:
            logger.warning(
                "SWE-EVO %s: treating %d unfindable P2P IDs as dataset "
                "artifacts, not broken tests",
                instance.instance_id,
                p2p_outcome.dropped_unfindable,
            )
    p2p_broken = p2p_runnable_total - p2p_passed

    result.fail_to_pass_passed = f2p_passed
    result.fail_to_pass_total = f2p_total
    result.pass_to_pass_broken = p2p_broken
    result.pass_to_pass_total = p2p_total
    result.fix_rate = f2p_passed / max(f2p_total, 1)
    result.resolved = (f2p_passed == f2p_total) and (p2p_broken == 0)

    logger.info(
        "SWE-EVO %s: resolved=%s fix_rate=%.2f F2P=%d/%d P2P_broken=%d/%d",
        instance.instance_id,
        result.resolved,
        result.fix_rate,
        f2p_passed,
        f2p_total,
        p2p_broken,
        p2p_total,
    )

    return result


async def _run_test_set(
    sandbox_id: str,
    repo_dir: str,
    test_ids: list[str],
    test_cmds: str,
    *,
    timeout: int = _DEFAULT_SWEEVO_TEST_TIMEOUT,
    max_retry_drop_rounds: int = 3,
) -> int:
    outcome = await _run_test_set_outcome(
        sandbox_id,
        repo_dir,
        test_ids,
        test_cmds,
        timeout=timeout,
        max_retry_drop_rounds=max_retry_drop_rounds,
    )
    return outcome.passed


async def _run_test_set_outcome(
    sandbox_id: str,
    repo_dir: str,
    test_ids: list[str],
    test_cmds: str,
    *,
    timeout: int = _DEFAULT_SWEEVO_TEST_TIMEOUT,
    max_retry_drop_rounds: int = 3,
) -> _TestSetOutcome:
    """Run a pytest set, tolerating unfindable IDs from a broken dataset.

    SWE-EVO ships some test_ids that no longer exist at the base commit
    (parametrize strings split across rows, etc.). Pytest exits code 4
    ("no tests ran") when ANY id is unfindable, even with
    ``--continue-on-collection-errors``. The retry loop parses the
    ``ERROR: not found:`` lines, drops those IDs, and re-runs — bounded
    so a pathological mismatch can't loop forever.
    """
    if not test_ids:
        return _TestSetOutcome(passed=0, runnable_total=0)

    candidate_ids = list(test_ids)
    output = ""
    for _round in range(max_retry_drop_rounds + 1):
        if not candidate_ids:
            return _TestSetOutcome(
                passed=0,
                runnable_total=0,
                dropped_unfindable=len(test_ids),
            )
        # Stage test_ids as a JSON file via the chunked-base64 helper. SWE-EVO
        # P2P sets can run into the thousands; inlining them as a Python list
        # literal inside the heredoc blows the docker-exec argv (E2BIG / "exec
        # /bin/bash: argument list too long"). The file-staging path keeps the
        # heredoc tiny — see memory note `checked_batch_apply_argv_limit.md`.
        ids_path = f"/tmp/sweevo_ids_{uuid4().hex}.json"
        runner_path = f"/tmp/sweevo_pytest_runner_{uuid4().hex}.py"
        await _write_file_via_chunked_base64(
            sandbox_id, ids_path, json.dumps(candidate_ids).encode("utf-8")
        )
        await _write_file_via_chunked_base64(
            sandbox_id,
            runner_path,
            _build_test_set_runner_script(ids_path, test_cmds).encode("utf-8"),
        )
        cmd = _build_test_set_command(repo_dir, runner_path)
        try:
            output = await _exec(sandbox_id, cmd, timeout=timeout, check=False)
        except Exception as exc:
            logger.warning("Test execution failed: %s", exc)
            return _TestSetOutcome(passed=0, runnable_total=len(candidate_ids))
        finally:
            await _exec(
                sandbox_id,
                f"rm -f {shlex.quote(ids_path)} {shlex.quote(runner_path)}",
                check=False,
            )

        # Pytest exit 4 + "ERROR: not found:" means at least one ID was
        # unfindable and the whole run was aborted. Drop the bad IDs and
        # retry; anything else is a real result.
        if "EXIT_CODE=4" not in output or "ERROR: not found:" not in output:
            break
        missing = _extract_pytest_not_found_ids(output, repo_dir)
        if not missing:
            break
        before = len(candidate_ids)
        candidate_ids = [t for t in candidate_ids if t not in missing]
        if len(candidate_ids) == before:
            # Couldn't reconcile any of the reported missing IDs (probably
            # prefix-truncated by pytest). Bail rather than infinite-loop.
            logger.warning(
                "pytest reported %d unfindable IDs; could not reconcile any "
                "against candidate set — giving up retry",
                len(missing),
            )
            break
        logger.warning(
            "pytest could not find %d IDs; dropping them and retrying (round %d)",
            before - len(candidate_ids),
            _round + 1,
        )

    if "EXIT_CODE=0" in output:
        passed = len(candidate_ids)
    else:
        passed = _parse_pytest_passed_count(output, len(candidate_ids))
    return _TestSetOutcome(
        passed=passed,
        runnable_total=len(candidate_ids),
        dropped_unfindable=len(test_ids) - len(candidate_ids),
    )


_NOT_FOUND_RE = re.compile(r"^ERROR: not found: (.+?)(?:\n|$)", re.MULTILINE)


def _extract_pytest_not_found_ids(output: str, repo_dir: str) -> set[str]:
    """Parse ``ERROR: not found: <id>`` lines into a set of canonical IDs.

    Pytest prefixes paths with the absolute ``rootdir`` (``/testbed/`` for
    SWE-EVO); strip that so the result can be set-subtracted against the
    repo-relative IDs the dataset ships.
    """
    prefix = repo_dir.rstrip("/") + "/"
    out: set[str] = set()
    for match in _NOT_FOUND_RE.finditer(output):
        raw = match.group(1).strip()
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
        out.add(raw)
    return out


def _build_test_set_runner_script(ids_path: str, test_cmds: str) -> str:
    """Return a real-file pytest runner safe for multiprocessing spawn.

    ``python - <<'PY'`` gives child processes a ``<stdin>`` main module path,
    which breaks Dask tests that use the processes scheduler. A staged script
    with a ``__main__`` guard lets spawn import the parent script without
    recursively starting pytest in the child.
    """
    return (
        "import json, shlex\n"
        "\n"
        "def main():\n"
        f"    with open({json.dumps(ids_path)}) as _f:\n"
        "        test_ids = json.load(_f)\n"
        f"    test_cmd = {json.dumps(test_cmds)}\n"
        "    pytest_argv = shlex.split(test_cmd)[1:] + test_ids\n"
        "    import pytest\n"
        "    exit_code = int(pytest.main(pytest_argv))\n"
        "    print(f'EXIT_CODE={exit_code}')\n"
        "    raise SystemExit(exit_code)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )


def _build_test_set_command(repo_dir: str, runner_path: str) -> str:
    return f"{_CONDA_ACTIVATE} && cd {repo_dir} && python {shlex.quote(runner_path)}"


def _parse_pytest_passed_count(output: str, total: int) -> int:
    m = re.search(r"(\d+) passed", output)
    if m:
        return int(m.group(1))
    return 0


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class SweevoLifecycle:
    """``LifecycleHooks`` implementation for SWE-EVO benchmark runs."""

    def __init__(
        self,
        instance: SWEEvoInstance,
        *,
        repo_dir: str,
        aggregate_jsonl_path: Path | None = None,
    ) -> None:
        self._instance = instance
        self._repo_dir = repo_dir
        self._aggregate_jsonl_path = aggregate_jsonl_path
        self._aborted_reason: str | None = None

    async def before_run(self, ctx: "RunContext") -> None:
        return None

    def on_event(self, event: "Event") -> None:
        return None

    async def on_aborted(self, ctx: "RunContext", reason: str) -> None:
        self._aborted_reason = reason

    async def after_run(self, ctx: "RunContext", report: "PipelineReport") -> None:
        completed_cleanly = (
            report.request_status == "done" and not report.aborted_by_timeout
        )
        result = SWEEvoResult(
            plan_id=report.request_id,
            instance_id=self._instance.instance_id,
            status="completed" if completed_cleanly else "failed",
            duration_s=report.duration_s,
            task_count=report.task_count,
            tasks_completed=report.tasks_completed,
            tasks_failed=report.tasks_failed,
        )
        if completed_cleanly:
            await apply_layerstack_to_repo(report.sandbox_id, self._repo_dir)
            result = await evaluate_sweevo_result(
                self._instance, result, report.sandbox_id, self._repo_dir
            )
        else:
            result.error = (
                "timeout"
                if report.aborted_by_timeout
                else (report.request_status or "unknown")
            )

        atomic_write_json(
            report.run_dir / "sweevo_result.json", dataclasses.asdict(result)
        )
        report.lifecycle_extras["sweevo_result"] = result

        if self._aggregate_jsonl_path is not None:
            self._append_aggregate_line(result, report)

    def _append_aggregate_line(
        self, result: SWEEvoResult, report: "PipelineReport"
    ) -> None:
        assert self._aggregate_jsonl_path is not None
        path = self._aggregate_jsonl_path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "instance_id": result.instance_id,
            "run_id": report.request_id,
            "resolved": result.resolved,
            "fix_rate": result.fix_rate,
            "fail_to_pass_passed": result.fail_to_pass_passed,
            "fail_to_pass_total": result.fail_to_pass_total,
            "pass_to_pass_broken": result.pass_to_pass_broken,
            "pass_to_pass_total": result.pass_to_pass_total,
            "duration_s": result.duration_s,
            "task_count": result.task_count,
            "tasks_completed": result.tasks_completed,
            "tasks_failed": result.tasks_failed,
            "status": result.status,
            "error": result.error,
            "sandbox_id": report.sandbox_id,
            "timestamp_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        line = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
        with open(path, "ab") as handle:
            handle.write(line)


# ---------------------------------------------------------------------------
# Verdict formatter
# ---------------------------------------------------------------------------


def format_verdict(report: "PipelineReport") -> tuple[str, int]:
    """Return the printable verdict line and an exit code."""
    sweevo_result = report.lifecycle_extras.get("sweevo_result")
    resolved = bool(getattr(sweevo_result, "resolved", False))
    fix_rate = float(getattr(sweevo_result, "fix_rate", 0.0))
    line = (
        f"benchmark_sweevo request_id={report.request_id} "
        f"status={report.request_status} "
        f"resolved={resolved} fix_rate={fix_rate:.2f} "
        f"sandbox_id={report.sandbox_id} run_dir={report.run_dir}"
    )
    return line, 0 if resolved else 1


__all__ = [
    "SweevoLifecycle",
    "apply_layerstack_to_repo",
    "ensure_sweevo_test_patch",
    "evaluate_sweevo_result",
    "format_verdict",
]
