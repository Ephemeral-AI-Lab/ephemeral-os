# Progressive Live-Test Tiers — Design

**Date:** 2026-05-08
**Status:** proposal
**Driver:** Phase 3 (`shell-large-capture-phase3-implementation-report-20260508.md`) ran into 4 separate cases of "test halts for too long":
1. Daytona sandbox provisioning timed out (300 s) on a stalled runner queue.
2. Daytona sandbox stuck in `state='starting'` indefinitely (state-machine bug).
3. A 16-cell phase07 size matrix in one pytest invocation only reports PASS/FAIL at the end — a bad cell in position 12 wastes the runtime of cells 1–11.
4. A 4-step concurrency batch (c=1, 5, 10, 20) burns 25 min before reporting a single number.

In every case the **fast-failure signal arrived too late**. This document specifies a tiered test runner that surfaces breakage at the earliest possible moment.

---

## 1 Goals

- **Cheap probes run first.** A Daytona-side stall must be visible in ≤ 30 s, not 5 min.
- **Per-cell streaming.** Every matrix cell writes its JSONL row before the next cell starts. A kill-9 mid-run preserves all completed cells.
- **Per-tier time budgets.** Each tier has a hard wall-clock budget; exceeding it aborts the tier and proceeds to the next.
- **Independent invocability.** Any tier can be run on its own; later tiers should not assume earlier tiers ran.
- **Resume-on-restart.** Re-running a partially-complete suite skips cells already in the artifact (idempotent at cell granularity).
- **Infra-fault escape hatch.** When Daytona stalls (state-machine bugs documented in §6), a single bash one-liner unblocks the suite.

## 2 Non-goals

- This is not a rewrite of the live-e2e harness. The existing `SandboxHandle`, `tool.shell`, and `gather_with_barrier` stay.
- This is not a CI orchestration plan. CI integration is a follow-up; the local-developer experience comes first.
- This does not replace pytest; it wraps it.

---

## 3 Tier Structure

| Tier | Purpose | Per-tier wall budget | Per-cell wall budget | Abort cascades to |
|---:|---|---:|---:|---|
| 0 | **Pre-flight.** Daytona stack health (`/api/health`), runner-queue poll (no stuck `starting` sandboxes), test-fixture image exists. | 30 s | n/a | abort everything |
| 1 | **Hot path smoke.** One `tool.shell("true")`, one `tool.shell` writing a single file under `tracked/`, one under `dist/`. Validates daemon socket bind, capture pipeline, OCC routing, both merge paths, /dev/shm cleanup. | 60 s | 15 s | abort 2+ |
| 2 | **K-scaling spot check.** phase06 K-scaling at exactly two cells: `tracked × K=1000` and `dist × K=1000` (each is one shell call). Catches gross perf regressions in 30 s. | 120 s | 60 s | warn 3+ |
| 3 | **Single-axis matrices.** phase07 size matrix, kind matrix, mixed-routing matrix. Each cell is a discrete row in the artifact; kill-9 mid-run preserves prior cells. | 600 s | 60 s | warn 4+ |
| 4 | **Cross-axis matrices.** phase09 size×kind, size×concurrency (c=1/5/10/20), kind×concurrency. Each `(axis_a, axis_b)` cell is independent and resumable. | 1500 s | 90 s | abort 5 |
| 5 | **Soak.** phase08 dev_shm 200-call regression + (Phase 3.5) RSS-and-cache soak 500-call. | 900 s | n/a | abort 6 |
| 6 | **Adversarial + injection.** phase09 §4A.4 + §4A.6. Single explicit assert per cell. | 600 s | 60 s | none (last) |

**Order of execution:** 0 → 1 → 2 → 3 → 4 → 5 → 6. A tier that exceeds its wall budget aborts cleanly (writes a `tier_aborted` summary row), and the next tier still runs unless this tier's failure cascades.

