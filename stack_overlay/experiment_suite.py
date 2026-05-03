"""Synthetic experiment suite for the depth-100 layer-stack prototype."""

from __future__ import annotations

import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stack_overlay.layer_manager import LayerManager
from stack_overlay.models import (
    ChangeStatus,
    DeleteChange,
    LayerChange,
    WriteChange,
)
from stack_overlay.occ import OccCommitter, content_hash

MAX_DEPTH = 100
SQUASH_TRIGGER = 80
SQUASH_TARGET = 40


@dataclass(frozen=True)
class SuiteProfile:
    e4_runs: int
    e4_shell_ops: int
    e4_api_ops: int
    e5_commits: int
    e6_runs: int
    e7_commits: int
    e10_iterations: int


PROFILES: dict[str, SuiteProfile] = {
    "quick": SuiteProfile(
        e4_runs=3,
        e4_shell_ops=40,
        e4_api_ops=80,
        e5_commits=500,
        e6_runs=10,
        e7_commits=128,
        e10_iterations=500,
    ),
    "standard": SuiteProfile(
        e4_runs=10,
        e4_shell_ops=120,
        e4_api_ops=240,
        e5_commits=3_000,
        e6_runs=30,
        e7_commits=256,
        e10_iterations=3_000,
    ),
    "doc-count": SuiteProfile(
        e4_runs=10,
        e4_shell_ops=480,
        e4_api_ops=960,
        e5_commits=15_000,
        e6_runs=100,
        e7_commits=1_000,
        e10_iterations=10_000,
    ),
}


ProgressLog = Callable[[str], None]


def run_experiment_suite(
    profile_name: str = "standard",
    *,
    progress_log: ProgressLog | None = None,
) -> dict[str, Any]:
    profile = PROFILES[profile_name]
    started = time.perf_counter()
    _log(progress_log, f"suite start profile={profile_name} parameters={profile.__dict__}")
    with tempfile.TemporaryDirectory(prefix="stack-overlay-suite-") as tmp:
        root = Path(tmp)
        experiments = [
            _timed("E4", progress_log, lambda: _run_e4(root / "e4", profile, progress_log)),
            _timed("E5", progress_log, lambda: _run_e5(root / "e5", profile, progress_log)),
            _timed("E6", progress_log, lambda: _run_e6(root / "e6", profile, progress_log)),
            _timed("E7", progress_log, lambda: _run_e7(root / "e7", profile, progress_log)),
            _blocked(
                "E8",
                "End-to-end perf vs current production design",
                "blocked: stack_overlay is not wired into the production shell "
                "runtime, so there is no old-vs-new benchmark target here",
            ),
            _timed("E9", progress_log, lambda: _run_e9(root / "e9")),
            _timed("E10", progress_log, lambda: _run_e10(root / "e10", profile, progress_log)),
            _blocked(
                "E11",
                "Staleness telemetry and optional cutoff behavior",
                "blocked: the prototype has OCC CAS but no shell mode or "
                "staleness policy implementation",
            ),
            _blocked(
                "E12",
                "Lease budget enforcement",
                "blocked: the prototype tracks layer leases but has no age, "
                "pinned-byte, old-manifest, or global eviction budget",
            ),
            _blocked(
                "E13",
                "Shell call mode coverage",
                "blocked: read_only, strict_stale, and exclusive modes are "
                "specified in the plan but not implemented in stack_overlay",
            ),
            _blocked(
                "E14",
                "Mode opt-in and agent UX",
                "blocked: mode classification needs real shell traces and the "
                "mode API from E13",
            ),
        ]

    elapsed_ms = (time.perf_counter() - started) * 1000
    result = {
        "profile": profile_name,
        "parameters": profile.__dict__,
        "elapsed_ms": round(elapsed_ms, 2),
        "summary": _summarize(experiments),
        "experiments": experiments,
    }
    _log(progress_log, f"suite end elapsed_ms={result['elapsed_ms']} summary={result['summary']}")
    return result


