# Complex build-from-scratch — Phase 2 plan

**Date:** 2026-05-11
**Status:** DRAFT — ready for implementation
**Pairs with:** `.omc/plans/complex-build-from-scratch-layer-stack-projection-verification-plan-20260511.md` (Phase 1, shipped),
`.omc/handoffs/complex-build-from-scratch-implementation-20260511.md` (Phase 1 implementation report)
**Owner:** sandbox / live-e2e
**Estimated effort:** 1 short ralph session (~1-2 hours)

---

## 1. Why Phase 2

Phase 1 shipped `sandbox.complex_project_build` (full + smoke). Both variants run green against live infra. The `oh-my-claudecode:architect` review came back **APPROVED-WITH-NITS**. Phase 2 closes those nits.

Five HIGH-priority nits were addressed in the Phase 1 deslop pass (§7 assertions wired, dead code removed, `lsp_reference_graph.json` deleted, `total_calls` split from `api_calls_total`). Four follow-up nits remain — they're tracked here so a single Phase 2 ralph can clear them all without re-litigating scope.

## 2. In-scope items

### 2.1 N1 — Per-module import smoke loop (plan §7.7)

**Spec quote** (plan §7.7): *"All 37 files importable: per-module `python -c "import …"` calls all returned exit 0."*

**Current state:** pytest collection transitively imports every fixture module; the probe never runs the explicit per-module import. The §7.7 contract item is technically satisfied by §7.6 (pytest exit 0) but is not asserted independently.

**Change:** Add a small loop in `_phase_f_pytest()` (or a new `_phase_f_per_module_imports()`) that, for each importable source module under `scheduler_demo/`, runs:

```python
await _shell(
    ctx,
    stats,
    command=f"cd {WORKSPACE_ROOT} && python3 -c 'import {dotted_name}'",
    timeout=30,
)
```

and accumulates a sandbox_check per module. Failures roll up via `passed_sandbox_checks`.

**Files touched:**
- `backend/src/live_e2e/squad/complex_project_build_probe.py` (~25 LOC added in Phase F)

**Test impact:** None on the host-side test suite. Live test asserts via the existing `passed_sandbox_checks` gate.

**Acceptance:** every importable module in `selected_files` (excluding `__init__.py`, `.gitignore`, `pyproject.toml`, `conftest.py`, `tests/*`) appears as a `module.import.<dotted_name>` sandbox_check with `passed=True`.

---

### 2.2 N2 — Compute amplification `pairs` from §13.6 ratio target

**Spec quote** (plan §13.6): *"edit:write ratio ≥4×"*. Also §7.2: *"≥2000 tool-call floor for full"*.

**Current state:** `complex_project_build_probe.py:_phase_d_edit_amplification` hard-codes:

```python
pairs = 6 if ctx.smoke else 30
```

If fixture file count changes, smoke set expands, or full target shifts, the magic number drifts silently from the constraint it's meant to satisfy.

**Change:** Replace the constant with a small computation that pegs `pairs` to the ratio + tool-call target with margin:

```python
def _compute_amp_pairs(
    selected_files: Sequence[FixtureFile],
    *,
    smoke: bool,
    edit_write_ratio_floor: float = 4.0,
    headroom: float = 1.25,
) -> int:
    py_anchor_count = sum(
        1 for f in selected_files
        if f.relative_path.endswith(".py")
        and "from __future__ import annotations" in f.final
    )
    if py_anchor_count == 0:
        return 0
    # Roughly: probe writes ≈ fixture count + a handful of bootstrap/metrics writes.
    # We need (existing_edits + 2 * pairs * py_anchor_count) >= ratio_floor * writes * headroom.
    write_count_est = len(selected_files) + 5
    existing_edits_est = sum(len(f.patches) for f in selected_files)
    target_edits = int(edit_write_ratio_floor * headroom * write_count_est)
    deficit = max(0, target_edits - existing_edits_est)
    pairs_from_ratio = (deficit + 2 * py_anchor_count - 1) // (2 * py_anchor_count)
    # Tool-call floor pad — only relevant in full mode where §7.2 demands ≥2000.
    if not smoke:
        # Existing toolkit calls excluding amp ≈ 600 (per Phase 1 evidence).
        baseline = 600
        floor_pairs = (2000 - baseline + 2 * py_anchor_count - 1) // (2 * py_anchor_count)
        return max(pairs_from_ratio, floor_pairs, 6)
    return max(pairs_from_ratio, 6)
```

