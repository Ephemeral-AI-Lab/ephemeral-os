# Phase 4 Pure `sandbox.api.*` Load Report

Date: 2026-05-06 local / 2026-05-05 UTC artifacts.

This report filters Phase 4 down to the public sandbox API path only. It
excludes native in-sandbox probes, direct `raw_exec`, and subsystem-only
`layer_stack` / `overlay` / `occ` tests.

## API Boundary

Timed load calls use:

- `sandbox.api.tool.shell_batch` for shell fan-out
- `sandbox.api.tool.edit_file` for edit fan-out

Setup and validation use:

- `sandbox.api.tool.write_file` to seed `.gitignore`, shared tracked files, and
  edit targets
- `sandbox.api.tool.read_file` to verify every accepted path is visible after
  the load batch

The setup and validation calls are intentionally excluded from load latency
tables because they are not part of the concurrent profile fan-out.

## Transport Change

The public runtime transport now assumes the runtime bundle was installed during
sandbox setup. Public API calls do not probe the bundle marker and do not upload
the bundle. If the bundle is missing, the runtime dispatch fails closed.

Shell fan-out now uses `api.shell_batch`, so the shell side sends one runtime
envelope for all shell items in a profile. That changes shell transport from
`shell_count` provider round trips to 1 provider round trip.

`edit_file`, `write_file`, and `read_file` still dispatch one public API
operation at a time.

## Verification Source

Focused unit verification:

```bash
.venv/bin/pytest \
  backend/tests/unit_test/test_sandbox/test_api/test_shell.py \
  backend/tests/unit_test/test_sandbox/test_api/test_daemon_transport.py \
  backend/tests/unit_test/test_sandbox/test_api/test_facade.py \
  -q
```

Result: `7 passed` in `0.14 s`.

Focused live load verification:

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  .venv/bin/pytest \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_load_profiles.py \
  --deselect backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_load_profiles.py::test_soak_profile_no_regression_over_15_min \
  -v -rs -s --tb=short
```

Result: `3 passed, 1 deselected` in `70.52 s`.

Focused live batch-scaling verification:

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  .venv/bin/pytest \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_concurrency_scaling.py \
  -v -rs -s --tb=short
```

Result: `1 passed` in `31.43 s`.

Pure API load artifacts:

- `.omc/results/live-e2e-integrated-smoke-20260505T173106Z.jsonl`
- `.omc/results/live-e2e-integrated-sustained-20260505T173125Z.jsonl`
- `.omc/results/live-e2e-integrated-burst-20260505T173203Z.jsonl`

## Metric Terms

- `Batch wall` is the elapsed host wall time for one measured group. In a
  concurrent API test, it starts when the shared barrier is released and ends
  when every call in that group completes.
- `Wall p50` / `Wall p99` are percentiles over the individual API-call wall
  times inside the measured group.
- `API runtime p50` / `API runtime p99` are percentiles over the in-sandbox
  runtime timing reported by each public API result. These exclude host/provider
  dispatch overhead.
- `Throughput` is `calls / batch_wall_seconds`.
- `Parallel factor` is `serial_equivalent_ms / batch_wall_ms`, where
  `serial_equivalent_ms` is the concurrency-1 baseline multiplied by the number
  of calls in the group.

## Profile Summary

| Profile | Timed API calls | Batch wall | API runtime p50 | API runtime p99 | Runtime budget | Runtime budget | Host wall p99 |
|---|---:|---:|---:|---:|---:|---|---:|
| `smoke` | 4 | 4412.206 ms | 199.278 ms | 470.957 ms | 500 ms | met | 1076.430 ms |
| `sustained` | 12 | 8061.291 ms | 455.577 ms | 700.439 ms | 1000 ms | met | 1359.841 ms |
| `burst` | 24 | 15316.685 ms | 900.910 ms | 1574.592 ms | 2500 ms | met | 2552.919 ms |

Compared with the previous non-batched pure API load run:

| Profile | Previous batch wall | Current batch wall | Improvement |
|---|---:|---:|---:|
| `smoke` | 6072.301 ms | 4412.206 ms | 27.3% |
| `sustained` | 12791.638 ms | 8061.291 ms | 37.0% |
| `burst` | 23622.075 ms | 15316.685 ms | 35.2% |

Interpretation: runtime p99 passed all budgets before and still passes. Host
wall p99 improved materially. The burst host wall p99 is now close to the
2500 ms diagnostic budget, but this run missed it by 52.919 ms.

## `sandbox.api.tool.shell_batch`