def _run_e4(
    root: Path,
    profile: SuiteProfile,
    progress_log: ProgressLog | None,
) -> dict[str, Any]:
    run_results = []
    total_violations: list[str] = []
    for run_index in range(profile.e4_runs):
        _log(progress_log, f"E4 run {run_index + 1}/{profile.e4_runs} start")
        result = _run_e4_once(
            root / f"run-{run_index}",
            run_index,
            profile.e4_shell_ops,
            profile.e4_api_ops,
        )
        run_results.append(result)
        total_violations.extend(result["violations"])
        _log(
            progress_log,
            "E4 run "
            f"{run_index + 1}/{profile.e4_runs} done "
            f"committed={result['committed']} rejected={result['rejected']} "
            f"violations={len(result['violations'])}",
        )

    return _result(
        "E4",
        "Correctness under concurrent agents",
        "passed" if not total_violations else "failed",
        {
            "runs": profile.e4_runs,
            "shell_ops": profile.e4_shell_ops * profile.e4_runs,
            "api_ops": profile.e4_api_ops * profile.e4_runs,
            "violations": len(total_violations),
            "sample_violations": total_violations[:5],
            "doc_rate_equivalent": "8 shell/sec + 16 api edits/sec, compressed",
        },
        "Synthetic threaded harness; validates frozen lease reads and OCC "
        "same-path rejection against the layer manager.",
    )


def _run_e4_once(
    root: Path,
    run_index: int,
    shell_ops: int,
    api_ops: int,
) -> dict[str, Any]:
    initial = {f"shared/{index}.txt": f"base-{index}\n" for index in range(24)}
    manager = LayerManager.create(
        root,
        initial,
        max_depth=MAX_DEPTH,
        squash_trigger=SQUASH_TRIGGER,
        squash_target=SQUASH_TARGET,
    )
    occ = OccCommitter(manager)
    records: list[tuple[str, str, ChangeStatus, int]] = []
    violations: list[str] = []
    record_lock = threading.Lock()

    def record(path: str, content: str, status: ChangeStatus, version: int) -> None:
        with record_lock:
            records.append((path, content, status, version))

    def shell_call(index: int) -> None:
        lease = manager.acquire()
        try:
            if index % 2 == 0:
                path = f"shared/{index % 24}.txt"
            else:
                path = f"shell/{run_index}-{index}.txt"
            base_text, base_exists = manager.read_text(path, lease.manifest)
            time.sleep((index % 5) / 10_000)
            later = manager.read_text(path, lease.manifest)
            if later != (base_text, base_exists):
                violations.append(f"torn snapshot read for {path}")
            change = WriteChange(
                path,
                f"shell-{run_index}-{index}\n",
                base_existed=base_exists,
                base_hash=content_hash(base_text) if base_exists else "",
            )
            result = occ.apply([change])
            record(path, change.final_content, result.files[0].status, result.manifest.version)
        finally:
            manager.release(lease)

    def api_edit(index: int) -> None:
        path = f"shared/{index % 24}.txt"
        base_text, base_exists = manager.read_text(path)
        time.sleep((index % 3) / 10_000)
        change = WriteChange(
            path,
            f"api-{run_index}-{index}\n",
            base_existed=base_exists,
            base_hash=content_hash(base_text) if base_exists else "",
        )
        result = occ.apply([change])
        record(path, change.final_content, result.files[0].status, result.manifest.version)

    _run_threaded(shell_call, shell_ops, api_edit, api_ops)

    expected: dict[str, tuple[int, str]] = {}
    rejected: list[tuple[str, str]] = []
    for path, content, status, version in records:
        if status is ChangeStatus.COMMITTED:
            current = expected.get(path)
            if current is None or version >= current[0]:
                expected[path] = (version, content)
        else:
            rejected.append((path, content))

    for path, (_version, content) in expected.items():
        final_text, exists = manager.read_text(path)
        if not exists or final_text != content:
            violations.append(f"accepted final mismatch for {path}")

    for path, content in rejected:
        final_text, exists = manager.read_text(path)
        if exists and final_text == content:
            violations.append(f"rejected content landed for {path}")

    return {
        "records": len(records),
        "committed": sum(1 for item in records if item[2] is ChangeStatus.COMMITTED),
        "rejected": sum(1 for item in records if item[2] is not ChangeStatus.COMMITTED),
        "violations": violations,
    }


