# Phase 2 — Mock-Scenario Suite Verification & Hardening

**Goal:** Bring the PG-backed mock-scenario test suite to fully green after the
task_center_runner restructure (`.omc/plans/task_center_runner-restructure.md`,
landed in 19 commits b7c5f286 → 7e5d910d). Real-LLM / Daytona-gated tests
stay deferred until a session with full provider access.

**Scope of "mock-scenario suite":**

```
backend/src/task_center_runner/tests/sweevo/
├── test_auto_squash_commit_resume.py
├── test_complex_project_build.py
├── test_complex_project_build_fixtures.py
├── test_complex_project_build_shell_edit_lsp.py
├── test_correctness.py
├── test_correctness_via_live_e2e.py
├── test_focused_scenarios.py
├── test_full_case_user_input.py
├── test_full_stack_adversarial.py
└── test_partial_parent_planner_full_only.py
```

Plus the PG-only file-level tests:

```
backend/src/task_center_runner/tests/test_stores.py
backend/src/task_center_runner/tests/test_sweevo_adapter_lock.py
backend/src/task_center_runner/tests/test_capacity_scenario_packs.py
backend/src/task_center_runner/tests/test_runner_imports.py
backend/src/task_center_runner/tests/test_scenario_suite_imports.py
```

Plus the (already-green) unit suite at `backend/tests/unit_test/`.

**Explicitly out of scope:**

- `tests/sweevo/test_real_agent.py` — gated by Daytona + LLM API keys.
- `entrypoints/__main__.py` CLI scaffolding from plan §2.
- `live_e2e/` shim removal — follow-up milestone after one release.
- 4 MOCK_* `EventType` removal — coupled with shim removal.

---

## 1. Acceptance Criteria

This phase passes when:

1. **Merge gate green:**
   ```
   cd backend && .venv/bin/pytest src/task_center_runner/tests/sweevo \
     -x -k "not test_real_agent" --no-header
   ```
   exits 0.

2. **Phase-1 unit posture preserved:** `backend/tests/unit_test` still reports
   1365+ passed / 1 skipped / 4 failed (the same 4 pre-existing failures
   carried since commit 66d70da1; no new regressions).

3. **Ruff clean:** `.venv/bin/ruff check backend/src/task_center_runner
   backend/src/live_e2e` returns clean.

4. **Structural-golden + no_core_imports + run_pipeline_smoke** in
   `backend/tests/unit_test/test_task_center_runner/` continue to pass — no
   regression in the invariants the restructure baked in.

5. **No new `live_e2e.performance_report.v1` references** introduced.
   `grep -rn "live_e2e.performance_report.v1" backend/ docs/ scripts/` shows
   only inert references (test asserts comparing the v2 schema, etc.).

6. **No real-agent test regressions** — the real-agent test file
   (`test_real_agent.py`) continues to be deselected via `-k "not
   test_real_agent"` and does not need to pass.

---

## 2. Environmental Pre-Flight (Step 0)

Before any test run, verify PG availability. The mock suite needs
`EPHEMERALOS_DATABASE_URL` set to a reachable PostgreSQL DSN.

```
.venv/bin/python -c "
import os
from db.engine import initialize_db, get_engine
assert os.environ.get('EPHEMERALOS_DATABASE_URL'), 'EPHEMERALOS_DATABASE_URL unset'
initialize_db()
print('PG OK:', get_engine())
"
```

If the env var is unset, source `.env` (the project root has one with
`EPHEMERALOS_DATABASE_URL=postgresql+psycopg://ephemeralos:ephemeralos@localhost:5432/ephemeralos`).
If PG itself is unreachable, escalate — this phase cannot proceed without
PG. No code changes will fix that.

---

## 3. Risk Inventory + Triage Playbook

The restructure landed without the mock suite as a verification gate. Here
are the most likely failure modes and the corresponding playbook for each.
Run the merge gate command from §1 first; if it exits 0, skip this section
entirely and proceed to Step 5 (sign-off).

