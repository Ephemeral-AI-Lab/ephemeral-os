# Phase 3 Integrated P0 Implementation Report

Date: 2026-05-05

## Scope

Implemented Phase 3 of
`backend/tests/live_e2e_test/sandbox/IMPLEMENTATION_PLAN.md`.

The integrated `layer_stack_overlay_occ/` skip stubs now exercise the public
sandbox tool path end-to-end:

- `sandbox.api.tool.write_file`
- `sandbox.api.tool.edit_file`
- `sandbox.api.tool.read_file`
- `sandbox.api.tool.shell`

Raw exec is used only for test harness duties outside the captured workspace:
resetting/polling `/tmp` side-channel files and injecting one stale staging
directory for recovery validation.

Files changed:

- `_harness/integrated_cases.py`
- `_harness/sandbox_fixture.py`
- `layer_stack_overlay_occ/test_shell_call_isolation.py`
- `layer_stack_overlay_occ/test_concurrent_agents.py`
- `layer_stack_overlay_occ/test_concurrency_scaling.py`
- `layer_stack_overlay_occ/test_codegen_race.py`
- `layer_stack_overlay_occ/test_failure_recovery.py`

## Verification

Environment:

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1
```

Commands run:

```bash
uv run python -m py_compile \
  backend/tests/live_e2e_test/sandbox/_harness/integrated_cases.py \
  backend/tests/live_e2e_test/sandbox/_harness/sandbox_fixture.py \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_shell_call_isolation.py \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_concurrent_agents.py \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_codegen_race.py \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_failure_recovery.py

.venv/bin/pytest --collect-only \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ -q

uv run ruff check \
  backend/tests/live_e2e_test/sandbox/_harness/integrated_cases.py \
  backend/tests/live_e2e_test/sandbox/_harness/sandbox_fixture.py \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ

EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  .venv/bin/pytest \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ \
  -v -rs -s --tb=short
```

Results:

| Check | Result |
|---|---:|
| py_compile | passed |
| integrated collect-only | 18 collected |
| targeted ruff | passed |
| failing-subset retry after directory root cause fix | 4 passed, 77.55 s |
| Phase 3 live gate | 14 passed, 4 skipped, 151.73 s |

The 4 skips are the Phase 4/5 load-profile stubs in
`test_load_profiles.py`: smoke, sustained, burst, and soak.

## Integrated Load-Shaped Metrics

Phase 3 does not claim the Phase 4 profile budgets. These metrics are from the
P0 integrated load-shaped correctness probes that now run in the live gate.

| Probe | Workload | Successes | Rejects | p50 | p99 | Max | Correctness |
|---|---:|---:|---:|---:|---:|---:|---|
| mixed shell/edit sample | 8 shell + 16 edit calls | 24 | 0 | 3238.213 ms | 4065.934 ms | 4081.096 ms | 24/24 accepted paths visible, drift 0 |
| accepted visible sample | 3 shell + 5 edit calls | 8 | 0 | 1376.228 ms | 1611.552 ms | 1616.563 ms | 8/8 accepted paths visible |
| 50 percent gitignored overlap | 8 `process.exec` writers, 4 paths | 8 | 0 | 3087.904 ms | 3091.122 ms | 3091.147 ms | LWW final values valid for every path |
| stale rejected shell | API write + stale shell | 1 | 1 | 1843.973 ms | 2725.726 ms | 2743.721 ms | rejected shell left no trace |

Observed integrated shell dispatch sits in the 1.8-4.5 s range in this live
Daytona run. That includes host-to-provider exec dispatch and runtime-server
startup, not only in-sandbox OCC/overlay work. Phase 4 remains the right place
to add JSONL profile artifacts and enforce p99 budgets.

The original full live gate used shell-overlay writers for the gitignored
overlap row. That measurement is now superseded: the overlap/LWW provider case
uses raw `process.exec`, while shell-overlay gitignored behavior remains covered
by the codegen race probes.

### Per-Call Timing JSONL

Follow-up focused timing run:

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
EPHEMERALOS_LIVE_E2E_TIMING_JSONL=.omc/results/live-e2e-phase3-per-call-timings-focused-20260505T160607Z.jsonl \
  .venv/bin/pytest \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_concurrent_agents.py::test_sustained_mixed_shell_edit_sample_has_no_torn_reads \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_concurrent_agents.py::test_overlapping_50pct_gitignored_paths_use_lww \
  -v -s --tb=short
```