def _run_e5(
    root: Path,
    profile: SuiteProfile,
    progress_log: ProgressLog | None,
) -> dict[str, Any]:
    manager = LayerManager.create(
        root,
        {"base.txt": "base\n"},
        max_depth=MAX_DEPTH,
        squash_trigger=SQUASH_TRIGGER,
        squash_target=SQUASH_TARGET,
    )
    depths = [manager.snapshot().depth]
    timings_ms: list[float] = []
    backpressure = 0
    started = time.perf_counter()
    log_every = _progress_step(profile.e5_commits)
    for index in range(profile.e5_commits):
        before = time.perf_counter()
        try:
            manager.commit(
                [
                    LayerChange(
                        f"commit/{index % 128}.txt",
                        "write",
                        f"{index}\n",
                    )
                ]
            )
        except RuntimeError:
            backpressure += 1
        timings_ms.append((time.perf_counter() - before) * 1000)
        depths.append(manager.snapshot().depth)
        if (index + 1) % log_every == 0 or index + 1 == profile.e5_commits:
            _log(
                progress_log,
                f"E5 commits={index + 1}/{profile.e5_commits} "
                f"depth={depths[-1]} max_depth={max(depths)} "
                f"backpressure={backpressure}",
            )
    elapsed = time.perf_counter() - started
    max_depth = max(depths)
    passed = backpressure == 0 and SQUASH_TARGET <= max_depth <= SQUASH_TRIGGER
    return _result(
        "E5",
        "Squash throughput vs append rate",
        "passed" if passed else "failed",
        {
            "commits": profile.e5_commits,
            "elapsed_s": round(elapsed, 3),
            "effective_commits_per_s": round(profile.e5_commits / elapsed, 2),
            "max_depth": max_depth,
            "final_depth": manager.snapshot().depth,
            "backpressure_events": backpressure,
            "commit_ms_p95": _percentile(timings_ms, 95),
            "commit_ms_p99": _percentile(timings_ms, 99),
            "max_allowed_depth": MAX_DEPTH,
            "squash_trigger": SQUASH_TRIGGER,
            "squash_target": SQUASH_TARGET,
        },
        "No coalescing worker exists yet; this measures synchronous squash "
        "inside the experimental LayerManager.",
    )


def _run_e6(
    root: Path,
    profile: SuiteProfile,
    progress_log: ProgressLog | None,
) -> dict[str, Any]:
    errors: list[str] = []
    for run_index in range(profile.e6_runs):
        manager = LayerManager.create(
            root / f"run-{run_index}",
            {"base.txt": "base\n"},
            max_depth=MAX_DEPTH,
            squash_trigger=30,
            squash_target=10,
        )
        lease = manager.acquire()
        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                try:
                    if manager.read_text("base.txt", lease.manifest) != ("base\n", True):
                        errors.append(f"leased read changed in run {run_index}")
                        return
                except OSError as exc:
                    errors.append(f"leased read error in run {run_index}: {exc}")
                    return

        thread = threading.Thread(target=reader)
        thread.start()
        try:
            for index in range(36):
                manager.commit(
                    [LayerChange(f"churn/{index}.txt", "write", f"{index}\n")]
                )
            if not manager.retired_layers():
                errors.append(f"no retired layers while lease pinned run {run_index}")
        finally:
            stop.set()
            thread.join(timeout=2)
            manager.release(lease)
        if manager.retired_layers():
            errors.append(f"retired layers survived release run {run_index}")
        if (run_index + 1) % _progress_step(profile.e6_runs) == 0:
            _log(
                progress_log,
                f"E6 runs={run_index + 1}/{profile.e6_runs} errors={len(errors)}",
            )

    return _result(
        "E6",
        "Layer GC under contention",
        "passed" if not errors else "failed",
        {
            "runs": profile.e6_runs,
            "errors": len(errors),
            "sample_errors": errors[:5],
        },
        "Filesystem lease-retention check; it cannot reproduce kernel overlay "
        "file-handle errors without the Daytona mount runtime.",
    )


def _run_e7(
    root: Path,
    profile: SuiteProfile,
    progress_log: ProgressLog | None,
) -> dict[str, Any]:
    manager = LayerManager.create(
        root,
        {f"tracked/{index}.txt": "x" * 1024 for index in range(128)},
        max_depth=MAX_DEPTH,
        squash_trigger=SQUASH_TRIGGER,
        squash_target=SQUASH_TARGET,
    )
    peak_bytes = _storage_bytes(root)
    log_every = _progress_step(profile.e7_commits)
    for index in range(profile.e7_commits):
        manager.commit(
            [LayerChange(f"workload/{index}.txt", "write", "y" * 2048 + "\n")]
        )
        peak_bytes = max(peak_bytes, _storage_bytes(root))
        if (index + 1) % log_every == 0 or index + 1 == profile.e7_commits:
            _log(
                progress_log,
                f"E7 commits={index + 1}/{profile.e7_commits} "
                f"peak_mb={round(peak_bytes / (1024 * 1024), 3)} "
                f"depth={manager.snapshot().depth}",
            )
    peak_mb = peak_bytes / (1024 * 1024)
    return _result(
        "E7",
        "Tmpfs sizing under realistic agent workloads",
        "partial",
        {
            "synthetic_files": 128 + profile.e7_commits,
            "peak_mb": round(peak_mb, 3),
            "under_256mb": peak_bytes < 256 * 1024 * 1024,
            "final_depth": manager.snapshot().depth,
        },
        "Synthetic layer-storage probe only; the planned replay of real "
        "codeact/dep-install/parallel-test traces is still needed.",
    )


