# Phase 05 Public File Ops Performance Metrics Report

**Date:** 2026-05-08 Asia/Shanghai
**Run source:** live Daytona-backed sandbox tests in the current dirty checkout
**Workspace root:** `/testbed`
**Layer-stack root:** `/tmp/eos-sandbox-runtime/layer-stack`
**Operations covered:** `read_file`, `write_file`, `edit_file`, `shell`

## 1. Verification Commands

### 1.1 Four-operation load matrix

```bash
EPHEMERALOS_LIVE_E2E_TIMING_JSONL=.omc/results/live-e2e-phase05-public-file-ops-per-call-20260508-current.jsonl \
EPHEMERALOS_PHASE05_READ_C20_BATCH_WALL_BUDGET_MS=60000 \
EPHEMERALOS_PHASE05_WRITE_C20_BATCH_WALL_BUDGET_MS=60000 \
EPHEMERALOS_PHASE05_EDIT_C20_BATCH_WALL_BUDGET_MS=60000 \
EPHEMERALOS_PHASE05_SHELL_C20_BATCH_WALL_BUDGET_MS=60000 \
EPHEMERALOS_PHASE05_MIXED_C20_BATCH_WALL_BUDGET_MS=60000 \
EPHEMERALOS_PHASE05_C20_WALL_P99_BUDGET_MS=60000 \
EPHEMERALOS_PHASE05_C20_RUNTIME_P99_BUDGET_MS=60000 \
.venv/bin/pytest \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_public_file_ops_load.py \
  -v -rs -s --tb=short
```

Result:

```text
1 passed, 1 warning in 117.89s
```

Fresh artifacts:

```text
.omc/results/live-e2e-phase05-public-file-ops-load_matrix-20260507T161532Z.jsonl
.omc/results/live-e2e-phase05-public-file-ops-per-call-20260508-current.jsonl
```

The load-matrix timestamp is UTC; it corresponds to the May 8 local run.

### 1.2 `workspace_bound=true` check

```bash
EPHEMERALOS_READ_LOAD_FILES=2 \
EPHEMERALOS_READ_LOAD_CALLS=2 \
EPHEMERALOS_READ_LOAD_CONCURRENCY=1 \
.venv/bin/pytest \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_workspace_base_read_load.py::test_workspace_base_read_load_metrics \
  -v -rs -s --tb=short
```

Result:

```text
1 passed, 1 warning in 11.15s
base_manifest_version=1
base_root_hash=f0036fdb7186324da0b2bb1bd101730a9eddcb39995b5630fd4f885e5a2d078c
```

This test calls `handle.tool.layer_metrics()` and asserts:

```python
assert result["workspace_bound"] is True
```

So yes: the explicit `workspace_bound=true` path was verified after the
previous doc-only turn.

## 2. Methodology

The load matrix launches independent public API calls behind a barrier. It does
not batch operations into one shell command.

Concurrencies:

```text
1, 5, 10, 20
```

Pure workload calls:

```text
read_file  = 36 calls
write_file = 36 calls
edit_file  = 36 calls
shell      = 36 calls
total      = 144 pure-operation calls
```

The same test also ran a separate `mixed` workload, but this report excludes it
from the four-operation tables except where noted in the artifact summary.

Correctness gates for each workload/concurrency row:

```text
all_calls_accounted=true
all_expected_paths_visible=true
unexpected_conflicts=0
final_reconciliation=true
```

## 3. Top-Level Results