Result: `2 passed, 1 warning in 59.08 s`.

Artifact:

```text
.omc/results/live-e2e-phase3-per-call-timings-focused-20260505T160607Z.jsonl
```

Rows: 32 total, 24 mixed shell/edit calls and 8 historical
gitignored-overlap shell calls. The gitignored-overlap shell rows are retained
only as a diagnostic artifact; the current overlap test uses `process.exec` and
is reported below.

Each JSONL row includes:

- `wall_ms`
- `api.shell.dispatch_total_s`
- `api.shell.total_s`
- `api.shell.overlay_s`
- `api.shell.occ_apply_s`
- `overlay.mount.materialize_lower_s`
- `overlay.mount.copy_lower_to_merged_s`
- `overlay.run_command_s`
- `overlay.capture_changes_s`
- `occ.prepare.*`
- `occ.commit.*`
- `layer_stack.transaction.lock_wait_s`
- `layer_stack.transaction.lock_held_s`
- full `timings` object for any additional runtime keys

Focused timing breakdown, displayed in milliseconds:

| Group | Calls | Wall p50 | Wall p99 | Dispatch p99 | Runtime API p99 | Overlay p99 | OCC apply p99 | OCC prepare p99 | OCC commit p99 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| mixed shell/edit | 24 | 3208.356 | 4056.649 | 4067.856 | 1809.656 | 716.501 | 1275.166 | 223.981 | 10.926 |
| mixed shell only | 8 | 3819.747 | 4067.869 | 4067.856 | 1809.656 | 716.501 | 1275.166 | 64.811 | 4.683 |
| mixed edit only | 16 | 2892.952 | 3481.256 | n/a | n/a | n/a | n/a | 228.521 | 11.027 |
| gitignored overlap, old shell probe | 8 | 4369.565 | 4609.251 | 4609.232 | 3517.899 | 2994.058 | 524.690 | 79.292 | 2.861 |

Corrected gitignored-overlap `process.exec` run:

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
EPHEMERALOS_LIVE_E2E_TIMING_JSONL=.omc/results/live-e2e-phase3-gitignored-overlap-process-exec-20260505T162158Z.jsonl \
  .venv/bin/pytest \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_concurrent_agents.py::test_overlapping_50pct_gitignored_paths_use_lww \
  -v -s --tb=short