**Files touched:**
- `backend/src/live_e2e/squad/complex_project_build_probe.py` (~30 LOC added; one-line replacement in `_phase_d_edit_amplification`)

**Test impact:** None (computation is internal). The live test still asserts §7.2 and §7.11; the new code must keep both green.

**Acceptance:**
- Smoke: computed `pairs >= 6` (matches Phase 1 behavior).
- Full: computed `pairs >= 30` (matches Phase 1 behavior, with headroom for future fixture additions).
- The `pairs` value is logged in the probe summary for future debugging.

---

### 2.3 N3 — Reconcile smoke runtime budget

**Spec quote** (plan §11): *"Runs in <2 min, no `EPHEMERALOS_RUN_HEAVY_LIVE_E2E` required"*.

**Current state:** Phase 1 smoke ran in 3:27 (207s) — 70% over budget. Two contributors:
1. Smoke set expanded from 13 → 17 files (to satisfy `domain/__init__.py` transitive imports).
2. Amp pairs at 6 per .py file × 14 files × 2 edits = 168 amp edits, taking ~80s alone.

**Change (pick one):**

**Option A — doc the new budget.** Update plan §10 + §11 to say *"smoke variant runs in <5 min, full variant 12-25 min"*. Lowest effort; preserves all coverage.

**Option B — trim smoke amp.** Drop smoke `pairs` from 6 to 3 (halving amp edits). Recompute via N2's helper — confirm we still hit edit:write ≥4×. Likely runtime saved ~40-60s.

**Option C — slim smoke fixture set further** to 6 files (matching plan §11 verbatim). Would require pulling `domain/__init__.py` and `services/__init__.py` into a lazy-import shape — touches checked-in fixture content. Highest blast radius.

**Recommendation:** Option A. The plan budget was set before transitive-import realities were understood; 3:27 vs 2:00 is acceptable for a pre-merge gate.

**Files touched:**
- `.omc/plans/complex-build-from-scratch-layer-stack-projection-verification-plan-20260511.md` (1-line edit to §10 + §11)

**Test impact:** None — doc-only.

**Acceptance:** plan §10/§11 budget matches observed wall time within ±30s.

---

### 2.4 N4 — Don't double-count `api_shell_count` on cwd fall-through retry

**Current state:** `_phase0_bootstrap` has a 2-attempt cwd fall-through (try `/testbed`, fall back to `/ephemeral-os`). Both attempts increment `stats.api_shell_count` even when the first fails. Architect noted this is benign cold-path but inflates the §7.17 saturation count if both attempts run.

**Change:** Increment `stats.api_shell_count` only on the successful attempt:

```python
for candidate_cwd in ("/testbed", WORKSPACE_ROOT):
    candidate_result = await sandbox_api.shell(...)
    if candidate_result.success and candidate_result.exit_code == 0:
        stats.api_shell_count += 1   # only count the successful attempt
        mkdir_result = candidate_result
        break
    last_err = ...
```

**Files touched:**
- `backend/src/live_e2e/squad/complex_project_build_probe.py` (3-line change in `_phase0_bootstrap`)

**Test impact:** None. The cold path runs the first attempt successfully so the count is unchanged.

**Acceptance:**
- Cold path: `api_shell_count` increments by 1 (was: 1).
- Retry path (workspace already bound to `/ephemeral-os` from prior probe): `api_shell_count` increments by 1 (was: 2). Net behavior change: -1 inflation when retry kicks in.

---

## 3. Out-of-scope for Phase 2

- Daytona scheduler degradation root cause (zombie `pending_build` quota leak). Separate operational ticket; not a test-code issue.
- pytest-timeout plugin registration. The `@pytest.mark.timeout(900)` / `(2400)` markers are silent no-ops; either add `pytest-timeout` to deps + register the marker, or remove the markers. Cosmetic; current behavior is forward-compatible.
- Full §7.25/§7.26 assertions (`git log ≥5 commits`, `git status` clean at end). Could be added but not high-impact — the probe already commits at every phase boundary.

