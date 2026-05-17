# Phase 05 Public File Ops Live E2E Report

Date: 2026-05-07 UTC

## Scope

This run implements
`.omc/plans/per-call-snapshot-layer-stack-migration/three-server-phase-05-live-e2e-public-file-ops.md`
against a real Daytona sandbox. The public surface under test is:

- `sandbox.api.read_file`
- `sandbox.api.write_file`
- `sandbox.api.edit_file`
- `sandbox.api.shell`

The imported workspace root is `/testbed`; the runtime layer-stack root is
`/tmp/eos-sandbox-runtime/layer-stack`.

## Commands

Image:

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1
```

Collection:

```bash
.venv/bin/pytest backend/tests/live_e2e_test --collect-only -q
```

Focused Phase 05 live run:

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
EPHEMERALOS_PHASE05_READ_C20_BATCH_WALL_BUDGET_MS=60000 \
EPHEMERALOS_PHASE05_WRITE_C20_BATCH_WALL_BUDGET_MS=60000 \
EPHEMERALOS_PHASE05_EDIT_C20_BATCH_WALL_BUDGET_MS=60000 \
EPHEMERALOS_PHASE05_SHELL_C20_BATCH_WALL_BUDGET_MS=60000 \
EPHEMERALOS_PHASE05_MIXED_C20_BATCH_WALL_BUDGET_MS=60000 \
EPHEMERALOS_PHASE05_C20_WALL_P99_BUDGET_MS=60000 \
EPHEMERALOS_PHASE05_C20_RUNTIME_P99_BUDGET_MS=60000 \
.venv/bin/pytest \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_public_file_ops_correctness.py \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_full_filesystem_view.py \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_public_file_ops_edge_cases.py \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_public_file_ops_load.py \
  -v -rs -s --tb=short
```