def _run_e9(root: Path) -> dict[str, Any]:
    manager = LayerManager.create(
        root,
        {"base.txt": "base\n"},
        max_depth=MAX_DEPTH,
        squash_trigger=SQUASH_TRIGGER,
        squash_target=SQUASH_TARGET,
    )
    manager.commit([LayerChange("committed.txt", "write", "ok\n")])
    (root / "L9999").mkdir()
    (root / "L9999" / "partial.txt").write_text("partial\n", encoding="utf-8")
    (root / "B9999").mkdir()
    (root / "B9999" / "partial.txt").write_text("partial\n", encoding="utf-8")
    (root / ".manifest.json.tmp").write_text("partial", encoding="utf-8")

    restarted = LayerManager(
        root,
        max_depth=MAX_DEPTH,
        squash_trigger=SQUASH_TRIGGER,
        squash_target=SQUASH_TARGET,
    )
    missing_before = restarted.missing_manifest_layers()
    recovered = restarted.recover_unreferenced_layers()
    missing_after = restarted.missing_manifest_layers()
    committed_text = restarted.read_text("committed.txt")
    partial_survived = (root / "L9999").exists() or (root / "B9999").exists()
    passed = (
        not missing_before
        and not missing_after
        and not partial_survived
        and committed_text == ("ok\n", True)
    )
    return _result(
        "E9",
        "Failure recovery",
        "passed" if passed else "failed",
        {
            "missing_before": list(missing_before),
            "missing_after": list(missing_after),
            "recovered_unreferenced_layers": recovered,
            "partial_dirs_removed": not partial_survived,
            "committed_layer_read": committed_text == ("ok\n", True),
        },
        "Covers mid-commit/mid-squash dirs before manifest publish. Dangling "
        "manifest references are detected by missing_manifest_layers(), not "
        "repairable in this prototype.",
    )


def _run_e10(
    root: Path,
    profile: SuiteProfile,
    progress_log: ProgressLog | None,
) -> dict[str, Any]:
    manager = LayerManager.create(
        root,
        {"seed.txt": "seed\n"},
        max_depth=MAX_DEPTH,
        squash_trigger=SQUASH_TRIGGER,
        squash_target=SQUASH_TARGET,
    )
    occ = OccCommitter(manager)
    violations: list[str] = []
    log_every = _progress_step(profile.e10_iterations)
    for index in range(profile.e10_iterations):
        _e10_same_path_conflict(manager, occ, index, violations)
        _e10_delete_noop(manager, occ, index, violations)
        _e10_create_conflict(manager, occ, index, violations)
        if (index + 1) % log_every == 0 or index + 1 == profile.e10_iterations:
            _log(
                progress_log,
                f"E10 iterations={index + 1}/{profile.e10_iterations} "
                f"violations={len(violations)} depth={manager.snapshot().depth}",
            )
    status = "partial" if not violations else "failed"
    return _result(
        "E10",
        "OCC and direct-merge correctness",
        status,
        {
            "occ_gated_iterations": profile.e10_iterations,
            "occ_gated_violations": len(violations),
            "sample_violations": violations[:5],
            "direct_merge_exceptions_covered": False,
        },
        "OCC-gated write/delete/create cases passed when there are zero "
        "violations. Direct merge types are not implemented in stack_overlay, "
        "so this cannot be a full E10 pass.",
    )


