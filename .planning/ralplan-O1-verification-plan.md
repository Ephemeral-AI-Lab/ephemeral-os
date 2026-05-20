# O(1) Overlay Mount — Verification Plan

**Goal:** prove that the direct-`mount(2)`-via-new-API path delivers per-lease lower-side disk cost that is **bounded in N** (the number of concurrent leases), with bounded memory growth.

**Anchor commits:** `2a529c67f` … `90bbfc350` (T1-T5 of the O(1) overlay-mount migration).

**Pre-existing telemetry to lean on:** `backend/src/sandbox/execution/resource_audit.py` (commit `1161ec4cc`). It already emits — per shell-exec — the variables we need:

| Key | Meaning |
|---|---|
| `resource.layer_stack.manifest_depth` | Number of layers in the active manifest at lease time |
| `resource.command_exec.run_dir_size_bytes` | Total bytes under the per-lease run directory |
| `resource.command_exec.upperdir_size_bytes` | Bytes in the lease's upper (writes captured during exec) |
| `resource.command_exec.scratch_filesystem_free_bytes` | Free bytes on the scratch tmpfs at sample time |
| `resource.layer_stack.storage_filesystem_free_bytes` | Free bytes on layer-stack storage FS |
| `resource.process_rss_bytes`, `resource.process_max_rss_bytes` | RSS and peak RSS of the daemon process |

Verification consumes these timing keys directly. No new instrumentation needed for scenario-level validation.

---

## 1. What "O(1) space and memory" means here, formally

Let `N` = concurrent active leases, `M` = manifest depth, `W` = command write bytes.

| Claim | Falsifiable statement |
|---|---|
| **O(1) lower-side disk per lease** | `max_over_leases( delta(scratch_filesystem_free_bytes) − upperdir_size_bytes )` ≤ **4 KiB**, independent of N and M. |
| **O(W) upper-side disk per lease** | `upperdir_size_bytes` ≤ `1.1 × W_observed` (10% slack for overlay metadata). |
| **O(1) memory per lease** | `delta(daemon_rss_bytes) / N` ≤ **2 MiB** at N=200 vs N=1 baseline. |
| **O(M) read CPU bounded** | Per-file negative-lookup CPU slope ≤ **50 µs/layer** in `cmd.exec.user_s`. |
| **O(N+M) is REJECTED** | If any lease shows `delta(scratch_free) ∝ workspace_size`, the new mount API is silently falling back to materialize and the test must fail loudly. |

The last row is the negative control. Without it, a green test means nothing — the harness must catch the case where the kill switch flips on by accident or the probe misreports.

---

## 2. The harness must compare BOTH paths

The previous harness measured only the new path and asserted "small." That can pass even if the new path is broken — `delta(scratch_free)` will be small either way because the scratch tmpfs is small. **The test must drive the same workload through both paths and compare.**

```
for path in ["new_mount_api", "legacy_materialize"]:
    with kill_switch_env(path):
        record = run_workload(N=…, M=…)
        assert telemetry_path_matches(record, path)  # verify which path actually ran
        store(path, record)

assert legacy.scratch_free_delta_per_lease >= 100 × new_api.scratch_free_delta_per_lease
assert new_api.scratch_free_delta_per_lease <= 4 KiB + upperdir_bytes
```

Telemetry confirms which path ran via the daemon RPC `mount_mode` field (already in scope per Step 8 of the original plan; emitted as `private_namespace_new_api` vs `private_namespace_legacy`).

---

## 3. Three-ring scenario suite

### Ring 1 — Correctness (must pass on every change)

Run these against both `materialize=True` and `materialize=False` paths to verify behavioral equivalence.

| Test | What it stresses | Pass criterion |
|---|---|---|
| `backend/src/task_center_runner/scenarios/pipeline/dependency_dag_parallel.py` | N parallel agents → N concurrent overlay mounts on same manifest. Lease refcounting + concurrent `fsopen`. | All agents succeed; no `ESTALE` / `ENOENT` in stderr; final manifest version = `base + N`. |
| `backend/src/task_center_runner/scenarios/pipeline/dependency_dag_diamond.py` | Late agents see committed writes from earlier branches. OCC stale-base detection under direct-mount layers. | Final merged view contains every diamond branch's writes; `ABORTED_VERSION` count = expected race count, no cascade past it. |
| `backend/src/task_center_runner/scenarios/correctness_testing.py` | Broad mutation correctness across the toolset. | Existing assertions hold. |
| `backend/src/task_center_runner/tests/sweevo/test_auto_squash_commit_resume.py` | Squash crosses the boundary while leases hold layers. | Squash completes; held leases continue serving correct content post-squash; no `ESTALE` in command stderr. |

Command (run inside CAP_SYS_ADMIN container, with `EPHEMERALOS_DATABASE_URL=sqlite:////tmp/eos-validation.db`):

```bash
cd backend && uv run pytest \
  src/task_center_runner/scenarios/pipeline/dependency_dag_parallel.py \
  src/task_center_runner/scenarios/pipeline/dependency_dag_diamond.py \
  src/task_center_runner/scenarios/correctness_testing.py \
  src/task_center_runner/tests/sweevo/test_auto_squash_commit_resume.py \
  -v --tb=short
```