### Risk A — `report.events` membership drift

**Symptom:** A test assertion using `Counter(event.type for event in
report.events)` or `any(event.type == EventType.X for event in
report.events)` fails because new `MOCK_*` event types appear in
`report.events`.

**Where the change came from:** Phase 4e/4g routes the audit bus through
`ScenarioLifecycle.captured_events`, which records every event the bus
publishes — including the 4 new `MOCK_*` types added in Phase 2. The
legacy `run_scenario` collected events the same way (`captured_events`
list in the inline `_on_event`), but the bus never published `MOCK_*`
types before Phase 4d.

**Mitigation:** Quick grep before running:

```
grep -rn 'EventType\.MOCK_\|"mock_launch\|"mock_tool_call\|"mock_prompt\|"mock_sandbox_check' \
  backend/src/task_center_runner/tests/ backend/tests/
```

If any test code explicitly asserts on MOCK_* types, that's expected
behavior and stays. If a test asserts an EXACT count of `report.events`
or checks `absent_events` against `MOCK_*`, edit the test (those weren't
real "absent" assertions because the type didn't exist before).

**Test inspection — already done:**
- `test_focused_scenarios.py:309: counts = Counter(event.type for event
  in report.events)` — uses `min_event_counts` and `absent_events` filters
  by type; MOCK_* additions don't break it.
- `test_full_case_user_input.py:113-117`: filters by `PLANNER_PARTIAL_PLAN`
  and `VERIFIER_FAILURE`; MOCK_* additions don't break it.

**Acceptable resolution paths:**
1. Test passes as-is → no change.
2. Test's `min_event_counts` insufficient → bump count + add a comment
   pointing to Phase 4d.
3. Test's `absent_events` includes a MOCK_* type → genuinely buggy assertion
   that was broken intent; either drop or invert.

### Risk B — `ScenarioLifecycle` payload reconstruction loss

**Symptom:** A test asserts on `report.launches[0].some_field` /
`report.tool_calls[0].metadata['key']` and the field comes back wrong
(empty dict, missing key, wrong type).

**Where the change came from:** Phase 4d publishes MOCK_* events via
`dataclasses.asdict(record)` at the publish site (in
`MockSquadRunner._publish_mock_record`). Phase 4g.1
(`ScenarioLifecycle.on_event`) reconstructs the dataclass via
`LaunchRecord(**event.payload)`, etc. This roundtrip is lossless for the
flat dataclasses (`LaunchRecord`, `ToolCallRecord`, `PromptInspection`)
but `SandboxCheck.changed_paths` is a `tuple`, and the lifecycle
defensively coerces `payload["changed_paths"] = tuple(cp) if not
isinstance(cp, tuple) else cp`.

**Mitigation:** Already handled. If a test fails because
`changed_paths` is a list instead of a tuple, that's the bug — fix the
defensive coercion (currently in `scenarios/lifecycle.py`).

**Other roundtrip suspects:**
- `ToolCallRecord.metadata: dict[str, Any]` — should roundtrip fine; Python
  dicts pass through `asdict` and back unchanged.
- `PromptInspection.checks: dict[str, bool]` — same.
- `PromptInspection.passed` — computed property; `asdict` correctly omits
  it. Reconstruction works via `PromptInspection(**payload)` because
  `passed` is not an init arg.

### Risk C — Bundle / stores ownership in the shim

**Symptom:** Test fails with "session closed" or "table does not exist" after
the run finishes, when the shim tries to compute `_graph_summary` against
the bundle.

**Where the change came from:** Phase 4e shim passes `config.stores =
bundle` so `run_pipeline` sees `owns_stores = False` and does NOT close
the bundle on the way out. The shim then computes `_graph_summary` and
closes the bundle itself if `owns_stores` was originally true.

**Mitigation:** Verify the engine's `finally` block in
`task_center_runner/core/engine.py` honors `owns_stores`. The current
implementation:

```python
finally:
    await config.sandbox.release(lease)
    recorder.dispose()
    if owns_stores:
        bundle.close()
    lifecycle_unsub()
```

`bundle.close()` only fires when `config.stores is None`, which is the
non-shim path. Shim passes a non-None bundle, so close is the shim's
responsibility. Looks correct; if a test fails on bundle access after
the run, this is the place to look.

### Risk D — `registered_mock_agents` scope vs runner construction

**Symptom:** Test fails with "agent not registered" or "no factory for
agent_kind: planner".

**Where the change came from:** Phase 4e shim wraps `await
run_pipeline(config)` in `with registered_mock_agents()`. The engine
constructs `MockSquadRunner` via `config.runner_factory(ctx)` AFTER bus +
recorder setup but BEFORE `start_task_center_entry_run`. The `with` block
must enclose `start_task_center_entry_run`, which is inside the engine,
inside `await run_pipeline`. ✓ Already the case.

**Mitigation:** No action needed unless a test specifically fails for
"agent not registered". If it does, the fix is to push the
`registered_mock_agents` context manager INSIDE the runner_factory closure
in `build_scenario_config`, around the `MockSquadRunner` construction. But
that would change the registration LIFETIME from per-run to
per-factory-call, which is incorrect.

### Risk E — `pipeline_run` fixture not picked up

**Symptom:** Test fails with "fixture 'pipeline_run' not found" when a
test tries to use it.

**Where the change came from:** The fixture lives in
`task_center_runner/core/fixtures.py`. The conftest at
`backend/src/task_center_runner/tests/conftest.py` declares
`pytest_plugins = ["task_center_runner.core.fixtures"]` (per Phase
5-step3).

**Mitigation:** Pre-flight check:
```
.venv/bin/pytest src/task_center_runner/tests/sweevo --collect-only \
  -k "test_focused_scenarios" 2>&1 | head
```
If collection works, the fixture path is correct.

### Risk F — `run_dir` path scheme change

**Symptom:** A test asserts the on-disk directory structure of audit
artifacts (`scenario_logs/<name>/<utc>_<id>/`) and fails because the
path doesn't match.

**Where the change came from:** Plan locked decision #8 — every mode
uses `audit_dir/<run_label>/<utc>_<self_id>`. Mock mode's `run_label` is
`scenario_logs/<scenario.name>` (set in `build_scenario_config`). So the
final path is `audit_dir/scenario_logs/<name>/<utc>_<id>/` — same as
before. ✓

**Mitigation:** No action expected; if a test fails on path scheme, the
fix is to align `build_scenario_config`'s `run_label` with what tests
expect. Currently: `f"scenario_logs/{scenario.name}"`. Matches the
legacy.

### Risk G — Unfinished `performance_report_task` warnings

**Symptom:** Test passes but emits `Task was destroyed but it is pending!`
warnings at teardown. May or may not fail depending on filterwarnings
configuration.

**Where the change came from:** Phase 3 makes `run_scenario` return a
`RunReport.performance_report_task: asyncio.Task | None`. Tests that don't
await it leak the task. The `pipeline_run` fixture exists for exactly
this purpose but tests must use it.

**Mitigation:** Check pyproject `filterwarnings` — `pyproject.toml` has
none for asyncio warnings. So the warnings don't fail tests (they're
informational). If they do fail (e.g., a `-W error` flag was added), the
fix is to thread `pipeline_run` through each test via the fixture, OR add
a per-test `await report.performance_report_task` if non-None.

