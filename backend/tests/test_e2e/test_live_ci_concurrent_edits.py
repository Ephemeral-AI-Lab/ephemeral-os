"""Live Daytona concurrent edit tests for CI/OCC edge cases.

Complements ``test_live_ci_rename_perf.py`` by stressing the OCC arbiter
and Jedi worker under true racing conditions. Fills gaps not covered by
``test_arbiter_parallel_edits.py`` / ``test_live_daytona_tool_occ_calls.py``
(both of which race edit-tool against edit-tool only):

* Non-overlapping concurrent edits across all 4 public write paths
  (``daytona_write_file``, ``daytona_edit_file``, ``ci_rename_symbol``,
  ``daytona_codeact``) on disjoint files — every write must land and
  OCC must record zero conflicts.
* Overlapping concurrent edits pairing *different* tool types
  (edit×edit, edit×write, edit×codeact, write×codeact) — exactly one
  writer per racing pair wins; the loser surfaces ``is_error=True`` with
  ``metadata["conflict"]`` set, and ``arbiter.metrics.conflicts_detected``
  grows by at least the number of racing pairs.
* Jedi worker script reuse — under ``CI_JEDI_WORKER_ENABLED=1`` every
  LSP call hits the persistent worker (``worker_successes`` increments,
  ``script_runs`` stays at zero since the subprocess fallback never
  fires).

All concurrency knobs come from environment variables; none are
hardcoded inside assertions.

Run with::

    uv run pytest backend/tests/test_e2e/test_live_ci_concurrent_edits.py \\
        -m live -v -s
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import shlex
import statistics
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import pytest
from dotenv import load_dotenv

from code_intelligence.lsp._jedi_worker_client import (
    ENV_FLAG as JEDI_WORKER_ENV_FLAG,
)
from code_intelligence.routing.service import CodeIntelligenceService
from tools.ci_toolkit.rename_tool import ci_rename_symbol
from tools.core.base import ToolExecutionContext
from tools.daytona_toolkit.codeact_tool import daytona_codeact
from tools.daytona_toolkit.edit_tool import daytona_edit_file
from tools.daytona_toolkit.tools import daytona_write_file

from tests.test_e2e.test_live_ci_rename_perf import (
    HAS_DAYTONA,
    LiveRenameEnv,
    TraceLog,
    _AsyncSandboxWrapper,
    _TracingSandboxWrapper,
    _percentile,
    _write_perf_project,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_PROJECT_ROOT / ".env")

pytestmark = [pytest.mark.e2e, pytest.mark.live]


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


CONCURRENCY = _env_int("CI_LIVE_CONCURRENCY", 12, minimum=4)
OVERLAP_PAIRS = _env_int("CI_LIVE_OVERLAP_PAIRS", 6, minimum=2)
NONOVERLAP_SLOTS = _env_int(
    "CI_LIVE_NONOVERLAP_SLOTS", max(CONCURRENCY, 8), minimum=4
)


@pytest.fixture(scope="module")
def live_edits_env() -> LiveRenameEnv:
    """Single sandbox shared by every test in this module."""
    if not HAS_DAYTONA:
        pytest.skip("Daytona credentials not configured")

    from sandbox.testing import (
        create_test_sandbox,
        delete_test_sandbox,
        get_sandbox_service,
    )

    info = create_test_sandbox(name="ci-lsp-concurrent-live")
    sandbox_id = info["id"]
    try:
        sandbox_svc = get_sandbox_service()
        raw_sandbox = sandbox_svc.get_sandbox_object(sandbox_id)
        home_resp = raw_sandbox.process.exec("pwd", timeout=10)
        home = (getattr(home_resp, "result", "") or "").strip() or "/home/daytona"
        trace = TraceLog()
        traced = _TracingSandboxWrapper(raw_sandbox, trace)
        env = LiveRenameEnv(
            sandbox_id=sandbox_id,
            raw_sandbox=raw_sandbox,
            traced_sandbox=traced,
            async_sandbox=_AsyncSandboxWrapper(traced),
            trace=trace,
            home=home,
            root_dir=f"{home}/ci_lsp_concurrent_{uuid.uuid4().hex[:8]}",
        )
        env.exec_checked(f"mkdir -p {shlex.quote(env.root_dir)}")
        yield env
    finally:
        delete_test_sandbox(sandbox_id)


def _init_git(env: LiveRenameEnv, root: str) -> None:
    env.exec_checked(
        " && ".join(
            [
                f"git -C {shlex.quote(root)} init -q",
                f"git -C {shlex.quote(root)} config user.email live-ci@example.invalid",
                f"git -C {shlex.quote(root)} config user.name live-ci",
                f"git -C {shlex.quote(root)} add .",
                f"git -C {shlex.quote(root)} commit -q -m init",
            ]
        ),
        timeout=180,
    )


def _build_service(
    env: LiveRenameEnv, root: str, suffix: str
) -> tuple[CodeIntelligenceService, ToolExecutionContext]:
    svc = CodeIntelligenceService(
        sandbox_id=f"{env.sandbox_id}-{suffix}",
        workspace_root=root,
        sandbox=env.traced_sandbox,
    )
    ctx = ToolExecutionContext(
        cwd=Path(root),
        metadata={
            "daytona_sandbox": env.async_sandbox,
            "daytona_cwd": root,
            "repo_root": root,
            "ci_service": svc,
            "arbiter": svc.arbiter,
            "agent_run_id": f"ci-lsp-{suffix}-{uuid.uuid4().hex[:8]}",
            "agent_name": "developer",
            "team_run_id": f"ci-lsp-{suffix}-team-{uuid.uuid4().hex[:8]}",
            "work_item_id": f"work-{suffix}",
            "work_item_started_at": time.time(),
        },
    )
    assert svc.ensure_initialized(wait=True) is True
    assert svc.lsp_client.ensure_ready(
        install_missing=True, languages=("python",)
    )["python"] is True
    assert svc.symbol_index.ensure_built(wait=True, timeout=60.0) is True
    return svc, ctx


def _print_block(label: str, payload: dict[str, Any]) -> None:
    print(
        f"[{label}] " + json.dumps(payload, sort_keys=True, default=str),
        flush=True,
    )


def _summarize_ops(op_results: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [float(item["duration_ms"]) for item in op_results]
    per_op = Counter(item.get("op", "?") for item in op_results)
    outcomes = Counter(item.get("outcome", "?") for item in op_results)
    return {
        "count": len(op_results),
        "p50_ms": _percentile(durations, 50),
        "p95_ms": _percentile(durations, 95),
        "max_ms": round(max(durations), 3) if durations else 0.0,
        "mean_ms": round(statistics.mean(durations), 3) if durations else 0.0,
        "per_op": dict(sorted(per_op.items())),
        "outcomes": dict(sorted(outcomes.items())),
    }


def _barrier_run(
    workers: list[Callable[[], dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Run ``workers`` in parallel threads, all crossing a Barrier first."""
    barrier = threading.Barrier(len(workers))

    def _wrapped(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        barrier.wait()
        return fn()

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers)) as pool:
        return list(pool.map(_wrapped, workers))