### Ring 2 — Performance (heavy_enabled=true required)

Heavy gate at `backend/src/task_center_runner/tests/_live_config.py:13`. Enable via central-config: `runner.live_e2e.heavy_enabled = true`. These tests measure the actual claim.

| Test | Workload shape | Key metric | Threshold |
|---|---|---|---|
| `tests/sweevo/test_complex_project_build_full.py` | ~100 shell-exec, ~50 file writes, mixed read+write churn. | `command_exec.mount_workspace_s` p50 | ≤ **baseline × 0.30** (mount is where the savings concentrate — materialize did the full tree copy here) |
| | | `api.shell.total_s` p50 | ≤ baseline × 1.10 (no broader regression) |
| | | `resource.command_exec.scratch_filesystem_free_bytes` minimum | ≥ 500 MiB at all sample points (the O(1) claim at scenario level) |
| `tests/sweevo/test_complex_project_build_grep_glob_full.py` | Read-heavy. ~500 grep / glob over many files at depth M. | `cmd.exec.user_s` p50 | ≤ baseline × 1.05 (per-read CPU bound holds at scenario level — the kernel layer-walk concern) |
| `tests/sweevo/test_complex_project_build_shell_edit_lsp_full.py` | Mixed; pushes manifest depth. | `resource.layer_stack.manifest_depth` p99 | ≤ 120 (squash still working; AUTO_SQUASH_MAX_DEPTH=100 with slack) |
| | | `resource.process_max_rss_bytes` final | ≤ baseline × 1.05 (no daemon RSS leak) |

Command (assumes baseline captured before the migration, stored at `.planning/o1-baseline.json` with median+σ per metric):

```bash
cd backend && \
  EPHEMERALOS_DATABASE_URL=sqlite:////tmp/eos-validation.db \
  uv run pytest \
    src/task_center_runner/tests/sweevo/test_complex_project_build_full.py \
    src/task_center_runner/tests/sweevo/test_complex_project_build_grep_glob_full.py \
    src/task_center_runner/tests/sweevo/test_complex_project_build_shell_edit_lsp_full.py \
    -v --tb=short --durations=10
```

Threshold function for any metric `m`:
```
fail_if observed[m] > max(baseline.median[m] × 1.10, baseline.median[m] + 3 × baseline.sigma[m])
```

5-run baseline; freeze median+σ in `.planning/o1-baseline.json`.

### Ring 3 — Saturation (optional, slow)

`backend/src/task_center_runner/scenarios/capacity/full_system_capacity_matrix.py` — N agents × M concurrent shells. Validates the 2 GiB `/eos-mount-scratch` tmpfs no longer fills under fan-out (the original concurrency wall this whole project was about).

| Metric | Threshold |
|---|---|
| `resource.command_exec.scratch_filesystem_free_bytes` (min across run) | ≥ 500 MiB |
| Total mount failures (counter of `MountAPIUnavailable` exceptions) | = 0 |
| RSS slope vs lease count | ≤ 2 MiB / lease |

---

## 4. Microbenchmark harness (the rewrite)

Location: `backend/tests/live_e2e_test/sandbox/overlay/native/`. Rewrites of the four files from T6.

### 4.1 Telemetry source

Replace custom `du -sb` / `df` calls with consumption of the existing per-shell timings dict. The harness:

1. Mounts the daemon with a sqlite store.
2. Runs N concurrent `api.shell(...)` calls.
3. Reads the `timings` dict on each response — already contains every key in the table above.
4. Aggregates per-N, per-M, per-path.

This eliminates the per-lease attribution bug (whole-tmpfs `df` was the problem in iter 1). The telemetry is already scoped per lease by `resource_audit.py`.

### 4.2 Tests to rewrite

| File | Sweep | Acceptance |
|---|---|---|
| `test_o1_lease_count_bound.py` | N ∈ {1, 10, 50, 100, 200} at M=10. **Both paths.** | `legacy.scratch_free_delta_per_lease ≥ 100× new_api.scratch_free_delta_per_lease`; `max(new_api.scratch_free_delta_per_lease − upperdir_size_bytes) ≤ 4 KiB` |
| `test_o1_manifest_depth_bound.py` | M ∈ {**2**, 10, 50, 100, 110} at N=10. RW mounts with upper on `/dev/shm` (matches prod). | `slope(mount_workspace_s, M) ≤ 5 ms/layer`; `scratch_free_delta` flat in M |
| `test_o1_per_read_cpu_bound.py` | M ∈ {2, 10, 50, 100, 110}. Workload: `cat <known_file>` (not negative lookup — kernel-walk cost is on successful resolution too). | `slope(cmd_exec_user_s, M) ≤ 50 µs/layer` |
| `test_o1_adversarial_harness_self_test.py` | N=50, **two** injected regressions: (a) 1 MiB upper write in lease 25 (existing), (b) materialize-on-namespace-path forced via direct call to `MergedView.materialize` in lease 30. | Both regressions must be named in `AssertionError`. |
| `test_o1_memory_bound.py` (NEW) | N ∈ {1, 200} at M=10. | `(rss_at_N200 − rss_at_N1) / 200 ≤ 2 MiB` per lease |