**Cascade rules** (right column above):
- *abort everything*: Tier 0 fail → no other tiers run. Daytona is broken; nothing downstream is meaningful.
- *abort N+*: Tier failure aborts all tiers ≥ N+1 except those marked "warn". This isolates infra cascades from correctness cascades.
- *warn*: failure logs a warning but the runner proceeds. Used when a tier's failure mode is independent of later tiers.

---

## 4 Per-cell streaming contract

**The current bug:** the existing matrices write their artifact at `with artifact.open("w"): for row in rows: ...` *after* the loop. A kill-9 anywhere in the loop loses everything.

**The fix:** open the artifact in append mode at tier start, flush after every cell:

```python
artifact = _artifact_path(label)
with artifact.open("a", encoding="utf-8") as fh:
    for cell in cells:
        row = await _run_cell(cell)
        fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
```

The tier's summary row is written last; its absence on read is the resume signal.

---

## 5 Resume-on-restart

The runner re-reads any pre-existing JSONL artifact for the tier and skips cells whose `cell_id` already appears with `passed: true`. Failed cells are retried (not skipped) — a bad cell on the previous run might be transient.

```python
def _completed_cells(artifact: Path) -> set[str]:
    if not artifact.exists():
        return set()
    completed: set[str] = set()
    with artifact.open() as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("passed") is True and row.get("cell_id"):
                completed.add(row["cell_id"])
    return completed
```

This makes a second invocation of the same tier *strictly cheaper* than the first.

---

## 6 Daytona escape hatch (state-machine bug workaround)

Phase 3 ran into Daytona's `SandboxStartAction.updateSandboxState` logging `"sandbox X is not in a pending state"` in a 10-second loop. The state machine expects `pending → starting → started`, but a row stuck at `state='starting'` triggers the loop. Standard `DELETE /api/sandbox/{id}?force=true` returns 500 because the controller refuses destructive ops on a transitioning row.

**Tier 0 includes** an explicit health probe and, when stuck rows are found, an automatic recovery:

```bash
# Detect stuck "starting" rows in the daytona DB.
STUCK=$(docker exec daytona-db-1 psql -U user -d daytona -t -A \
    -c "SELECT id FROM sandbox WHERE state='starting' \
        AND \"updatedAt\" < NOW() - INTERVAL '60 seconds'")

# Force-destroy: bypass the API state machine; reconciler picks up next tick.
if [ -n "$STUCK" ]; then
    docker exec daytona-db-1 psql -U user -d daytona \
        -c "UPDATE sandbox SET state='destroyed', \"desiredState\"='destroyed' \
            WHERE state='starting' AND \"updatedAt\" < NOW() - INTERVAL '60 seconds'"
fi
```

The DB UPDATE bypasses the broken state-machine transition the API enforces. After the UPDATE the reconciler stops looping and the suite can proceed.

**This is documented as the workaround, not the fix.** A real fix lives in Daytona itself — out of scope for this repo, but Tier 0's auto-recovery means the local test suite is no longer blocked on it.

---

## 7 Per-tier wall budget enforcement

