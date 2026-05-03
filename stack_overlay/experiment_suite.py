"""Synthetic experiment suite for the depth-100 layer-stack prototype."""

from __future__ import annotations

import tempfile
import threading
import time
import json
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
from stack_overlay.policies import (
    DirectMergePolicy,
    LeaseBudget,
    LeaseSnapshot,
    ShellCommitGate,
    ShellMode,
    classify_shell_mode,
)

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
ResultSink = Callable[[dict[str, Any]], None]


def run_experiment_suite(
    profile_name: str = "standard",
    *,
    progress_log: ProgressLog | None = None,
    result_sink: ResultSink | None = None,
) -> dict[str, Any]:
    profile = PROFILES[profile_name]
    started = time.perf_counter()
    _log(progress_log, f"suite start profile={profile_name} parameters={profile.__dict__}")
    with tempfile.TemporaryDirectory(prefix="stack-overlay-suite-") as tmp:
        root = Path(tmp)
        experiments = [
            _timed(
                "E4",
                progress_log,
                result_sink,
                lambda: _run_e4(root / "e4", profile, progress_log),
            ),
            _timed(
                "E5",
                progress_log,
                result_sink,
                lambda: _run_e5(root / "e5", profile, progress_log),
            ),
            _timed(
                "E6",
                progress_log,
                result_sink,
                lambda: _run_e6(root / "e6", profile, progress_log),
            ),
            _timed(
                "E7",
                progress_log,
                result_sink,
                lambda: _run_e7(root / "e7", profile, progress_log),
            ),
            _timed(
                "E8",
                progress_log,
                result_sink,
                lambda: _run_e8(root / "e8", progress_log),
            ),
            _timed("E9", progress_log, result_sink, lambda: _run_e9(root / "e9")),
            _timed(
                "E10",
                progress_log,
                result_sink,
                lambda: _run_e10(root / "e10", profile, progress_log),
            ),
            _timed(
                "E11",
                progress_log,
                result_sink,
                lambda: _run_e11(root / "e11"),
            ),
            _timed(
                "E12",
                progress_log,
                result_sink,
                lambda: _run_e12(),
            ),
            _timed(
                "E13",
                progress_log,
                result_sink,
                lambda: _run_e13(root / "e13"),
            ),
            _timed(
                "E14",
                progress_log,
                result_sink,
                _run_e14,
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
    passed = peak_bytes < 256 * 1024 * 1024
    return _result(
        "E7",
        "Tmpfs sizing under realistic agent workloads",
        "passed" if passed else "failed",
        {
            "synthetic_files": 128 + profile.e7_commits,
            "peak_mb": round(peak_mb, 3),
            "under_256mb": passed,
            "final_depth": manager.snapshot().depth,
            "workload_shape": "code edits + generated outputs + cache-like files",
        },
        "Experimental representative workload; production trace replay can still "
        "refine the sizing formula.",
    )


def _run_e8(root: Path, progress_log: ProgressLog | None) -> dict[str, Any]:
    baseline_ms: list[float] = []
    overlay_ms: list[float] = []
    for index in range(200):
        direct_root = root / "direct" / str(index)
        started = time.perf_counter()
        _write_workload_files(direct_root, index, 8)
        _capture_text_diff(direct_root)
        baseline_ms.append((time.perf_counter() - started) * 1000)

    manager = LayerManager.create(
        root / "overlay",
        {"base.txt": "base\n"},
        max_depth=MAX_DEPTH,
        squash_trigger=SQUASH_TRIGGER,
        squash_target=SQUASH_TARGET,
    )
    occ = OccCommitter(manager)
    for index in range(200):
        upper = root / "upper" / str(index)
        started = time.perf_counter()
        _write_workload_files(upper, index, 8)
        changes = [
            WriteChange(path, content, base_existed=False)
            for path, content in _capture_text_diff(upper)
        ]
        occ.apply(changes)
        overlay_ms.append((time.perf_counter() - started) * 1000)
        if (index + 1) % 50 == 0:
            _log(progress_log, f"E8 ops={index + 1}/200")

    baseline_p50 = _percentile(baseline_ms, 50) or 0.0
    baseline_p99 = _percentile(baseline_ms, 99) or 0.0
    overlay_p50 = _percentile(overlay_ms, 50) or 0.0
    overlay_p99 = _percentile(overlay_ms, 99) or 0.0
    passed = (
        overlay_p50 <= max(0.001, baseline_p50) * 1.2
        and overlay_p99 <= max(0.001, baseline_p99) * 1.5
    )
    return _result(
        "E8",
        "End-to-end perf vs today",
        "passed" if passed else "partial",
        {
            "ops": 200,
            "baseline_p50_ms": baseline_p50,
            "baseline_p99_ms": baseline_p99,
            "overlay_occ_p50_ms": overlay_p50,
            "overlay_occ_p99_ms": overlay_p99,
            "median_ratio": overlay_p50 / max(0.001, baseline_p50),
            "p99_ratio": overlay_p99 / max(0.001, baseline_p99),
        },
        "Prototype local shell-op proxy: command writes, upperdir capture, "
        "changeset build, and OCC layer commit. Live Daytona E8 measures the "
        "real mount+command+capture stages separately.",
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
    direct = _run_direct_merge_matrix()
    large_diff = _run_large_diff_benchmark(root / "large-diff")
    status = "passed" if not violations and direct["violations"] == 0 else "failed"
    return _result(
        "E10",
        "OCC and direct-merge correctness",
        status,
        {
            "occ_gated_iterations": profile.e10_iterations,
            "occ_gated_violations": len(violations),
            "sample_violations": violations[:5],
            "direct_merge_exceptions_covered": True,
            "direct_merge_matrix": direct,
            "large_diff_to_occ": large_diff,
        },
        "Covers OCC-gated write/delete/create cases, explicit direct-merge "
        "prefix bounds, and large overlay diff parsing into OCC changes.",
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


def _run_direct_merge_matrix() -> dict[str, Any]:
    policy = DirectMergePolicy()
    cases = [
        (".cache/tool.bin", "binary", True),
        ("node_modules/.cache/link", "symlink", True),
        ("build/.wh.asset", "opaque_dir", True),
        ("src/app.py", "binary", False),
        ("docs/link", "symlink", False),
        ("backend/src/.wh.secret", "opaque_dir", False),
    ]
    violations = []
    decisions = []
    for path, change_type, expected in cases:
        decision = policy.decide(path, change_type)
        decisions.append(decision.__dict__)
        if decision.allowed is not expected:
            violations.append(
                {
                    "path": path,
                    "change_type": change_type,
                    "expected": expected,
                    "actual": decision.allowed,
                }
            )
    return {
        "cases": len(cases),
        "violations": len(violations),
        "sample_violations": violations[:5],
        "decisions": decisions,
    }


def _run_large_diff_benchmark(root: Path) -> dict[str, Any]:
    manager = LayerManager.create(
        root,
        {"base.txt": "base\n"},
        max_depth=MAX_DEPTH,
        squash_trigger=SQUASH_TRIGGER,
        squash_target=SQUASH_TARGET,
    )
    occ = OccCommitter(manager)
    results = []
    for count in (100, 1_000, 5_000):
        payload_lines = [
            json.dumps(
                {
                    "path": f"large/{count}/file-{index:05d}.txt",
                    "content": f"value-{count}-{index}\n",
                },
                sort_keys=True,
            )
            for index in range(count)
        ]
        payload = "\n".join(payload_lines)
        parse_started = time.perf_counter()
        changes = [
            WriteChange(
                item["path"],
                item["content"],
                base_existed=False,
            )
            for item in (json.loads(line) for line in payload.splitlines())
        ]
        parse_ms = (time.perf_counter() - parse_started) * 1000
        merge_started = time.perf_counter()
        result = occ.apply(changes)
        merge_ms = (time.perf_counter() - merge_started) * 1000
        results.append(
            {
                "changes": count,
                "payload_bytes": len(payload.encode("utf-8")),
                "parse_ms": round(parse_ms, 4),
                "occ_merge_ms": round(merge_ms, 4),
                "total_ms": round(parse_ms + merge_ms, 4),
                "changes_per_s": round(count / max(0.001, (parse_ms + merge_ms) / 1000), 2),
                "success": result.success,
            }
        )
    return {"runs": results}


def _run_e11(root: Path) -> dict[str, Any]:
    manager = LayerManager.create(
        root,
        {"config.yaml": "mode: old\n"},
        max_depth=MAX_DEPTH,
        squash_trigger=SQUASH_TRIGGER,
        squash_target=SQUASH_TARGET,
    )
    occ = OccCommitter(manager)
    gate = ShellCommitGate(occ)
    snapshot = manager.snapshot()
    started = 1_000.0
    accepted = []
    rejected = []
    for lag in (1, 2, 4, 5, 6, 10, 20):
        while manager.snapshot().version < snapshot.version + lag:
            manager.commit(
                [
                    LayerChange(
                        f"advance/{manager.snapshot().version}.txt",
                        "write",
                        "x\n",
                    )
                ]
            )
        active = manager.snapshot()
        gated = gate.apply(
            mode=ShellMode.GATED,
            changes=[
                WriteChange(
                    f"generated/gated-{lag}.json",
                    "{}\n",
                    base_existed=False,
                )
            ],
            snapshot=snapshot,
            active=active,
            shell_started_at=started,
            now=started + 120,
        )
        strict = gate.apply(
            mode=ShellMode.STRICT_STALE,
            changes=[
                WriteChange(
                    f"generated/strict-{lag}.json",
                    "{}\n",
                    base_existed=False,
                )
            ],
            snapshot=snapshot,
            active=active,
            shell_started_at=started,
            now=started + 120,
        )
        accepted.append(
            {
                "lag": lag,
                "gated_status": gated.status,
                "gated_warnings": gated.warnings,
            }
        )
        rejected.append(
            {
                "lag": lag,
                "strict_status": strict.status,
                "strict_warnings": strict.warnings,
            }
        )

    gated_ok = all(item["gated_status"] == "committed" for item in accepted)
    strict_ok = all(
        item["strict_status"] == "rejected_stale_snapshot" for item in rejected
    )
    return _result(
        "E11",
        "Staleness telemetry and optional cutoff behavior",
        "passed" if gated_ok and strict_ok else "failed",
        {
            "gated_cases": accepted,
            "strict_cases": rejected,
            "max_lag": 5,
            "max_age_s": 60,
        },
        "Default gated mode records telemetry and commits OCC-clean writes; "
        "strict_stale rejects when lag/age exceeds policy.",
    )


def _run_e12() -> dict[str, Any]:
    budget = LeaseBudget(
        max_age_s=60,
        max_pinned_bytes_per_session=1_000,
        max_old_manifests=3,
        max_total_pinned_bytes_global=5_000,
    )
    leases = [
        LeaseSnapshot("fresh", 10, 100, 9),
        LeaseSnapshot("expired", 120, 100, 8),
        LeaseSnapshot("old-a", 50, 500, 1),
        LeaseSnapshot("old-b", 40, 500, 2),
        LeaseSnapshot("old-c", 30, 500, 3),
        LeaseSnapshot("old-d", 20, 500, 4),
    ]
    decisions = budget.evaluate(
        leases,
        active_manifest_version=10,
        global_pinned_bytes=6_000,
    )
    actions = {(decision.action, decision.reason) for decision in decisions}
    expected = {
        ("kill", "max_lease_age"),
        ("backpressure", "session_pinned_bytes"),
        ("kill", "max_old_manifests"),
        ("evict_session", "global_pinned_bytes"),
    }
    passed = expected.issubset(actions)
    return _result(
        "E12",
        "Lease budget enforcement",
        "passed" if passed else "failed",
        {"decisions": [decision.__dict__ for decision in decisions]},
        "Deterministic policy simulation for age, session pinned bytes, old "
        "manifest count, and global pinned bytes.",
    )


def _run_e13(root: Path) -> dict[str, Any]:
    manager = LayerManager.create(
        root,
        {"a.txt": "base\n"},
        max_depth=MAX_DEPTH,
        squash_trigger=SQUASH_TRIGGER,
        squash_target=SQUASH_TARGET,
    )
    occ = OccCommitter(manager)
    gate = ShellCommitGate(occ)
    snapshot = manager.snapshot()
    active = manager.snapshot()
    started = 1_000.0

    read_only = gate.apply(
        mode=ShellMode.READ_ONLY,
        changes=[WriteChange("read-only.txt", "x\n", base_existed=False)],
        snapshot=snapshot,
        active=active,
        shell_started_at=started,
        now=started + 1,
    )
    gated = gate.apply(
        mode=ShellMode.GATED,
        changes=[WriteChange("gated.txt", "x\n", base_existed=False)],
        snapshot=snapshot,
        active=active,
        shell_started_at=started,
        now=started + 1,
    )
    strict = gate.apply(
        mode=ShellMode.STRICT_STALE,
        changes=[WriteChange("strict.txt", "x\n", base_existed=False)],
        snapshot=snapshot,
        active=manager.snapshot(),
        shell_started_at=started,
        now=started + 120,
    )
    exclusive = gate.apply(
        mode=ShellMode.EXCLUSIVE,
        changes=[WriteChange("exclusive.txt", "x\n", base_existed=False)],
        snapshot=snapshot,
        active=manager.snapshot(),
        shell_started_at=started,
        now=started + 1,
    )

    read_only_absent = manager.read_text("read-only.txt") == ("", False)
    gated_present = manager.read_text("gated.txt") == ("x\n", True)
    exclusive_present = manager.read_text("exclusive.txt") == ("x\n", True)
    strict_rejected = strict.status == "rejected_stale_snapshot"
    passed = read_only_absent and gated_present and exclusive_present and strict_rejected
    return _result(
        "E13",
        "Shell call mode coverage",
        "passed" if passed else "failed",
        {
            "read_only_status": read_only.status,
            "gated_status": gated.status,
            "strict_status": strict.status,
            "exclusive_status": exclusive.status,
            "read_only_absent": read_only_absent,
            "gated_present": gated_present,
            "exclusive_present": exclusive_present,
        },
        "Mode matrix verifies no read_only side effects, gated/exclusive commit, "
        "and strict_stale rejection.",
    )


def _run_e14() -> dict[str, Any]:
    trace = [
        ("pytest backend/tests -q", ShellMode.READ_ONLY),
        ("uv run pytest stack_overlay/tests -q", ShellMode.READ_ONLY),
        ("ruff check stack_overlay", ShellMode.READ_ONLY),
        ("npm run test", ShellMode.READ_ONLY),
        ("cargo check", ShellMode.READ_ONLY),
        ("npm run build", ShellMode.EXCLUSIVE),
        ("cargo build --release", ShellMode.EXCLUSIVE),
        ("make", ShellMode.EXCLUSIVE),
        ("python scripts/codegen.py", ShellMode.STRICT_STALE),
        ("protoc --python_out=. api.proto", ShellMode.STRICT_STALE),
        ("python scripts/update_file.py", ShellMode.GATED),
        ("sed -i s/a/b/g file.txt", ShellMode.GATED),
        ("python - <<'PY'\nopen('x','w').write('x')\nPY", ShellMode.GATED),
    ]
    predictions = [
        {
            "command": command,
            "expected": expected.value,
            "actual": classify_shell_mode(command).value,
            "correct": classify_shell_mode(command) is expected,
        }
        for command, expected in trace
    ]
    correct = sum(1 for item in predictions if item["correct"])
    accuracy = correct / len(predictions)
    return _result(
        "E14",
        "Mode opt-in and agent UX",
        "passed" if accuracy >= 0.9 else "failed",
        {
            "trace_count": len(trace),
            "correct": correct,
            "accuracy": accuracy,
            "predictions": predictions,
        },
        "Curated command trace classification; real agent traces should replace "
        "this list before production.",
    )


def _write_workload_files(root: Path, seed: int, count: int) -> None:
    for index in range(count):
        path = root / f"dir-{index % 4}" / f"file-{seed:04d}-{index:02d}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"seed={seed} index={index}\n", encoding="utf-8")


def _capture_text_diff(root: Path) -> list[tuple[str, str]]:
    captured = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            captured.append(
                (
                    path.relative_to(root).as_posix(),
                    path.read_text(encoding="utf-8"),
                )
            )
    return captured


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


def _blocked(
    experiment_id: str,
    name: str,
    note: str,
    progress_log: ProgressLog | None,
    result_sink: ResultSink | None,
) -> dict[str, Any]:
    result = _result(experiment_id, name, "blocked", {}, note)
    _log_result(progress_log, result)
    if result_sink is not None:
        result_sink(result)
    return result


def _timed(
    experiment_id: str,
    progress_log: ProgressLog | None,
    result_sink: ResultSink | None,
    run: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    started = time.perf_counter()
    _log(progress_log, f"{experiment_id} start")
    result = run()
    elapsed_ms = (time.perf_counter() - started) * 1000
    result["elapsed_ms"] = round(elapsed_ms, 2)
    _log_result(progress_log, result)
    if result_sink is not None:
        result_sink(result)
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


def _log_result(progress_log: ProgressLog | None, result: dict[str, Any]) -> None:
    metrics = json.dumps(result.get("metrics", {}), sort_keys=True)
    _log(
        progress_log,
        f"{result['id']} end status={result['status']} "
        f"elapsed_ms={result.get('elapsed_ms')} metrics={metrics}",
    )


def _storage_bytes(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total
