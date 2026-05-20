# O1 Overlay Mount — Validation Runbook (§4)

Step-by-step runbook for the heavy_enabled live_e2e on sqlite. Copy-pasteable commands.

---

## 4.1 Pre-flight (STOP if any step fails)

**Step 1 — Confirm Linux host ≥ 5.2:**
```bash
uname -r
# expect: 5.2 or higher (e.g., 6.5.0-...)
```

**Step 2 — Confirm new mount API available:**
```bash
cd backend
.venv/bin/python -c "
from sandbox.execution.overlay.new_mount_api import probe_supported
print('probe_supported():', probe_supported())
"
# expect: probe_supported(): True
```

**Step 3 — Confirm CAP_SYS_ADMIN (if running in Docker):**
```bash
capsh --print 2>/dev/null | grep sys_admin || echo "capsh not available; check docker --cap-add=SYS_ADMIN"
```

**Step 4 — Export SQLite database URL:**
```bash
export EPHEMERALOS_DATABASE_URL="sqlite:////tmp/eos-validation.db"
```

**Step 5 — Initialize the SQLite schema:**

NOTE: This project does NOT have a migration CLI. Schema is bootstrapped via
`Base.metadata.create_all`. The correct import is `from db.base import Base` +
`import db.models` (the latter populates Base.metadata as a side effect).
The plan's §4.1 reference to `task_center_runner.core.models` is incorrect —
use this command instead:

```bash
cd backend
.venv/bin/python tests/live_e2e_test/sandbox/overlay/native/bootstrap_sqlite_validation.py
# Expected stdout:
# created N tables: ['agent_attempts', 'agent_runs', ...] ...
# Database ready at: sqlite:////tmp/eos-validation.db
```

Pass condition: exits 0 and prints "created N tables" with N > 0.

---

## 4.2 Heavy scenarios

These scenarios run when `EPHEMERALOS_DATABASE_URL` is set and the database is
initialized. All three are in `backend/src/task_center_runner/tests/sweevo/`:

- `test_complex_project_build_full.py` — primary perf scenario
- `test_complex_project_build_grep_glob_full.py` — filesystem-heavy variant
- `test_complex_project_build_shell_edit_lsp_full.py` — canary for plugin path (must pass to prove `WorkspaceProjection` not regressed)

---

## 4.3 Pre-change baseline — 5-run median+σ

```bash
cd backend
git tag baseline-pre-fsopen $(git rev-parse HEAD)

for run in 1 2 3 4 5; do
  .venv/bin/pytest -xvs \
    src/task_center_runner/tests/sweevo/test_complex_project_build_full.py \
    src/task_center_runner/tests/sweevo/test_complex_project_build_grep_glob_full.py \
    src/task_center_runner/tests/sweevo/test_complex_project_build_shell_edit_lsp_full.py \
    2>&1 | tee /tmp/eos-baseline-run-${run}.log
  cp -r .sweevo_runs /tmp/eos-baseline-run-${run}-sweevo_runs
done

# Aggregate 5 runs: compute median, p95, σ per metric
.venv/bin/python scripts/analyze_complex_build_perf.py \
  --baseline-aggregate \
  /tmp/eos-baseline-run-*/sweevo_runs/*/perf.json \
  > /tmp/eos-baseline-stats.txt

cat /tmp/eos-baseline-stats.txt
```

Expected baseline indicators (pre-change):
- `layer_stack.materialize_p95_s` > 0
- `layer_stack.materialize_count` ≈ shell_count per session
- Per-lease tmpfs delta > 0 (the EXDEV copy is happening)

---

## 4.4 Post-change run

After Steps 1–11 are merged:

```bash
cd backend
.venv/bin/pytest -xvs \
  src/task_center_runner/tests/sweevo/test_complex_project_build_full.py \
  src/task_center_runner/tests/sweevo/test_complex_project_build_grep_glob_full.py \
  src/task_center_runner/tests/sweevo/test_complex_project_build_shell_edit_lsp_full.py \
  2>&1 | tee /tmp/eos-post.log

.venv/bin/python scripts/analyze_complex_build_perf.py \
  .sweevo_runs/*/perf.json \
  > /tmp/eos-post-perf.txt

.venv/bin/python scripts/analyze_complex_build_perf.py \
  --compare-to /tmp/eos-baseline-stats.txt \
  .sweevo_runs/*/perf.json \
  > /tmp/eos-post-vs-baseline.txt

cat /tmp/eos-post-vs-baseline.txt
```

---

## 4.5 Pass/fail thresholds (data-driven)

For every numeric metric: `post_value ≤ max(baseline_median × 1.20, baseline_median + 3σ)`

| Metric | Baseline | Post | Pass condition |
|---|---|---|---|
| `layer_stack.materialize_p95_s` | > 0 (5-run median) | 0 or absent | < baseline_median × 0.05 (improvement gate) |
| `layer_stack.materialize_count` | ≈ shell_count | 0 | == 0 for namespace mode |
| `command_exec.mount_workspace_s` p95 | 5-run median+σ | post p95 | ≤ max(median × 1.20, median + 3σ) |
| `complex_project_build` exit code | 0 | 0 | unchanged |
| `complex_project_build_shell_edit_lsp` exit code | 0 | 0 | unchanged (proves plugin path not regressed) |
| `complex_project_build_grep_glob` exit code | 0 | 0 | unchanged |
| Per-lease upperdir+workdir bytes | > 0 (EXDEV copy) | ≤ 64 KiB | hard per-lease threshold (from Bound A harness) |

---

## 4.6 Regression diagnosis

**If `command_exec.mount_workspace_s` regresses:**
1. Check `overlay.new_mount_api.unavailable_total` — if > 0, probe_supported() is failing and fell back to materialize+mount(8).
2. Check `layer_stack.depth_guard_violations_total` — if > 0, manifest depth exceeded OVL_MAX_STACK_GUARD=110; squash may not be running.
3. Compare `command_exec.layer_count` distribution against baseline — a Bound C regression shows as growing mount cost at the same depth.

**If `complex_project_build_shell_edit_lsp` fails:** the plugin path is regressed. Verify Step 11 (materialize=True branch is untouched).

**Run the Bound A/B/C audit harness directly:**
```bash
cd backend
docker run --rm \
  --cap-add=SYS_ADMIN \
  --security-opt seccomp=unconfined \
  --security-opt apparmor=unconfined \
  -v $(pwd):/workspace \
  -w /workspace \
  python:3.11-slim \
  bash -c "pip install pytest -q && .venv/bin/pytest -xvs \
    tests/live_e2e_test/sandbox/overlay/native/test_o1_lease_count_bound.py \
    tests/live_e2e_test/sandbox/overlay/native/test_o1_manifest_depth_bound.py \
    tests/live_e2e_test/sandbox/overlay/native/test_o1_per_read_cpu_bound.py \
    tests/live_e2e_test/sandbox/overlay/native/test_o1_adversarial_harness_self_test.py \
    tests/live_e2e_test/sandbox/overlay/native/test_o1_memory_bound.py"
```

**Per-metric drilldown:** check `.sweevo_runs/<run_id>/o1_audit.json` for per-bound pass/fail detail.