### 4.3 Fixes to apply alongside

- Change `pytest.skip()` → `continue` in B/C sweeps so single-M failures don't abandon the whole sweep.
- Start M sweeps at 2 (overlay `lowerdir+` requires ≥2 entries for ro-without-upper). Or mount RW with `upperdir` on `/dev/shm` and start at M=1.
- All tests `pytest.mark.skipif(sys.platform != "linux" or not has_cap_sys_admin())`.

---

## 5. Pass/fail decision tree

```
                   ┌─────────────────────────────────────────┐
                   │ Run Ring 1 (correctness)                │
                   └────────────────┬────────────────────────┘
                                    │
                          ┌─────────┴─────────┐
                          v                   v
                      All pass            Any fail
                          │                   │
                          v                   v
                  Run Ring 2 (perf)      STOP — fix
                          │              correctness
              ┌───────────┴───────────┐  before perf
              v                       v
     mount_workspace_s        Any threshold breached
       ≤ baseline × 0.30
              │                       │
              v                       v
       Run microbench harness   Diagnose: probe?
       (§4.2 tests)             kill switch? metric?
              │
      ┌───────┴───────┐
      v               v
  Bound A negative   Bounds B, C, memory pass
  control: legacy ≥
  100× new
      │
      v
  All four bounds + ring 2 perf pass
              │
              v
  T10 complete: runtime no longer has a separate stack-depth
  guard or typed depth exception. Manifest depth is bounded by
  `AUTO_SQUASH_MAX_DEPTH = 100`; squash health is observed via
  `resource.layer_stack.manifest_depth` p99 alert.
```

---

## 6. What goes into `.planning/o1-baseline.json`

Captured BEFORE running the post-change scenarios, on a clean checkout of `main` at the parent commit of `2a529c67f`. 5 runs each, median + σ recorded.

```json
{
  "test_complex_project_build_full": {
    "command_exec.mount_workspace_s": { "median": 0.0XX, "sigma": 0.0XX, "n": 5 },
    "api.shell.total_s":              { "median": 0.0XX, "sigma": 0.0XX, "n": 5 },
    "resource.command_exec.scratch_filesystem_free_bytes_min": { … }
  },
  "test_complex_project_build_grep_glob_full": {
    "cmd.exec.user_s":                { "median": 0.0XX, "sigma": 0.0XX, "n": 5 }
  },
  "test_complex_project_build_shell_edit_lsp_full": {
    "resource.layer_stack.manifest_depth_p99": { "median": …, "sigma": … },
    "resource.process_max_rss_bytes":          { "median": …, "sigma": … }
  },
  "git_baseline_sha": "<parent of 2a529c67f>"
}
```

The threshold function reads this file and computes `max(median × 1.10, median + 3σ)` per metric.

---

## 7. Execution order

```
Step  Owner       Task                                                  Wall time
────  ─────────── ────────────────────────────────────────────────────  ─────────
T8    worker-1    Faithful-fix harness (§4)                                 ~30m
T8b   worker-4    Capture baseline on parent of 2a529c67f                   ~20m
T9.1  worker-4    Ring 1 correctness (both paths)                           ~15m
T9.2  worker-4    Ring 2 perf vs baseline (heavy_enabled sweevo)            ~45m
T9.3  worker-4    Ring 3 saturation (optional)                              ~30m
T10   worker-1    Strip runtime stack-manifest-depth per CLAUDE.md §2          DONE
T11   lead        ADR closure + memory note update                          DONE
```

Each task ends with an atomic commit. Per-task commits ≠ stage `git add .` — list files explicitly (parallel codex sessions are active in unrelated paths per `feedback_parallel_user_commits.md` memory).

---

## 8. Acceptance summary (the one-liner)

The verification passes iff, on Linux + CAP_SYS_ADMIN:

1. **Bound A** with negative control: `legacy.scratch_free_delta / new.scratch_free_delta ≥ 100×`, and `max(new.scratch_free_delta − upperdir_bytes) ≤ 4 KiB` over N ∈ {1, 10, 50, 100, 200}.
2. **Bound B**: `slope(mount_workspace_s, M) ≤ 5 ms/layer` over M ∈ {2, 10, 50, 100, 110}.
3. **Bound C**: `slope(cmd_exec_user_s, M) ≤ 50 µs/layer` over the same sweep.
4. **Memory bound**: `(rss_at_N200 − rss_at_N1) / 200 ≤ 2 MiB`.
5. **Ring 1 correctness**: all four scenarios pass on both `materialize=True` and `materialize=False`.
6. **Ring 2 performance**: `mount_workspace_s` p50 ≤ baseline × 0.30; all other metrics within `max(median × 1.10, median + 3σ)`.
7. **Adversarial self-test**: both injected regressions (upper write + forced materialize) are caught and named.

On pass: guard removal (T10) and ADR/memory-note closure (T11) are complete; close the team.
On fail at any step: stop the pipeline, diagnose, do not advance.