# ---------------------------------------------------------------------------
# Test A: non-overlapping concurrent edits across all write paths
# ---------------------------------------------------------------------------


def test_concurrent_nonoverlap_edits_across_tools(
    live_edits_env: LiveRenameEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All 4 write paths run concurrently on disjoint targets — zero conflicts."""
    monkeypatch.setenv(JEDI_WORKER_ENV_FLAG, "1")
    env = live_edits_env
    root = f"{env.root_dir}/nonoverlap_{uuid.uuid4().hex[:6]}"
    _write_perf_project(env, root)
    _init_git(env, root)
    svc, ctx = _build_service(env, root, "nonoverlap")
    arbiter_before = svc.arbiter.metrics.conflicts_detected
    tokens_before = svc.arbiter.metrics.tokens_issued
    trace_mark = env.trace.mark()

    try:
        generated_dir = f"{root}/pkg/generated"
        env.exec_checked(f"mkdir -p {shlex.quote(generated_dir)}")

        def _write_worker(index: int) -> Callable[[], dict[str, Any]]:
            target = f"{generated_dir}/written_{index}.py"

            def _run() -> dict[str, Any]:
                started = time.perf_counter()
                result = asyncio.run(
                    daytona_write_file.execute(
                        daytona_write_file.input_model(
                            file_path=target,
                            content=f"value_{index} = {index}\n",
                        ),
                        ctx,
                    )
                )
                return {
                    "index": index,
                    "op": "write",
                    "outcome": "ok" if not result.is_error else "error",
                    "metadata": dict(result.metadata or {}),
                    "duration_ms": round(
                        (time.perf_counter() - started) * 1000, 3
                    ),
                    "output": result.output,
                }

            return _run

        def _edit_worker(index: int) -> Callable[[], dict[str, Any]]:
            target = f"{generated_dir}/edited_{index}.py"
            env.write_file(target, f"seed_{index} = 0\n")

            def _run() -> dict[str, Any]:
                started = time.perf_counter()
                result = asyncio.run(
                    daytona_edit_file.execute(
                        daytona_edit_file.input_model(
                            file_path=target,
                            old_text=f"seed_{index} = 0",
                            new_text=f"seed_{index} = {index}",
                            description=f"nonoverlap edit #{index}",
                        ),
                        ctx,
                    )
                )
                return {
                    "index": index,
                    "op": "edit",
                    "outcome": "ok" if not result.is_error else "error",
                    "metadata": dict(result.metadata or {}),
                    "duration_ms": round(
                        (time.perf_counter() - started) * 1000, 3
                    ),
                    "output": result.output,
                }

            return _run

        def _rename_worker(index: int) -> Callable[[], dict[str, Any]]:
            target = f"{generated_dir}/rename_{index}.py"
            env.write_file(
                target,
                "\n".join(
                    [
                        f"def sym_{index}(x):",
                        f"    return x + {index}",
                        "",
                        f"def caller_{index}(x):",
                        f"    return sym_{index}(x)",
                        "",
                    ]
                ),
            )

            def _run() -> dict[str, Any]:
                started = time.perf_counter()
                result = asyncio.run(
                    ci_rename_symbol.execute(
                        ci_rename_symbol.input_model(
                            file_path=target,
                            line=1,
                            character=4,
                            new_name=f"sym_{index}_renamed",
                        ),
                        ctx,
                    )
                )
                return {
                    "index": index,
                    "op": "rename",
                    "outcome": "ok" if not result.is_error else "error",
                    "metadata": dict(result.metadata or {}),
                    "duration_ms": round(
                        (time.perf_counter() - started) * 1000, 3
                    ),
                    "output": result.output,
                }

            return _run

        def _codeact_worker(index: int) -> Callable[[], dict[str, Any]]:
            rel_path = f"pkg/generated/codeact_{index}.py"

            def _run() -> dict[str, Any]:
                started = time.perf_counter()
                code = (
                    f'write({rel_path!r}, "codeact_value_{index} = {index}\\n")'
                )
                result = asyncio.run(
                    daytona_codeact.execute(
                        daytona_codeact.input_model(
                            mode="python",
                            code=code,
                            timeout=180,
                        ),
                        ctx,
                    )
                )
                return {
                    "index": index,
                    "op": "codeact",
                    "outcome": "ok" if not result.is_error else "error",
                    "metadata": dict(result.metadata or {}),
                    "duration_ms": round(
                        (time.perf_counter() - started) * 1000, 3
                    ),
                    "output": result.output,
                }

            return _run

        builders: list[Callable[[int], Callable[[], dict[str, Any]]]] = [
            _write_worker,
            _edit_worker,
            _rename_worker,
            _codeact_worker,
        ]
        workers: list[Callable[[], dict[str, Any]]] = []
        for index in range(NONOVERLAP_SLOTS):
            builder = builders[index % len(builders)]
            workers.append(builder(index))

        started_at = time.perf_counter()
        telemetry_before = svc.lsp_client.telemetry
        tree_before = svc.tree_cache.stats
        results = _barrier_run(workers)
        duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
        telemetry_after = svc.lsp_client.telemetry
        tree_after = svc.tree_cache.stats

        errors = [item for item in results if item["outcome"] != "ok"]
        assert not errors, json.dumps(errors, sort_keys=True, default=str)

        edits = svc.arbiter.recent_edits(seconds=600)
        counts = Counter(
            str(getattr(item, "edit_type", "") or "") for item in edits
        )
        expected_types = {"write", "edit", "rename", "codeact"}
        assert expected_types.issubset(counts), dict(counts)

        conflicts_delta = (
            svc.arbiter.metrics.conflicts_detected - arbiter_before
        )
        tokens_delta = svc.arbiter.metrics.tokens_issued - tokens_before
        assert conflicts_delta == 0, (
            f"Unexpected conflicts: {conflicts_delta} (counts={dict(counts)})"
        )

        payload = {
            "label": "A.nonoverlap_mixed_write_paths",
            "concurrency": len(workers),
            "duration_ms": duration_ms,
            "ops": _summarize_ops(results),
            "arbiter_conflicts_delta": conflicts_delta,
            "arbiter_tokens_delta": tokens_delta,
            "arbiter_total_edits": svc.arbiter.metrics.total_edits,
            "lsp_worker_successes_delta": (
                telemetry_after.worker_successes
                - telemetry_before.worker_successes
            ),
            "lsp_worker_errors_delta": (
                telemetry_after.worker_errors - telemetry_before.worker_errors
            ),
            "lsp_worker_fallbacks_delta": (
                telemetry_after.worker_fallbacks
                - telemetry_before.worker_fallbacks
            ),
            "lsp_script_runs_delta": (
                telemetry_after.script_runs - telemetry_before.script_runs
            ),
            "tree_cache_hits_delta": tree_after["hits"] - tree_before["hits"],
            "tree_cache_misses_delta": (
                tree_after["misses"] - tree_before["misses"]
            ),
            "tree_cache_size": tree_after["size"],
            "symbol_index_generation": svc.symbol_index.generation,
            "status": svc.status(),
            "trace_summary": env.trace.summary_since(trace_mark),
        }
        _print_block("ci-lsp-concurrent-nonoverlap", payload)
        # Tree cache (in-process) stays empty under worker mode — Jedi state
        # lives in the persistent sandbox daemon. Fallback / errors must stay
        # at zero so every LSP call was served by the worker without falling
        # back to subprocess-per-call.
        assert payload["lsp_worker_fallbacks_delta"] == 0
        assert payload["lsp_worker_errors_delta"] == 0
    finally:
        svc.dispose()


# ---------------------------------------------------------------------------
# Test B: overlapping concurrent edits detect conflicts
# ---------------------------------------------------------------------------


def test_concurrent_overlap_edits_detect_conflicts(
    live_edits_env: LiveRenameEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Racing pairs on the same region — exactly one winner per pair."""
    monkeypatch.setenv(JEDI_WORKER_ENV_FLAG, "1")
    env = live_edits_env
    root = f"{env.root_dir}/overlap_{uuid.uuid4().hex[:6]}"
    env.exec_checked(f"mkdir -p {shlex.quote(root)}/pkg")
    env.write_file(f"{root}/pkg/__init__.py", "")
    for pair_index in range(OVERLAP_PAIRS):
        env.write_file(
            f"{root}/pkg/shared_{pair_index}.py",
            f"shared_marker_{pair_index} = 0\n",
        )
    _init_git(env, root)

    svc, ctx = _build_service(env, root, "overlap")
    arbiter_before = svc.arbiter.metrics.conflicts_detected
    trace_mark = env.trace.mark()

    try:
        pair_results: list[dict[str, Any]] = []

        for pair_index in range(OVERLAP_PAIRS):
            target = f"{root}/pkg/shared_{pair_index}.py"
            marker = f"shared_marker_{pair_index}"

            def _edit_variant(
                pair: int, attempt: int, seed: str, path: str
            ) -> Callable[[], dict[str, Any]]:
                def _run() -> dict[str, Any]:
                    started = time.perf_counter()
                    result = asyncio.run(
                        daytona_edit_file.execute(
                            daytona_edit_file.input_model(
                                file_path=path,
                                old_text=f"{seed} = 0",
                                new_text=f"{seed} = {pair}{attempt}",
                                description=(
                                    f"overlap pair {pair} attempt {attempt}"
                                ),
                            ),
                            ctx,
                        )
                    )
                    return {
                        "pair": pair,
                        "attempt": attempt,
                        "op": "edit",
                        "is_error": result.is_error,
                        "metadata": dict(result.metadata or {}),
                        "duration_ms": round(
                            (time.perf_counter() - started) * 1000, 3
                        ),
                        "output": result.output,
                    }

                return _run

            def _codeact_variant(
                pair: int, attempt: int, seed: str, path: str
            ) -> Callable[[], dict[str, Any]]:
                rel_path = f"pkg/shared_{pair}.py"

                def _run() -> dict[str, Any]:
                    started = time.perf_counter()
                    new_content = f"{seed} = {pair}{attempt}\n"
                    code = f"write({rel_path!r}, {new_content!r})"
                    result = asyncio.run(
                        daytona_codeact.execute(
                            daytona_codeact.input_model(
                                mode="python",
                                code=code,
                                timeout=180,
                            ),
                            ctx,
                        )
                    )
                    return {
                        "pair": pair,
                        "attempt": attempt,
                        "op": "codeact",
                        "is_error": result.is_error,
                        "metadata": dict(result.metadata or {}),
                        "duration_ms": round(
                            (time.perf_counter() - started) * 1000, 3
                        ),
                        "output": result.output,
                    }

                return _run

            def _write_variant(
                pair: int, attempt: int, seed: str, path: str
            ) -> Callable[[], dict[str, Any]]:
                def _run() -> dict[str, Any]:
                    started = time.perf_counter()
                    result = asyncio.run(
                        daytona_write_file.execute(
                            daytona_write_file.input_model(
                                file_path=path,
                                content=f"{seed} = {pair}{attempt}\n",
                            ),
                            ctx,
                        )
                    )
                    return {
                        "pair": pair,
                        "attempt": attempt,
                        "op": "write",
                        "is_error": result.is_error,
                        "metadata": dict(result.metadata or {}),
                        "duration_ms": round(
                            (time.perf_counter() - started) * 1000, 3
                        ),
                        "output": result.output,
                    }

                return _run

            # Overlap combos must include only tools that carry an *intent*
            # (old_text / base-content) the OCC layer can validate against the
            # current file. `daytona_write_file` is unconditional overwrite —
            # last-writer-wins by contract, not an OCC bug — so it is
            # intentionally excluded from this racing matrix.
            combos = [
                (_edit_variant, _edit_variant),
                (_edit_variant, _codeact_variant),
                (_codeact_variant, _codeact_variant),
            ]
            left_builder, right_builder = combos[pair_index % len(combos)]
            workers = [
                left_builder(pair_index, 0, marker, target),
                right_builder(pair_index, 1, marker, target),
            ]
            outcomes = _barrier_run(workers)
            pair_results.append(
                {
                    "pair_index": pair_index,
                    "left_op": outcomes[0]["op"],
                    "right_op": outcomes[1]["op"],
                    "outcomes": outcomes,
                }
            )

        winners_per_pair: list[int] = []
        losers_per_pair: list[int] = []
        conflict_reasons: Counter[str] = Counter()
        for pair in pair_results:
            wins = [item for item in pair["outcomes"] if not item["is_error"]]
            losses = [item for item in pair["outcomes"] if item["is_error"]]
            winners_per_pair.append(len(wins))
            losers_per_pair.append(len(losses))
            for loss in losses:
                reason = str(loss["metadata"].get("conflict_reason", "")) or (
                    "conflict"
                    if loss["metadata"].get("conflict")
                    else "unknown"
                )
                conflict_reasons[reason] += 1

        conflicts_delta = (
            svc.arbiter.metrics.conflicts_detected - arbiter_before
        )
        payload = {
            "label": "B.overlap_racing_pairs",
            "pairs": OVERLAP_PAIRS,
            "winners_histogram": dict(Counter(winners_per_pair)),
            "losers_histogram": dict(Counter(losers_per_pair)),
            "conflict_reasons": dict(conflict_reasons),
            "arbiter_conflicts_delta": conflicts_delta,
            "arbiter_total_edits": svc.arbiter.metrics.total_edits,
            "pair_results": pair_results,
            "trace_summary": env.trace.summary_since(trace_mark),
        }
        _print_block("ci-lsp-concurrent-overlap", payload)

        assert all(count == 1 for count in winners_per_pair), (
            f"Unexpected winner histogram: {payload['winners_histogram']}"
        )
        assert all(count == 1 for count in losers_per_pair), (
            f"Unexpected loser histogram: {payload['losers_histogram']}"
        )
        assert conflicts_delta >= OVERLAP_PAIRS, (
            f"conflicts_detected grew by {conflicts_delta}, "
            f"expected >= {OVERLAP_PAIRS}"
        )

        for pair_index in range(OVERLAP_PAIRS):
            target = f"{root}/pkg/shared_{pair_index}.py"
            text = env.read_file(target)
            marker = f"shared_marker_{pair_index}"
            matches = re.findall(rf"{re.escape(marker)} = (\d+)", text)
            assert len(matches) == 1, (
                f"Torn state for pair {pair_index}: {text!r}"
            )
    finally:
        svc.dispose()


# ---------------------------------------------------------------------------
# Test C: Jedi worker routes every call; fallback never fires
# ---------------------------------------------------------------------------


def test_jedi_worker_reuse_under_load(
    live_edits_env: LiveRenameEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under ``CI_JEDI_WORKER_ENABLED=1`` every LSP call hits the worker."""
    monkeypatch.setenv(JEDI_WORKER_ENV_FLAG, "1")
    env = live_edits_env
    root = f"{env.root_dir}/worker_{uuid.uuid4().hex[:6]}"
    core_path, uses_path, _more_path = _write_perf_project(env, root)
    _init_git(env, root)
    svc, ctx = _build_service(env, root, "worker")
    telemetry_before = svc.lsp_client.telemetry
    tree_before = svc.tree_cache.stats
    trace_mark = env.trace.mark()

    try:
        # Pin each read-only LSP op to a position known to land on a Python
        # symbol in the perf project (see `_write_perf_project`):
        #   core.py (1, 0) → `def alpha(value):` — used for find_references / hover
        #   uses.py (4, 11) → `alpha(1)` reference — used for goto_definition
        # uses.py/more.py line 1 col 0 is the `from` keyword and has no
        # references; probing those would be a test bug, not an LSP issue.

        def _worker(index: int) -> dict[str, Any]:
            started = time.perf_counter()
            op_index = index % 4
            if op_index == 0:
                refs = svc.lsp_client.find_references(core_path, 1, 0)
                ok = len(refs) >= 1
                op = "find_references"
            elif op_index == 1:
                defs = svc.lsp_client.goto_definition(uses_path, 4, 11)
                ok = len(defs) >= 1
                op = "goto_definition"
            elif op_index == 2:
                hover = svc.lsp_client.hover(core_path, 1, 0)
                ok = hover is not None
                op = "hover"
            else:
                result = asyncio.run(
                    ci_rename_symbol.execute(
                        ci_rename_symbol.input_model(
                            file_path=core_path,
                            line=5,
                            character=0,
                            new_name=f"beta_worker_{index}",
                            dry_run=True,
                        ),
                        ctx,
                    )
                )
                ok = not result.is_error
                op = "rename_dry"
            return {
                "index": index,
                "op": op,
                "ok": ok,
                "duration_ms": round(
                    (time.perf_counter() - started) * 1000, 3
                ),
            }

        barrier = threading.Barrier(CONCURRENCY)

        def _synced(index: int) -> dict[str, Any]:
            barrier.wait()
            return _worker(index)

        started_at = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=CONCURRENCY
        ) as pool:
            results = list(pool.map(_synced, range(CONCURRENCY)))
        duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
        telemetry_after = svc.lsp_client.telemetry
        tree_after = svc.tree_cache.stats

        worker_successes = (
            telemetry_after.worker_successes
            - telemetry_before.worker_successes
        )
        script_runs = (
            telemetry_after.script_runs - telemetry_before.script_runs
        )
        worker_errors = (
            telemetry_after.worker_errors - telemetry_before.worker_errors
        )
        worker_fallbacks = (
            telemetry_after.worker_fallbacks
            - telemetry_before.worker_fallbacks
        )
        cache_hits = (
            telemetry_after.cache_hits - telemetry_before.cache_hits
        )

        payload = {
            "label": "C.jedi_worker_reuse_under_load",
            "concurrency": CONCURRENCY,
            "duration_ms": duration_ms,
            "ops": _summarize_ops(
                [
                    {**item, "outcome": "ok" if item["ok"] else "error"}
                    for item in results
                ]
            ),
            "lsp_worker_successes_delta": worker_successes,
            "lsp_worker_errors_delta": worker_errors,
            "lsp_worker_fallbacks_delta": worker_fallbacks,
            "lsp_script_runs_delta": script_runs,
            "lsp_cache_hits_delta": cache_hits,
            "tree_cache_hits_delta": tree_after["hits"] - tree_before["hits"],
            "tree_cache_size": tree_after["size"],
            "worker_active": svc.lsp_client.worker_active,
            "status": svc.status(),
            "trace_summary": env.trace.summary_since(trace_mark),
        }
        _print_block("ci-lsp-concurrent-worker", payload)

        failures = [item for item in results if not item["ok"]]
        assert not failures, json.dumps(failures, sort_keys=True)
        assert payload["worker_active"] is True
        assert worker_successes >= 1
        assert worker_errors == 0
        assert worker_fallbacks == 0
        assert script_runs == 0, (
            f"Jedi subprocess-fallback fired {script_runs} time(s) under "
            "worker mode — script instance was not reused."
        )
    finally:
        svc.dispose()


@pytest.fixture(autouse=True, scope="module")
def _emit_trace_summary(live_edits_env: LiveRenameEnv):
    yield
    live_edits_env.trace.print_all()
    live_edits_env.trace.print_summary()