| Operation | C | Calls | Batch ms | Wall p50 | Wall p95 | Wall p99 | Runtime p99 | Parallel factor | Efficiency | Ops/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| read_file | 1 | 1 | 387.654 | 386.001 | 386.001 | 386.001 | 2.270 | 0.996 | 0.996 | 2.580 |
| read_file | 5 | 5 | 447.769 | 440.228 | 442.609 | 442.665 | 2.159 | 4.909 | 0.982 | 11.166 |
| read_file | 10 | 10 | 513.462 | 487.285 | 507.166 | 507.251 | 2.447 | 9.540 | 0.954 | 19.476 |
| read_file | 20 | 20 | 980.161 | 841.528 | 943.619 | 963.161 | 6.209 | 16.870 | 0.843 | 20.405 |
| write_file | 1 | 1 | 505.670 | 503.810 | 503.810 | 503.810 | 104.640 | 0.996 | 0.996 | 1.978 |
| write_file | 5 | 5 | 1651.351 | 1637.124 | 1646.599 | 1647.096 | 1212.370 | 4.942 | 0.988 | 3.028 |
| write_file | 10 | 10 | 3520.774 | 3454.634 | 3498.619 | 3514.033 | 3027.004 | 9.785 | 0.979 | 2.840 |
| write_file | 20 | 20 | 6838.251 | 6761.763 | 6784.157 | 6825.358 | 6068.432 | 19.394 | 0.970 | 2.925 |
| edit_file | 1 | 1 | 598.618 | 598.189 | 598.189 | 598.189 | 162.593 | 0.999 | 0.999 | 1.671 |
| edit_file | 5 | 5 | 1860.622 | 1855.640 | 1857.580 | 1857.768 | 1429.798 | 4.975 | 0.995 | 2.687 |
| edit_file | 10 | 10 | 4234.745 | 4143.922 | 4230.329 | 4232.943 | 3633.674 | 9.692 | 0.969 | 2.361 |
| edit_file | 20 | 20 | 7812.247 | 7538.002 | 7796.831 | 7802.571 | 6568.972 | 18.537 | 0.927 | 2.560 |
| shell | 1 | 1 | 1103.064 | 1102.351 | 1102.351 | 1102.351 | 687.013 | 0.999 | 0.999 | 0.907 |
| shell | 5 | 5 | 1268.335 | 1227.306 | 1263.163 | 1265.109 | 761.694 | 4.804 | 0.961 | 3.942 |
| shell | 10 | 10 | 1587.247 | 1361.168 | 1557.498 | 1578.087 | 773.686 | 8.707 | 0.871 | 6.300 |
| shell | 20 | 20 | 6069.358 | 5999.240 | 6014.383 | 6048.911 | 5191.793 | 19.714 | 0.986 | 3.295 |

## 4. C20 Stage Attribution

### 4.1 `read_file`

| Stage | p99 ms |
|---|---:|
| API total | 6.209 |
| Layer-stack read | 5.805 |
| Lease acquire | 0.247 |
| Runtime dispatch | 6.234 |
| Boot to dispatch | 0.490 |

Interpretation:

`read_file` is dominated by provider/transport wall time, not runtime work.
At C20, per-call wall p99 is `963.161 ms` while runtime p99 is only
`6.209 ms`.

### 4.2 `write_file`

| Stage | p99 ms |
|---|---:|
| API total | 6068.432 |
| API OCC apply | 6065.077 |
| OCC apply total | 6064.308 |
| OCC prepare | 6021.410 |
| Route + base hash | 6021.406 |
| Commit queue wait | 125.484 |
| OCC commit phase | 214.494 |
| Commit transaction | 172.600 |
| Layer publish | 23.693 |
| Layer lock wait | 0.005 |
| Runtime dispatch | 6068.486 |

Interpretation:

`write_file` C20 is dominated by OCC prepare, specifically route/base-hash
work. The layer-stack publish path is not the bottleneck: publish p99 is
`23.693 ms` and lock wait p99 is effectively zero.

### 4.3 `edit_file`

| Stage | p99 ms |
|---|---:|
| API total | 6568.972 |
| API OCC apply | 6509.190 |
| Snapshot read | 73.956 |
| Derive bytes | 0.182 |
| OCC apply total | 6504.625 |
| OCC prepare | 6359.230 |
| Route + base hash | 6359.226 |
| Commit queue wait | 89.521 |
| OCC commit phase | 275.822 |
| Commit transaction | 182.386 |
| Layer publish | 24.518 |
| Layer lock wait | 0.763 |
| Runtime dispatch | 6569.034 |