Unit guardrail:

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox -q
```

## Results

| Command | Result |
|---|---:|
| live collect-only | 104 collected |
| focused Phase 05 live run | 7 passed, 1 warning, 226.96 s |
| sandbox unit guardrail | 364 passed, 1 skipped, 1 warning, 2.38 s |

The first un-overridden load attempt stopped on the draft write c20 wall-p99
redline: observed `6881.341 ms` vs default `5000 ms`. The final run used the
plan's environment-variable budget overrides to collect the full matrix and
preserve correctness evidence.

## Artifacts

- `.omc/results/live-e2e-phase05-public-file-ops-correctness-20260507T160042Z.jsonl`
- `.omc/results/live-e2e-phase05-public-file-ops-full_filesystem_view-20260507T160103Z.jsonl`
- `.omc/results/live-e2e-phase05-public-file-ops-edge_conflicts-20260507T160124Z.jsonl`
- `.omc/results/live-e2e-phase05-public-file-ops-edge_shell_conflicts-20260507T160134Z.jsonl`
- `.omc/results/live-e2e-phase05-public-file-ops-load_matrix-20260507T160411Z.jsonl`

## Correctness Summary

| Area | Evidence |
|---|---|
| Imported base view | `read_file` and `shell` saw base `/testbed` content after raw `/testbed/raw.txt` was mutated to `dirty`. |
| Public mutations | `write_file`, `edit_file`, and `shell` commits were visible to later public reads and shell commands. |
| Full filesystem boundary | Relative and absolute `/testbed` public reads matched; `/testbed/../tmp/...` hard-rejected; `/tmp` and `/root/.cache` remained provider-FS passthrough. |
| Symlinks | `links/inside` resolved in-workspace; `links/outside` classified outside and did not advance the manifest. |
| Shell mixed FS | A shell writing absolute `/testbed/tracked/fullfs/absolute.txt` and `/tmp/phase05-mixed-outside.txt` published only the workspace file through OCC. |
| Conflicts | Same-path writes and overlapping edits produced exactly one accepted mutation and one conflict. |
| Disjoint edit retry | Concurrent disjoint edits on one file use deterministic retry when one request loses the full-file CAS race. |
| Shell stale conflicts | Stale shell write/delete lost to public writes and left no partial ignored/workspace output. |
| Nonzero shell policy | Shell side effects publish even when `exit_code != 0`; result `success` remains false. |
| Fail-closed cases | Create-only existing path rejected, binary edit failed closed, missing reads returned `exists=False`, timeout before write left no workspace publication or active leases. |

## Load Matrix

| Workload | C | Batch ms | Wall p50 | Wall p95 | Wall p99 | Runtime p99 | Parallel factor | Efficiency | Ops/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| read_file | 1 | 442.452 | 441.955 | 441.955 | 441.955 | 2.403 | 0.999 | 0.999 | 2.260 |
| read_file | 5 | 529.148 | 524.829 | 527.699 | 528.109 | 2.346 | 4.905 | 0.981 | 9.449 |
| read_file | 10 | 726.728 | 658.358 | 719.322 | 722.084 | 6.462 | 9.073 | 0.907 | 13.760 |
| read_file | 20 | 1645.809 | 1501.617 | 1598.568 | 1626.735 | 10.870 | 17.694 | 0.885 | 12.152 |
| write_file | 1 | 598.353 | 597.818 | 597.818 | 597.818 | 127.171 | 0.999 | 0.999 | 1.671 |
| write_file | 5 | 1784.348 | 1769.098 | 1781.826 | 1782.278 | 1300.586 | 4.949 | 0.990 | 2.802 |
| write_file | 10 | 4208.866 | 4148.614 | 4194.148 | 4205.317 | 3384.844 | 9.790 | 0.979 | 2.376 |
| write_file | 20 | 7737.663 | 7695.529 | 7723.483 | 7733.564 | 6698.569 | 19.306 | 0.965 | 2.585 |
| edit_file | 1 | 628.646 | 626.979 | 626.979 | 626.979 | 169.106 | 0.997 | 0.997 | 1.591 |
| edit_file | 5 | 2022.648 | 1980.747 | 2018.801 | 2020.565 | 1540.447 | 4.885 | 0.977 | 2.472 |
| edit_file | 10 | 4457.855 | 4415.194 | 4449.681 | 4451.532 | 3830.651 | 9.742 | 0.974 | 2.243 |
| edit_file | 20 | 9359.426 | 9117.360 | 9354.314 | 9354.500 | 7335.518 | 18.799 | 0.940 | 2.137 |
| shell | 1 | 1203.420 | 1202.663 | 1202.663 | 1202.663 | 746.912 | 0.999 | 0.999 | 0.831 |
| shell | 5 | 1870.703 | 1840.685 | 1862.569 | 1865.258 | 1270.169 | 4.809 | 0.962 | 2.673 |
| shell | 10 | 7325.950 | 7210.020 | 7321.256 | 7321.427 | 5709.434 | 9.349 | 0.935 | 1.365 |
| shell | 20 | 16161.182 | 16145.975 | 16157.279 | 16157.773 | 14451.139 | 19.839 | 0.992 | 1.238 |
| mixed | 1 | 666.330 | 665.598 | 665.598 | 665.598 | 10.101 | 0.999 | 0.999 | 1.501 |
| mixed | 5 | 1394.141 | 1082.456 | 1387.714 | 1390.205 | 399.325 | 4.311 | 0.862 | 3.586 |
| mixed | 10 | 2981.383 | 2200.186 | 2694.022 | 2918.543 | 1422.811 | 7.447 | 0.745 | 3.354 |
| mixed | 20 | 7822.641 | 6861.924 | 6956.222 | 7639.264 | 3916.462 | 16.834 | 0.842 | 2.557 |

Every load row had `all_calls_accounted=true`, `all_expected_paths_visible=true`,
`unexpected_conflicts=0`, and `final_reconciliation=true`.

## Timing Attribution

C20 p99 breakdown from the load artifact:

| Workload | OCC apply p99 ms | OCC prepare p99 ms | OCC commit p99 ms | Shell overlay p99 ms |
|---|---:|---:|---:|---:|
| write_file | 6760.559 | 6749.399 | 182.256 | 0 |
| edit_file | 7265.360 | 7040.880 | 165.525 | 0 |
| shell | 10837.891 | 10090.157 | 649.183 | 1296.667 |

The budget misses are dominated by OCC prepare/base-hash routing under c20
manifest lag, not by layer-stack lock wait. In the representative c20 shell
calls, `occ.prepare.route_and_base_hash_s` was about `9.7-10.1 s` while
`layer_stack.transaction.lock_wait_s` was near zero. Shell overlay work was
visible but smaller: c20 shell `api.shell.overlay_s` p99 was `1296.667 ms`,
with command runtime around `0.8-1.0 s` for most c20 shell calls.

## Redlines

Draft defaults passed for `read_file`. The following default c20 redlines missed
in this live environment:

| Workload | Draft redline | Observed |
|---|---:|---:|
| write_file wall p99 | 5000 ms | 7733.564 ms |
| write_file runtime p99 | 2500 ms | 6698.569 ms |
| edit_file wall p99 | 5000 ms | 9354.500 ms |
| edit_file runtime p99 | 2500 ms | 7335.518 ms |
| shell batch wall | 12000 ms | 16161.182 ms |
| shell wall p99 | 7000 ms | 16157.773 ms |
| shell runtime p99 | 4000 ms | 14451.139 ms |
| mixed wall p99 | 7000 ms | 7639.264 ms |

The focused run passed with explicit `60000 ms` overrides so the suite could
record the full matrix. Treat the default misses as current performance
evidence, not correctness failures.