| Profile | Shell items | Batch dispatch p99 | Runtime p99 | Overlay p99 | Shell OCC apply p99 | Runtime batch p99 |
|---|---:|---:|---:|---:|---:|---:|
| `smoke` | 1 | 1082.988 ms | 477.627 ms | 406.537 ms | 70.686 ms | 480.227 ms |
| `sustained` | 4 | 1359.841 ms | 700.911 ms | 389.201 ms | 311.644 ms | 706.014 ms |
| `burst` | 8 | 2552.919 ms | 1576.777 ms | 564.849 ms | 1037.885 ms | 1605.978 ms |

Shell OCC internals:

| Profile | `occ.apply.total_s` | `occ.prepare.total_s` | `occ.commit.total_s` | `occ.serial.queue_wait_s` | `layer_stack.transaction.lock_wait_s` |
|---|---:|---:|---:|---:|---:|
| `smoke` | 69.241 ms | 61.168 ms | 3.473 ms | 3.801 ms | 0.001 ms |
| `sustained` | 66.804 ms | 58.017 ms | 4.434 ms | 3.570 ms | 0.003 ms |
| `burst` | 69.680 ms | 60.773 ms | 5.556 ms | 4.030 ms | 0.006 ms |

Shell conclusion: batching removed most provider round-trip amplification. The
remaining burst shell cost is inside the runtime batch: overlay command/capture
plus outer shell OCC apply. The inner OCC commit remains below 6 ms p99.

## `sandbox.api.tool.edit_file`

| Profile | Calls | Wall p50 | Wall p99 | Runtime p50 | Runtime p99 | Runtime max |
|---|---:|---:|---:|---:|---:|---:|
| `smoke` | 3 | 743.392 ms | 861.954 ms | 143.276 ms | 253.040 ms | 255.280 ms |
| `sustained` | 8 | 976.803 ms | 1231.455 ms | 320.995 ms | 556.954 ms | 562.056 ms |
| `burst` | 16 | 1746.852 ms | 2318.767 ms | 696.204 ms | 1114.012 ms | 1124.898 ms |

OCC stage p99s:

| Profile | `occ.apply.total_s` | `occ.prepare.total_s` | `occ.commit.total_s` | `occ.serial.queue_wait_s` | `layer_stack.transaction.lock_wait_s` |
|---|---:|---:|---:|---:|---:|
| `smoke` | 112.494 ms | 99.751 ms | 7.147 ms | 3.504 ms | 0.002 ms |
| `sustained` | 76.568 ms | 66.976 ms | 4.307 ms | 3.963 ms | 0.006 ms |
| `burst` | 130.563 ms | 118.907 ms | 6.131 ms | 4.201 ms | 0.011 ms |

Edit conclusion: `edit_file` remains cheaper than shell. Burst edit wall p99
is now 2.32 s and runtime p99 is 1.11 s. Commit remains a small slice; prepare
and apply dominate under mixed shell/edit contention.

## `sandbox.api.tool.write_file`

`write_file` is covered in this profile as setup, not as load. It seeds:

- `.gitignore`
- `tracked/load-shared.txt`
- `tracked/load-edit-XX.txt`

Each seed write is asserted committed before the timed batch starts. The report
does not claim write-load p99 because no `write_file` calls are currently in
the concurrent load fan-out.

## `sandbox.api.tool.read_file`

`read_file` is covered as final validation, not as load. It verifies:

- every accepted shell output path exists
- every edited tracked file contains the expected new value
- profile drift is `0`

The report does not claim read-load p99 because reads are sequential
post-profile checks.

## Correctness

All three pure API profiles had:

- all timed public API calls succeed
- no conflicts
- every accepted path visible via `sandbox.api.tool.read_file`
- all expected tracked edit contents present
- drift `0`
- JSONL artifacts emitted for every timed call

## Bottom Line

Pure `sandbox.api.*` runtime performance is acceptable for Phase 4:

- `smoke`: runtime p99 471 ms against 500 ms
- `sustained`: runtime p99 700 ms against 1000 ms
- `burst`: runtime p99 1575 ms against 2500 ms

Pure API host wall time is materially better after shell batching:

- `smoke`: wall p99 1076 ms
- `sustained`: wall p99 1360 ms
- `burst`: wall p99 2553 ms

The commit core is not the bottleneck. Across burst, `edit_file` commit p99 is
6.131 ms and shell-capture commit p99 is 5.556 ms. The next remaining latency
target is one-at-a-time edit/read/write dispatch and the runtime-side shell
overlay/OCC apply envelope.