Bulk-fix template:

```python
async def test_X(audit_dir, stores, sweevo_sandbox, pipeline_run):
    report = await run_scenario(scenario, sandbox_id=..., ...)
    pipeline_run(report)  # auto-awaits at teardown
    ...
```

---

## 4. Execution Plan

### Step 1 — Pre-flight (5 min)

```
# Set PG env if not already
source .env

# Confirm PG reachable
.venv/bin/python -c "from db.engine import initialize_db; initialize_db(); print('PG OK')"

# Confirm collection works
.venv/bin/pytest src/task_center_runner/tests/sweevo --collect-only \
  -k "not test_real_agent" --no-header 2>&1 | tail -5
```

If collection fails (e.g., import error), fix imports before running the
suite. The Phase 1 shim should handle all `from live_e2e.*` imports
through `sys.modules` aliasing.

### Step 2 — Run the merge gate (15–60 min, depending on hardware)

```
cd backend && .venv/bin/pytest src/task_center_runner/tests/sweevo \
  -x -k "not test_real_agent" --no-header
```

`-x` stops at the first failure so each issue gets attention serially.

### Step 3 — Triage each failure

Apply the playbook from §3 by symptom. Each failure becomes a single
targeted commit. Pattern per failure:

1. Identify which Risk (A–G) the failure matches.
2. Apply the documented fix (test edit or source edit per playbook).
3. Re-run JUST that test (`pytest <path::name> -v`) to confirm fix.
4. Re-run the suite to check no regression.
5. Commit with explicit pathspec.