Each tier wraps its pytest invocation in a wall-clock timer. On budget exceeded:
1. SIGINT pytest (graceful — finishes the current cell's row write).
2. Wait 30 s for graceful exit.
3. SIGKILL if still alive.
4. Append a `tier_aborted_wall_budget` summary row with elapsed_s and last_cell_id.
5. Move to the next tier (subject to cascade rules).

Implementation: a small wrapper script in `backend/tests/live_e2e_test/_tools/run_tiered.py` (~150 LOC). Invoked as `python -m backend.tests.live_e2e_test._tools.run_tiered --tier 0,1,2`. The wrapper enforces budgets via `asyncio.wait_for`, parses pytest's exit code, aggregates the tier-summary JSONL into a single `progressive-test-summary-{run_id}.jsonl`, and exits non-zero if any cascading tier failed.

---

## 8 Test-suite refactor required

The existing test files are mostly tier-3 today. To enable tier-1/2/4 separation, **split**:

| Existing file | Tier today | New tier(s) |
|---|---:|---|
| `test_phase06_large_capture_scaling.py` | 3 | 2 (K=1000 spot check) + 4 (full K-scaling) |
| `test_phase07_complex_capture_metrics.py::test_phase07_size_matrix` | 3 | 3 (no change) |
| `test_phase07_*::test_phase07_kind_matrix` | 3 | 3 (no change) |
| `test_phase07_*::test_phase07_mixed_routing_matrix` | 3 | 3 (no change) |
| `test_phase08_dev_shm_bounded.py` | 5 | 5 (no change) |
| `test_phase09_complex_e2e.py::test_phase09_size_x_kind` | 4 | 4 (no change) |
| `test_phase09_complex_e2e.py::test_phase09_adversarial` | 6 | 6 (no change) |
| **NEW: `test_phase00_smoke.py`** | n/a | 1 |
| **NEW: `test_phase09_size_x_concurrency.py`** | n/a | 4 |
| **NEW: `test_phase09_kind_x_concurrency.py`** | n/a | 4 |

Each "new" file follows the per-cell streaming + resume contract from §§4–5.

Total new LOC: ~250 across three test files + ~150 for the runner wrapper + Tier-0 health probe ≈ **~400 LOC** of test-infra. Production code unchanged.

---

## 9 What this would have changed for Phase 3

Re-running the Phase 3 session under this design:
- **Daytona stall** at 22:25 UTC: Tier 0 catches stuck `state='starting'` row in 30 s and auto-recovers. Total saved: ~10 min of confusion.
- **Phase 07 size matrix v1** at 22:39 UTC: 16 cells streaming row-by-row. The 1 MiB × 8 falsifier cell would have its row in the artifact at minute 1, not minute 6.
- **Phase 09 adversarial first run** at 22:59 UTC: the two failed symlink cells are in the artifact at minute 1 of 1.5; we don't waste time on the other 5 before discovering the routing fix.
- **k=1000 concurrency** (the run that prompted this doc): the c=1 batch lands its row in ~30 s. If Daytona stalls before c=5, the c=1 baseline is preserved and the cascade rule (Tier 4 abort doesn't propagate to Tier 5–6) keeps the soak and adversarial runs going.

---

## 10 Implementation phases

| Phase | LOC | What lands | When |
|---:|---:|---|---|
| **A** | ~80 | `test_phase00_smoke.py` (Tier 1) + Tier 0 health probe + Daytona DB escape hatch as a standalone script `backend/tests/live_e2e_test/_tools/daytona_probe.sh` | Phase 3.5 |
| **B** | ~100 | Per-cell streaming refactor on the existing phase07 + phase09 matrices (in-place; no API change) | Phase 3.5 |
| **C** | ~150 | `run_tiered.py` wrapper + tier configuration in `backend/tests/live_e2e_test/_tools/tiers.toml` | Phase 3.5 |
| **D** | ~150 | New cross-axis matrices (`test_phase09_size_x_concurrency.py`, `test_phase09_kind_x_concurrency.py`) using the streaming + resume contract | Phase 3.5 |

Phase A is the highest-priority — it converts every future "Daytona is stuck" incident from a 10-minute confusion into a 30-second autodiagnosis.

---

## 11 What we explicitly are NOT doing here

- **Not** running tests in parallel within a tier. Tests inside a tier share one Daytona sandbox (per-test fixture); `pytest-xdist` is out of scope.
- **Not** replacing pytest. The wrapper invokes pytest; cells stay as pytest functions.
- **Not** a flaky-test quarantine. That's a separate concern; the resume contract makes flake-rerun cheap, but the runner doesn't auto-retry.
- **Not** a CI plan. Local developer experience first; CI integration follows once the local invariants hold.
