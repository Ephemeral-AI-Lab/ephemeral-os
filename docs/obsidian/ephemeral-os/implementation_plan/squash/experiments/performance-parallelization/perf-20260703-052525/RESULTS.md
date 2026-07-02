# Squash performance attribution — RESULTS

Run id: `perf-20260703-052525`
Benchmark: `LOAD-COMBO-HTTP` (live Docker, `ubuntu:24.04`).
Environment: sandbox container = **4 CPUs** (`nproc`), host = 14. Daemon binary
built from `main` at experiment start (no production changes for the baseline).

## TL;DR

The post-commit **remount sweep is 90–94 % of squash op time**; plan + build +
commit (block flatten) is 6–10 %. The sweep cost is `M_migrated × ~7.3 ms`,
executed **strictly serially** (serial loop *and* one process-wide state mutex
held across every per-session transaction). Identity sessions (no replaced
layer) are already ~0.03 ms each — effectively free. Therefore:

- Parallelizing block flatten (handoff step 3) **cannot** help this case.
- Pre-filtering identity sessions (handoff step 2) **saves nothing** (measured).
- The fix is to **parallelize the migrated remount path off the global lock**
  and **batch the per-session `persist_handles` into one write**.

## Method (reproducible, zero production-code change for baseline)

The daemon already emits, per squash invocation, one `layerstack.squash` parent
span (whole daemon-side op) and one `workspace_session.remount` child span per
swept session, each carrying `dur_ms` + a `disposition` attr
(`Identity`/`Migrated`/`Leased`/`Faulty`), plus a `namespace.exec.remount_overlay`
child for the subprocess. We harvest the daemon `observability.ndjson`
(`/eos/runtime/daemon/observability/observability.ndjson`) out of the container
**before teardown removes it**, then bucket spans by trace.

- Harvest hook: `SQUASH_HARVEST_OBS=1` gate added to the E2E teardown
  (`conftest.py` → `helpers.harvest_observability`, `docker cp`). Test-infra
  only; no `src/`/`crates/` change; no effect on normal runs.
- Analysis: `scripts/analyze_spans.py` (groups by trace, sums remount `dur_ms`,
  splits by disposition, computes `non_sweep = parent − sweep − faulty_destroy`).
- Driver: `scripts/run_combo.sh <label> <sessions>`.
- Raw evidence: `logs/attribution-baseline-s50.txt|json`,
  `logs/attribution-baseline-s200.txt`, `logs/pytest-*.log`.

## Baseline — 50 sessions (complete span capture)

`scripts/run_combo.sh baseline-s50 50` — PASS, T_http_disconnect = 20.48 ms.

| inv | blocks | swept | squash op (parent) | remount sweep | sweep % | non-sweep (open+plan+build+commit) | migrated | identity |
|----:|-------:|------:|-------------------:|--------------:|--------:|-----------------------------------:|---------|----------|
| 1 | 1 | 21 | 163 ms | 146 ms | **89.6 %** | 17 ms | 21 × ~7 ms | 0 |
| 2 | 1 | 38 | 158 ms | 148 ms | **93.7 %** | 10 ms | 21 × 7.0 ms | 17 × 0.06 ms |
| 3 | 1 | 54 | 176 ms | 163 ms | **92.6 %** | 13 ms | 20 × 8.1 ms | 34 × 0.03 ms |
| 4 (cleanup) | 1 | 0 | 11 ms | 0 | — | 11 ms | 0 | 0 |

Harness wall (incl. CLI + gateway + round trip): T_squash = 202.959 ms.

### Per-migrated-session cost (62 migrations across the run)

| component | span | n | mean | min | median | max | share |
|---|---|--:|--:|--:|--:|--:|--:|
| whole migrated remount | `workspace_session.remount` | 62 | 7.34 ms | 4 | 7.5 | 12 | 100 % |
| subprocess runner | `namespace.exec.remount_overlay` | 62 | 2.37 ms | 1 | 2 | 4 | **32 %** |
| residual (quiesce + apply + persist + release) | — | 62 | 4.97 ms | 3 | 5 | 9 | **68 %** |

The subprocess (`&self`, parallelizable) is only a third of the cost; the
residual two-thirds is quiesce + `persist_handles` + lease release. Any fix that
parallelizes only the subprocess is capped at a ~1.5× speedup.

## Baseline — 200 sessions (harness complete; span capture partial)

`scripts/run_combo.sh baseline-s200 200` — PASS, T_http_disconnect = 21.475 ms,
T_e2e = 48.6 s.

| inv | harness wall |
|----:|-------------:|
| 1 | 648.308 ms |
| 2 | **2141.280 ms** |
| 3 | 802.918 ms |
| 4 (cleanup) | 35.442 ms |

Consistent with the original handoff report (645–913 ms typical). The 2141 ms
outlier is a **serial-sweep tail-latency** effect: a single session hitting the
500 ms quiesce freeze budget stalls the *entire* remaining serial sweep — a
failure mode a parallel sweep isolates to one worker.

Span capture at 200 sessions is partial: the daemon rotates
`observability.ndjson` at the 8 MB default, and the run emitted ~31 k periodic
resource **sample** records (200 workspaces × ticks) that evicted the earlier
spans (only one rotated generation is kept). The captured invocation 3 span tree
(parent 775 ms, 204 remount children) matches the harness number. For the
final before/after we will raise `observability.max_file_bytes` for complete
200-session span capture; the per-session cost model above (from the complete
50-session run) already generalizes because per-session work is independent of N.

## Complexity of the current algorithm

Let `N` = live sessions, `M` = migrated sessions (`M ≤ N`), `t̄ ≈ 7.3 ms` the
per-migration cost, `t_id ≈ 0.03 ms` the identity cost.

- **Time (wall):** `T_sweep = M·t̄ + (N−M)·t_id`, fully serial — serialized twice
  (the `.iter().map(remount_session)` loop *and* the single
  `Mutex<WorkspaceRuntimeState>` held across each transaction). Θ(M) with a large
  constant.
- **`persist_handles` amplification:** each migrated session rewrites the *whole*
  handle set and fsyncs twice ⇒ `Θ(M·N)` serialized bytes + `2M` fsyncs.
- **Tail:** one freeze straggler adds up to the full 500 ms budget to the whole
  sweep (serial), not to one session.
- **Space:** `O(1)` — one handle clone + one frozen-task set live at a time.
- **Lock hold:** global state mutex held `Θ(T_sweep)`, blocking every other
  workspace-runtime operation for the whole sweep.

## Conclusions carried into the design

1. Optimize the **remount sweep**, nothing else (block flatten is 6–10 %).
2. Parallelize the **whole** migrated execute phase (quiesce + subprocess), not
   just the subprocess — the residual is 68 %.
3. Break the process-wide state mutex's whole-transaction hold; the expensive
   middle touches only a *cloned* handle + `&NamespaceRuntime` (`&self`) + a
   per-thread `LayerStack`, so it needs no `&mut` on shared state.
4. Batch `persist_handles` to **one** write per sweep (kills the `Θ(M·N)` +
   `2M`-fsync amplification).
5. Do **not** pre-filter identity sessions — they are already free.
6. Keep peak memory `O(W)` (bounded concurrency width), never `O(N)`: the
   speedup comes from overlapping blocking waits, not from buffering in RAM.