def _e10_same_path_conflict(
    manager: LayerManager,
    occ: OccCommitter,
    index: int,
    violations: list[str],
) -> None:
    path = f"same/{index % 64}.txt"
    other = f"same-disjoint/{index % 64}.txt"
    manager.commit([LayerChange(path, "write", "base\n")])
    lease = manager.acquire()
    try:
        base, existed = manager.read_text(path, lease.manifest)
        if not existed:
            violations.append(f"missing same-path base {index}")
            return
        winner_content = f"winner-{index}\n"
        loser_content = f"loser-{index}\n"
        other_content = f"other-{index}\n"
        other_base, other_exists = manager.read_text(other)
        winner = occ.apply([WriteChange(path, winner_content, True, content_hash(base))])
        loser = occ.apply(
            [
                WriteChange(path, loser_content, True, content_hash(base)),
                WriteChange(
                    other,
                    other_content,
                    other_exists,
                    content_hash(other_base) if other_exists else "",
                ),
            ]
        )
        final_text, _exists = manager.read_text(path)
        other_text, other_exists = manager.read_text(other)
        if not winner.success:
            violations.append(f"winner rejected {index}")
        if loser.files[0].status is not ChangeStatus.ABORTED_VERSION:
            violations.append(f"same-path loser accepted {index}")
        if loser.files[1].status is not ChangeStatus.COMMITTED:
            violations.append(f"disjoint write rejected {index}")
        if final_text == loser_content:
            violations.append(f"loser landed {index}")
        if not other_exists or other_text != other_content:
            violations.append(f"disjoint write missing {index}")
    finally:
        manager.release(lease)


def _e10_delete_noop(
    manager: LayerManager,
    occ: OccCommitter,
    index: int,
    violations: list[str],
) -> None:
    path = f"delete/{index % 64}.txt"
    manager.commit([LayerChange(path, "write", "base\n")])
    lease = manager.acquire()
    try:
        base, existed = manager.read_text(path, lease.manifest)
        if not existed:
            violations.append(f"missing delete base {index}")
            return
        first = occ.apply([DeleteChange(path, content_hash(base))])
        second = occ.apply([DeleteChange(path, content_hash(base))])
        final_text, final_exists = manager.read_text(path)
        if not first.success or not second.success:
            violations.append(f"delete no-op rejected {index}")
        if final_exists or final_text:
            violations.append(f"delete no-op left content {index}")
    finally:
        manager.release(lease)


def _e10_create_conflict(
    manager: LayerManager,
    occ: OccCommitter,
    index: int,
    violations: list[str],
) -> None:
    path = f"create/{index % 64}.txt"
    manager.commit([LayerChange(path, "delete")])
    lease = manager.acquire()
    try:
        _base, existed = manager.read_text(path, lease.manifest)
        if existed:
            violations.append(f"unexpected create base {index}")
            return
        winner = occ.apply([WriteChange(path, f"winner-{index}\n", False)])
        loser = occ.apply([WriteChange(path, f"loser-{index}\n", False)])
        final_text, final_exists = manager.read_text(path)
        if not winner.success:
            violations.append(f"create winner rejected {index}")
        if loser.files[0].status is not ChangeStatus.ABORTED_VERSION:
            violations.append(f"create loser accepted {index}")
        if not final_exists or final_text != f"winner-{index}\n":
            violations.append(f"create winner not final {index}")
    finally:
        manager.release(lease)


def _run_threaded(
    shell_call: Callable[[int], None],
    shell_ops: int,
    api_edit: Callable[[int], None],
    api_ops: int,
) -> None:
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = [
            pool.submit(shell_call, index)
            for index in range(shell_ops)
        ] + [
            pool.submit(api_edit, index)
            for index in range(api_ops)
        ]
        for future in as_completed(futures):
            future.result()


def _blocked(experiment_id: str, name: str, note: str) -> dict[str, Any]:
    return _result(experiment_id, name, "blocked", {}, note)


def _timed(
    experiment_id: str,
    progress_log: ProgressLog | None,
    run: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    started = time.perf_counter()
    _log(progress_log, f"{experiment_id} start")
    result = run()
    elapsed_ms = (time.perf_counter() - started) * 1000
    result["elapsed_ms"] = round(elapsed_ms, 2)
    _log(
        progress_log,
        f"{experiment_id} end status={result['status']} elapsed_ms={result['elapsed_ms']}",
    )
    return result


def _result(
    experiment_id: str,
    name: str,
    status: str,
    metrics: dict[str, Any],
    note: str,
) -> dict[str, Any]:
    return {
        "id": experiment_id,
        "name": name,
        "status": status,
        "metrics": metrics,
        "note": note,
    }


def _summarize(experiments: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for experiment in experiments:
        status = str(experiment["status"])
        summary[status] = summary.get(status, 0) + 1
    return summary


def _percentile(values: list[float], pct: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * pct / 100)))
    return round(ordered[index], 4)


def _progress_step(total: int) -> int:
    return max(1, total // 10)


def _log(progress_log: ProgressLog | None, message: str) -> None:
    if progress_log is not None:
        progress_log(message)


def _storage_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total