```

Result: `1 passed, 1 warning in 9.55 s`.

Artifact:

```text
.omc/results/live-e2e-phase3-gitignored-overlap-process-exec-20260505T162158Z.jsonl
```

Rows: 8 `process_exec` calls.

| Group | Calls | Wall p50 | Wall p99 | Max | Shell/overlay/OCC timings |
|---|---:|---:|---:|---:|---|
| gitignored overlap, process.exec | 8 | 3087.905 ms | 3091.122 ms | 3091.147 ms | n/a |

Key interpretation:

- The old shell-based gitignored-overlap row was dominated by
  `overlay.run_command_s` at ~2984 ms p99 because the test started 8 overlay
  shells, then the host waited for 8 `/tmp` side-channel markers before
  releasing them. That coordination wait lived inside the shell command, so it
  was counted as shell overlay runtime. The corrected test uses raw
  `process.exec`; the JSONL rows have `op="process_exec"` and null
  shell/overlay/OCC timing fields.
- The corrected `process.exec` overlap wall p99 is still ~3091 ms because the
  provider commands intentionally wait for all 8 processes to announce
  readiness before the host releases them. That is provider-exec launch plus
  test coordination, not overlay/OCC work.
- The mixed-shell p99 is dominated by host dispatch plus runtime shell work, not
  by layer-stack commit. `occ.commit.total_s` p99 is ~4.7 ms for mixed shells.
- Mixed edits are slow in wall time because they launch alongside 8 shell
  runtime calls. Their in-sandbox OCC commit p99 is ~11 ms, while
  `occ.prepare.total_s` reaches ~229 ms in the tail.
- Layer-stack transaction waits were negligible in the mixed run: lock wait p99
  stayed around 0.006 ms.

## Shell Isolation Metrics

| Probe | Workload | Successes | Rejects | p50 | p99 | Max | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| repeated snapshot reads | shell 100 reads + concurrent API write | 2 | 0 | 2506.917 ms | 4031.722 ms | 4062.840 ms | 100 paired reads, drift 0 |
| pre-edit leased view | shell read + concurrent API edit | 2 | 0 | 1794.366 ms | 2605.234 ms | 2621.782 ms | shell saw old content; final view saw edit |
| overlapping shell writers | 2 shell writers, same tracked path | 1 | 1 | 1820.465 ms | 1850.171 ms | 1850.777 ms | first commit won; loser rejected with `content changed` |

## Codegen Race Metrics

| Probe | Workload | Successes | Rejects | p50 | p99 | Max | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| tracked generated file | 2 shell writers, same tracked path | 1 | 1 | 1912.912 ms | 1946.041 ms | 1946.717 ms | stale writer rejected with `content changed` |
| gitignored artifact | 2 shell writers, same `dist/` path | 2 | 0 | 1801.209 ms | 1830.784 ms | 1831.388 ms | both accepted; final value followed LWW |
| mixed tracked + gitignored shell | winning API write + stale mixed shell | 1 | 1 | 1870.838 ms | 2751.975 ms | 2769.957 ms | tracked conflict rejected whole shell capture; gitignored side dropped |

The mixed shell result intentionally follows the clarified contract: a
shell-captured tracked conflict rejects the whole shell request layer. The
gitignored file from that same shell is not partially published.

## Failure Recovery Metrics

| Probe | Scenario | Result |
|---|---|---|
| publish conflict recovery | stale shell loses to API write | final file remains `winner`; active leases 0; staging dirs 0; no orphan layers/staging removed |
| squash checkpoint cleanup | 6-layer stack plus injected stale staging dir | compacted depth 6 -> 2; staging dirs 1 -> 0; injected orphan staging removed |
| timeout lease cleanup | shell times out before writing | late write absent; active leases 0; staging dirs 0; compact clean |

## Complex And Edge Case Handling

Covered by Phase 3:

- Shell snapshot isolation: a shell that leased before an API write continued
  to read the old snapshot for 100 repeated reads while the final public read
  saw the API write.
- Tracked-path conflict handling: two stale shell writers on the same tracked
  file produced exactly one accepted write and one rejected write.
- Mixed shell/edit concurrency: 8 shell calls and 16 edit calls completed
  concurrently with zero drift and every accepted path visible in the final
  merged view.
- Rejected-write invisibility: stale rejected shell changes had no final-view
  trace.
- Gitignore routing through the sandbox-local `GitignoreOracle`: shell codegen
  writes under `dist/` are accepted and resolve through LWW in the codegen race
  probe.
- Gitignored overlap through direct provider execution: 8 raw `process.exec`
  writers targeting 4 `dist/` paths resolve to valid filesystem LWW final
  values without overlay/OCC attribution.
- Strict shell capture conflict handling: a shell capture that touched both a
  tracked conflicting file and a gitignored file published neither file from
  that shell.
- Failure recovery: conflict, compact, stale staging cleanup, and timeout
  paths leave no active lease/staging residue visible through runtime metrics.

### Concurrency Scaling: 1/5/10/20 Public Shell Calls

Follow-up focused scaling run:

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
EPHEMERALOS_LIVE_E2E_TIMING_JSONL=.omc/results/live-e2e-phase3-concurrency-scaling-20260505T161437Z.jsonl \
  .venv/bin/pytest \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_concurrency_scaling.py \
  -v -s --tb=short
```

Result: `1 passed, 1 warning in 47.20 s`.