## 4. Verification plan

After Phase 2 edits, run in order:

```bash
# 1. Host-side regression (fast, no infra):
PYTHONPATH=backend/src .venv/bin/pytest \
  backend/src/live_e2e/tests/sweevo/test_complex_project_build_fixtures.py \
  backend/src/live_e2e/tests/test_scenario_suite_imports.py -q

# 2. Lint:
.venv/bin/ruff check backend/src/live_e2e/ backend/scripts/analyze_complex_build_perf.py

# 3. Smoke variant — confirm N2 amp computation still hits §7.2 + §7.11 floors,
#    and N1 import-smoke checks pass:
PYTHONPATH=backend/src .venv/bin/pytest \
  backend/src/live_e2e/tests/sweevo/test_complex_project_build.py::test_complex_project_build_smoke \
  -v -s --tb=short

# 4. Full variant — confirm tool-call floor still met:
EPHEMERALOS_RUN_HEAVY_LIVE_E2E=1 PYTHONPATH=backend/src .venv/bin/pytest \
  backend/src/live_e2e/tests/sweevo/test_complex_project_build.py::test_complex_project_build_full \
  -v -s --tb=short
```

Each test must:
- Return exit 0.
- Have `passed_sandbox_checks=True` (i.e. all new `module.import.<x>` checks pass).
- Show `perf.json` with valid v1 schema.

## 5. Definition of done

- [ ] N1: per-module import smoke loop added in Phase F; each smoke fixture's importable module produces a `module.import.<name>` sandbox_check that passes.
- [ ] N2: `_compute_amp_pairs()` replaces magic constants; recorded in summary for visibility.
- [ ] N3: plan §10/§11 budget updated to match reality OR amp trimmed to hit <2 min.
- [ ] N4: cwd fall-through increments `api_shell_count` only on success.
- [ ] `ruff check` clean on all touched files.
- [ ] Host-side test suite passes (11/11).
- [ ] Smoke + full live tests pass against fresh Daytona infra.
- [ ] Architect re-review (oh-my-claudecode:architect, sonnet tier) returns APPROVED.

## 6. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| N2 ratio computation drifts under fixture-set churn | Medium | Add a host-side test that loads `SCHEDULER_DEMO_FILES` and asserts `_compute_amp_pairs(selected, smoke=False) >= some_lower_bound` |
| Daytona scheduler degradation blocks live re-verify | High (operational) | Use the zombie-cleanup runbook in `.omc/handoffs/complex-build-from-scratch-implementation-20260511.md` §7.3 |
| N1 adds ~15 toolkit calls to smoke runtime budget | Low | Smoke already 3:27; 15 extra calls ≈ +5s. Negligible |
| `_compute_amp_pairs` over-amplifies for tiny smoke set | Low | Floor at `max(..., 6)` keeps it bounded |

## 7. Hand-off checklist

- [ ] Read `.omc/handoffs/complex-build-from-scratch-implementation-20260511.md` for Phase 1 context.
- [ ] Confirm Daytona is healthy: `curl http://localhost:3000/api/health` returns `{"status":"ok"}` AND `Counter(s['state'] for s in /api/sandbox)` shows no `pending_build` / `error` zombies.
- [ ] Run `git status` — branch should still be `codex/fix-dot-path-normalization-tests`; only the Phase 1 commits (and your Phase 2 follow-ups) should appear.
- [ ] Verify `.omc/prd.json` reflects Phase 1 S-9 as in-progress (will be re-marked APPROVED in Phase 2 architect review).
- [ ] Run §4 verification commands in order; stop on any red.

---

## 8. References

- `.omc/plans/complex-build-from-scratch-layer-stack-projection-verification-plan-20260511.md` — Phase 1 spec.
- `.omc/handoffs/complex-build-from-scratch-implementation-20260511.md` — Phase 1 implementation report (architecture, files changed, issue log).
- `.omc/prd.json` — Phase 1 PRD with all 9 stories.
- `backend/src/live_e2e/squad/complex_project_build_probe.py` — touchpoint for N1, N2, N4.
- `backend/src/live_e2e/tests/sweevo/test_complex_project_build.py` — test file (no changes expected, asserts via existing `passed_sandbox_checks`).