Interpretation:

`edit_file` has the same dominant cost as `write_file`: OCC prepare/base-hash
routing. Edit-specific byte derivation is negligible (`0.182 ms` p99), and the
snapshot read cost is visible but secondary (`73.956 ms` p99).

### 4.4 `shell`

| Stage | p99 ms |
|---|---:|
| API total | 5191.793 |
| Workspace replacement/capture | 452.745 |
| API OCC apply | 4044.543 |
| Prepare snapshot | 2665.943 |
| Materialize lowerdir | 2664.727 |
| Mount workspace | 36.308 |
| Run command | 419.220 |
| Capture upperdir | 37.535 |
| Release snapshot | 50.869 |
| OCC apply total | 4042.385 |
| OCC prepare | 3556.258 |
| Route + base hash | 3556.254 |
| Commit queue wait | 110.943 |
| OCC commit phase | 359.802 |
| Commit transaction | 227.850 |
| Runtime dispatch | 5192.093 |

Interpretation:

`shell` has two real costs:

- snapshot materialization before command execution (`2664.727 ms` p99)
- OCC prepare/base-hash routing after capture (`3556.254 ms` p99)

The actual command used in this load test is simple; command runtime p99 is
`419.220 ms`, smaller than the snapshot and OCC costs.

## 5. Default Budget Status

The run used `60000 ms` budget overrides so the full matrix would complete and
write artifacts. Compared against the draft defaults in
`test_phase05_public_file_ops_load.py`, the current results are:

| Operation | Batch observed/budget | Wall p99 observed/budget | Runtime p99 observed/budget | Default status |
|---|---:|---:|---:|---|
| read_file | 980.161 / 5000 | 963.161 / 3000 | 6.209 / 1000 | pass |
| write_file | 6838.251 / 8000 | 6825.358 / 5000 | 6068.432 / 2500 | miss |
| edit_file | 7812.247 / 8000 | 7802.571 / 5000 | 6568.972 / 2500 | miss |
| shell | 6069.358 / 12000 | 6048.911 / 7000 | 5191.793 / 4000 | miss |

Default redline misses are performance evidence, not correctness failures. All
pure workload calls succeeded and reconciled.

## 6. Findings

1. `workspace_bound=true` was explicitly verified after setup. The public
   runtime had `base_manifest_version=1`, and the test that passed asserts
   `layer_metrics()["workspace_bound"] is True`.

2. `read_file` scales well in runtime terms. Runtime p99 stays below `7 ms` at
   C20; wall p99 is nearly all provider/runtime round-trip overhead.

3. `write_file` and `edit_file` are bottlenecked by OCC prepare/base-hash
   routing under C20. Commit, publish, and lock wait are small compared with
   route/base-hash work.

4. `shell` is faster in this run than the May 7 report, but still misses the
   default runtime p99 budget. Its two meaningful costs are lowerdir
   materialization and OCC prepare.

5. The layer-stack lock is not the limiting factor in this matrix. C20 lock-wait
   p99 is `0.005 ms` for write, `0.763 ms` for edit, and near-zero relative to
   shell's multi-second snapshot/OCC work.

6. For the user's stated read/write/edit-heavy workload, the highest-leverage
   optimization remains reducing repeated OCC prepare/base-hash work and
   transport overhead. A shell-specific server split would not materially
   improve `read_file`, `write_file`, or `edit_file` based on these numbers.

## 7. Raw Evidence

```text
.omc/results/live-e2e-phase05-public-file-ops-load_matrix-20260507T161532Z.jsonl
.omc/results/live-e2e-phase05-public-file-ops-per-call-20260508-current.jsonl
.omc/results/live-e2e-phase01-workspace-base-workspace_base_read_load-20260507T161625Z.jsonl
```