Artifact:

```text
.omc/results/live-e2e-phase3-concurrency-scaling-20260505T161437Z.jsonl
```

Rows: 36 shell calls. Each row is a public `sandbox.api.tool.shell` call that
writes one unique gitignored `dist/scaling/...` path. The test verifies every
accepted path is present in `changed_paths` and visible through
`read_file(...)` after the batch.

Parallel factor uses the 1-call wall time as the serial baseline:

```text
parallel_factor = (baseline_1_call_wall_ms * concurrency) / batch_wall_ms
parallel_efficiency = parallel_factor / concurrency
```

| Concurrency | Batch wall | Per-call p50 | Per-call p99 | Serial equivalent | Parallel factor | Efficiency | Throughput |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1271.292 ms | 1270.462 ms | 1270.462 ms | 1270.462 ms | 0.999x | 99.9% | 0.787 ops/s |
| 5 | 1590.737 ms | 1451.747 ms | 1586.816 ms | 6352.312 ms | 3.993x | 79.9% | 3.143 ops/s |
| 10 | 2111.867 ms | 1788.706 ms | 2099.967 ms | 12704.623 ms | 6.016x | 60.2% | 4.735 ops/s |
| 20 | 3857.255 ms | 3151.007 ms | 3837.442 ms | 25409.247 ms | 6.587x | 32.9% | 5.185 ops/s |

Stage timing p99s, displayed in milliseconds:

| Concurrency | Dispatch p99 | Runtime API p99 | Overlay p99 | Run command p99 | Capture p99 | OCC apply p99 | OCC prepare p99 | OCC commit p99 | Layer lock wait p99 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1270.427 | 360.060 | 292.826 | 283.399 | 2.369 | 66.785 | 59.608 | 2.020 | 0.002 |
| 5 | 1586.789 | 627.476 | 341.834 | 331.451 | 2.897 | 285.189 | 61.839 | 2.608 | 0.005 |
| 10 | 2099.954 | 955.669 | 394.556 | 381.684 | 3.393 | 564.016 | 66.640 | 3.838 | 0.008 |
| 20 | 3837.430 | 1660.387 | 879.103 | 838.970 | 14.459 | 1114.958 | 150.530 | 5.280 | 0.004 |

Interpretation:

- The benchmark scales, but not linearly. Throughput improves from
  0.787 ops/s to 5.185 ops/s, while speedup flattens from 6.016x at 10 calls
  to 6.587x at 20 calls.
- At 20-way concurrency, the largest wall component is outside the sandbox
  runtime handler: `api.shell.dispatch_total_s - api.shell.total_s` has a
  p99 of about 2314 ms. That is host/provider dispatch and response overhead,
  not layer-stack commit work.
- Inside the runtime, `api.shell.occ_apply_s` grows to a 1115 ms p99 at
  concurrency 20, but the inner OCC timings stay much smaller:
  `occ.prepare.total_s` p99 is about 151 ms, `occ.commit.total_s` p99 is about
  5 ms, and `layer_stack.transaction.lock_wait_s` p99 remains near zero.
  The gap points to queueing around the runtime commit gate/file lock that
  wraps the OCC service, rather than slow layer publication.
- Overlay work increases under 20-way pressure, but it is still bounded:
  `overlay.run_command_s` p99 is about 839 ms for a tiny write command,
  `overlay.capture_changes_s` p99 is about 14 ms, and mount materialization
  remains below about 24 ms p99.
- Compared with the earlier gitignored-overlap probe, this run removes the
  test-side `/tmp` release barrier from the shell command. That is why the
  20-way `overlay.run_command_s` p99 is about 839 ms here instead of the
  ~2984 ms tail observed in the overlap probe.

Residual gaps:

- `test_load_profiles.py` still contains the four Phase 4/5 skip stubs. No
  Phase 4 smoke/sustained/burst/soak p99 budgets are claimed by this report.
- Full Phase 4 profile JSONL artifacts under `.omc/results/live-e2e-*.jsonl`
  are still a Phase 4 requirement.