If a failure does NOT match any Risk in §3:
- Capture the full traceback verbatim in the commit message.
- Add a new "Risk H" entry to this plan with the symptom and root cause.
- Then fix.

### Step 4 — Run the broader gate (10 min)

```
.venv/bin/pytest backend/src/task_center_runner/tests -x \
  -k "not test_real_agent" --no-header
```

Catches the PG-only file-level tests (`test_stores.py`,
`test_sweevo_adapter_lock.py`, etc.) that aren't under `sweevo/`.

### Step 5 — Acceptance run + sign-off

Once §1 criteria all hold:
- Final clean run of the merge gate.
- Final clean run of `backend/tests/unit_test`.
- Final `ruff check`.
- Update `.omc/progress.txt` to record Phase 2 status + the commit range
  that landed.
- (Optional) Open a single Phase-2 PR collecting the triage commits.

---

## 5. Codex-Parallel Hygiene Reminder

Per project memory `feedback_parallel_user_commits.md`:

- Stage with explicit pathspec only (`git commit -m '...' -- <paths>`),
  never `git add <dir>`.
- Re-check HEAD between commits — codex may land parallel commits during
  the session.
- The `.omc/` directory is gitignored; progress.txt updates do not need
  to be staged.

---

## 6. Open Decisions (Inherit Locks)

All locked decisions from the parent plan
(`.omc/plans/task_center_runner-restructure.md` §10.1) remain in force:

1. Package name `task_center_runner` is canonical; `live_e2e/` is a
   silent shim.
2. Unified workflow via `run_pipeline(config)`.
3. Default `SandboxProvisioner.release()` destroys best-effort;
   `AttachExisting.release()` is no-op.
4. Schema string is `task_center_runner.performance_report.v2` (Phase 3
   bumped it).
5. `run_dir` is `audit_dir/<run_label>/<utc>_<self_id>` for all modes.
6. Shim is silent (no `DeprecationWarning`).
7. Migration pass condition is the §1 merge gate.

This Phase-2 plan does not modify those locks. Any departure must
escalate.

---

## 7. Handoff Brief

**Branch state at handoff:**
- `codex/fix-dot-path-normalization-tests`
- 19 commits ahead of the parent point: b7c5f286 → 7e5d910d
- Unit suite: 1365 passed, 1 skipped, 4 pre-existing failures
- Ruff clean
- PRD: `.omc/prd.json` — P1/P2/P3 marked `passes:true`; P4 listed as
  substantively done with deferred 4g; P5 substantively done with §2 and
  §6 inconsistency flagged.

**Next-iteration first move:**
1. `source .env` (or set `EPHEMERALOS_DATABASE_URL` manually) and verify
   PG is reachable.
2. Run the merge gate from §1.
3. If green: update progress.txt, no code changes needed. Phase 2 is a
   verification milestone.
4. If red: apply §3 playbook serially. Each failure → single commit.

**Estimated effort:** 1–4 hours if the suite is green or only needs
mechanical fixes (Risk A–G). Longer if a new Risk class (H, I, …) emerges
that wasn't anticipated.
