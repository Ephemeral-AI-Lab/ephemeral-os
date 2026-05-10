---
title: "Live E2E Scenarios — Full Migration (delete the live_test shim)"
tags: ["live-e2e", "scenarios-migration", "follow-up", "shim-removal"]
created: 2026-05-10T13:30:00.000Z
updated: 2026-05-10T13:37:56.000Z
sources: ["live-e2e-testing-framework-design.md"]
links: ["live-e2e-testing-framework-design.md"]
category: decision
confidence: high
schemaVersion: 1
---

# Live E2E Scenarios — Full Migration (delete the live_test shim)

_Drafted 2026-05-10. Sequel to_ `live-e2e-testing-framework-design.md` _which lifted the framework to_ `backend/src/live_e2e/` _and left_ `backend/src/benchmarks/sweevo/live_test/` _as a re-export shim. This document records the work required to **physically remove the shim** and consolidate every scenario + test file under_ `live_e2e/`. _No new framework features — purely structural cleanup + import-path migration._

## TL;DR

**Move every SWE-EVO-coupled scenario test out of `benchmarks/sweevo/live_test/tests/` into `live_e2e/tests/sweevo/`. Move the SWE-EVO entry-point helper (the `run_sweevo_scenario` shim that builds the entry prompt) into `live_e2e/sweevo_adapter.py`. Repoint every external importer at the new paths. Delete the entire `benchmarks/sweevo/live_test/` sub-tree (35 files of shim).**

After this PR:

- `backend/src/benchmarks/sweevo/` contains only the **dataset glue** (`dataset.py`, `models.py`, `prompt.py`, `sandbox.py`, `evaluation.py`, `__main__.py`, `__init__.py`) — that's the pre-shim spec contract: dataset-specific code stays where it is.
- `backend/src/live_e2e/` owns the framework AND the SWE-EVO test/adapter glue that consumes the framework.
- The old `benchmarks/sweevo/live_test/` import paths are **gone**. Any external code that still imports from there is updated.

## Why now

The shim approach in the previous migration (`live-e2e-testing-framework-design.md`) was deliberately transitional — it kept legacy import paths green so that:

1. The migration could ship without coordinating with every consumer in one PR.
2. The risk of import-path churn was bounded.
3. External codebases (if any pulled this as a library) had time to migrate.

That cushion is no longer needed:

- Every internal consumer is now known and enumerated (see "Inventory" below).
- The shim is a pure pass-through — keeping it is dead-mass that obscures the real architecture.
- New contributors hit two parallel module trees with the same names, which is confusing.

## Inventory — what currently uses the shim

External importers of `benchmarks.sweevo.live_test.*` (paths that must be updated):

| Consumer | Imports | New target |
|---|---|---|
| `backend/tests/unit_test/test_benchmarks/test_sweevo_audit_recorder.py` | `live_test.audit.{bus,events,node_id,recorder}`, `live_test.stores` | `live_e2e.audit.*`, `live_e2e.stores` |
| `backend/tests/unit_test/test_benchmarks/test_sweevo_sandbox_event_monitor.py` | `live_test.audit.{bus,events,node_id,recorder,stream_bridge}` | `live_e2e.audit.*` |
| `backend/tests/unit_test/test_benchmarks/test_sweevo_mock_agent_execution.py` | `live_test.runner.run_scenario`, `live_test.scenarios.correctness_testing.CorrectnessTesting`, `live_test.stores` | `live_e2e.sweevo_adapter.run_sweevo_scenario`, `live_e2e.scenarios.correctness_testing`, `live_e2e.stores` |
| `backend/src/benchmarks/sweevo/__main__.py` | `live_test.runner.run_scenario`, `live_test.scenarios.SCENARIO_REGISTRY` | `live_e2e.sweevo_adapter.run_sweevo_scenario`, `live_e2e.scenarios.SCENARIO_REGISTRY` |
| `backend/src/benchmarks/sweevo/live_test/tests/test_*.py` | (lives inside the shim — moved/deleted in this PR, see Phase B) | `live_e2e/tests/sweevo/test_*.py` |
| `backend/tests/live_e2e_test/_tools/tiers.toml` (tier 7 `sweevo_mock_framework`) | path-string `backend/src/benchmarks/sweevo/live_test/tests/` | `backend/src/live_e2e/tests/sweevo/` |

The shim sub-tree itself (35 files at `backend/src/benchmarks/sweevo/live_test/`) is then deleted in one operation.

## Destination layout (post-migration)

```
backend/src/benchmarks/sweevo/
  __init__.py
  __main__.py                       # entry point — repointed at live_e2e.sweevo_adapter
  dataset.py                        # unchanged — SWE-EVO dataset glue
  evaluation.py                     # unchanged
  models.py                         # unchanged — SWEEvoInstance, _REPO_DIR, ...
  prompt.py                         # unchanged — build_sweevo_user_prompt
  sandbox.py                        # unchanged — create_sweevo_test_sandbox, reset_sweevo_workspace

backend/src/live_e2e/
  __init__.py
  audit/                            # framework-internal — unchanged
  fixtures.py                       # unchanged — dataset-agnostic fixtures
  hooks/
  runner.py                         # unchanged — generic run_scenario(...)
  scenarios/                        # unchanged — CorrectnessTesting et al.
  squad/
  stores.py                         # unchanged — per-test PG schema
  sweevo_adapter.py                 # NEW — run_sweevo_scenario() + sweevo fixtures
  tests/
    __init__.py
    conftest.py                     # pytest_plugins = ["live_e2e.fixtures"]
    test_runner_imports.py          # offline wiring tests
    test_stores.py                  # PG round-trip tests
    sweevo/                         # NEW — SWE-EVO live tests under live_e2e/
      __init__.py
      conftest.py                   # imports SWE-EVO fixtures from live_e2e.sweevo_adapter
      test_correctness.py           # ← moved from benchmarks/sweevo/live_test/tests/
      test_correctness_via_live_e2e.py
      test_full_case_user_input.py
      test_full_stack_adversarial.py
```

The `live_e2e/sweevo_adapter.py` module is the new public surface for SWE-EVO consumers — it provides:

- `run_sweevo_scenario(scenario, *, instance, sandbox_id, audit_dir, ...)` — builds the SWE-EVO entry prompt and delegates to `live_e2e.run_scenario`.
- `sweevo_instance`, `sweevo_sandbox`, `workspace` pytest fixtures. `live_e2e/tests/sweevo/conftest.py` imports these fixtures directly from the adapter so they stay scoped to the SWE-EVO tests.

The dataset-specific modules (`dataset.py`, `models.py`, `prompt.py`, `sandbox.py`, `evaluation.py`) **stay** under `benchmarks/sweevo/` per the original spec contract — they are the dataset, not the framework. `live_e2e/sweevo_adapter.py` imports from them.

## Phased plan

Six stories, strict serial. Each story has acceptance criteria you can verify with grep + pytest.

### Phase A — Stand up the new SWE-EVO surface inside live_e2e

**S-1: `live_e2e/sweevo_adapter.py` + `live_e2e/tests/sweevo/conftest.py`**

- New file `backend/src/live_e2e/sweevo_adapter.py` exposing:
  - `async def run_sweevo_scenario(scenario, *, instance, sandbox_id, audit_dir, stores=None, repo_dir=_REPO_DIR, extra_hooks=(), user_prompt=None) -> RunReport` — same body as the current `benchmarks/sweevo/live_test/runner.py` shim (calls `build_sweevo_user_prompt(...)` and delegates to `live_e2e.run_scenario`).
  - Pytest fixtures `sweevo_instance` (session-scoped, reads `EOS_SWEEVO_INSTANCE`), `sweevo_sandbox` (session-scoped, calls `create_sweevo_test_sandbox`), `workspace` (per-test reset via session cache key).
- New file `backend/src/live_e2e/tests/sweevo/__init__.py` (empty).
- Keep `backend/src/live_e2e/tests/conftest.py` loading only `pytest_plugins = ["live_e2e.fixtures"]`. Add `backend/src/live_e2e/tests/sweevo/conftest.py` that imports `sweevo_instance`, `sweevo_sandbox`, and `workspace` from `live_e2e.sweevo_adapter`; this pytest version rejects nested `pytest_plugins`.
- Smoke: `.venv/bin/python -c "from live_e2e.sweevo_adapter import run_sweevo_scenario; from live_e2e.sweevo_adapter import sweevo_instance, sweevo_sandbox, workspace"` exits 0.
- `.venv/bin/ruff check backend/src/live_e2e/sweevo_adapter.py backend/src/live_e2e/tests/sweevo/` exits 0.

### Phase B — Move SWE-EVO test files out of the shim

**S-2: Move 4 test files into `live_e2e/tests/sweevo/`**

- Move (`git mv` to preserve history):
  - `backend/src/benchmarks/sweevo/live_test/tests/test_correctness.py` → `backend/src/live_e2e/tests/sweevo/test_correctness.py`
  - `backend/src/benchmarks/sweevo/live_test/tests/test_correctness_via_live_e2e.py` → `backend/src/live_e2e/tests/sweevo/test_correctness_via_live_e2e.py`
  - `backend/src/benchmarks/sweevo/live_test/tests/test_full_case_user_input.py` → `backend/src/live_e2e/tests/sweevo/test_full_case_user_input.py`
  - `backend/src/benchmarks/sweevo/live_test/tests/test_full_stack_adversarial.py` → `backend/src/live_e2e/tests/sweevo/test_full_stack_adversarial.py`
- Inside each moved file, rewrite imports:
  - `from benchmarks.sweevo.live_test.runner import run_scenario` → `from live_e2e.sweevo_adapter import run_sweevo_scenario`
  - `from benchmarks.sweevo.live_test.audit.<x> import …` → `from live_e2e.audit.<x> import …`
  - `from benchmarks.sweevo.live_test.hooks.<x> import …` → `from live_e2e.hooks.<x> import …`
  - `from benchmarks.sweevo.live_test.scenarios.<x> import …` → `from live_e2e.scenarios.<x> import …`
  - `from benchmarks.sweevo.live_test.stores import create_in_memory_task_center_stores` → `from live_e2e.stores import create_per_test_task_center_stores`.
- Delete `backend/src/benchmarks/sweevo/live_test/tests/` entirely (including `__init__.py`, `conftest.py`).
- Pytest collection: `.venv/bin/pytest backend/src/live_e2e/tests --collect-only -q` exits 0 (17 tests).
- Live verification: `.venv/bin/pytest backend/src/live_e2e/tests/sweevo/test_correctness.py -q` PASSES against real Daytona (uses the same warm-sandbox path the existing run uses).
- `.venv/bin/ruff check backend/src/live_e2e/tests/` exits 0.

### Phase C — Repoint external importers

**S-3: Update unit_test/test_benchmarks/ imports**

- `backend/tests/unit_test/test_benchmarks/test_sweevo_audit_recorder.py`:
  - Replace `from benchmarks.sweevo.live_test.audit.<x>` → `from live_e2e.audit.<x>` (4 lines).
  - Replace `from benchmarks.sweevo.live_test.stores import (TaskCenterStoreBundle, create_in_memory_task_center_stores)` → `from live_e2e.stores import TaskCenterStoreBundle, create_per_test_task_center_stores`.
- `backend/tests/unit_test/test_benchmarks/test_sweevo_sandbox_event_monitor.py`:
  - Replace `from benchmarks.sweevo.live_test.audit.<x>` → `from live_e2e.audit.<x>` (5 lines).
- `backend/tests/unit_test/test_benchmarks/test_sweevo_mock_agent_execution.py`:
  - Replace `from benchmarks.sweevo.live_test.runner import run_scenario` → `from live_e2e.sweevo_adapter import run_sweevo_scenario`.
  - Replace `from benchmarks.sweevo.live_test.scenarios.correctness_testing import ...` → `from live_e2e.scenarios.correctness_testing import ...`.
  - Replace `from benchmarks.sweevo.live_test.stores import ...` → `from live_e2e.stores import create_per_test_task_center_stores`.
- `backend/src/benchmarks/sweevo/__main__.py`:
  - Replace `from benchmarks.sweevo.live_test.runner import run_scenario` → `from live_e2e.sweevo_adapter import run_sweevo_scenario`.
  - Replace `from benchmarks.sweevo.live_test.scenarios import SCENARIO_REGISTRY` → `from live_e2e.scenarios import SCENARIO_REGISTRY`.
  - Update docstrings + the help-text path reference (`backend/src/benchmarks/sweevo/live_test/tests/` → `backend/src/live_e2e/tests/sweevo/`).
- Verify: `.venv/bin/pytest backend/tests/unit_test/test_benchmarks -q` exits 0 (27 passed regression floor).
- `.venv/bin/python -m benchmarks.sweevo --help` works (no import errors).
- `.venv/bin/ruff check` exits 0 on all touched files.

### Phase D — Delete the shim

**S-4: Remove `backend/src/benchmarks/sweevo/live_test/` entirely**

- Confirm no remaining importers via grep:
  ```bash
  grep -rln "benchmarks\.sweevo\.live_test" backend/ --include="*.py"
  # Expected: empty (excluding the shim itself which is about to be deleted).
  ```
- Delete the whole sub-tree:
  ```bash
  git rm -r backend/src/benchmarks/sweevo/live_test/
  ```
- That removes 35 files (the 30 shim source files + 5 deleted-test stubs from S-2).
- After deletion: `find backend/src/benchmarks/sweevo -type d` shows only the top-level `sweevo/` dir.
- Smoke: `.venv/bin/python -c "import benchmarks.sweevo"` exits 0 (the package still exists, just without the live_test sub-package).
- Smoke: `.venv/bin/python -c "from benchmarks.sweevo.live_test import runner"` raises `ModuleNotFoundError` (proof of removal).

### Phase E — Update tier 7 + docs

**S-5: Repoint `tiers.toml` tier 7 + housekeeping**

- `backend/tests/live_e2e_test/_tools/tiers.toml`:
  - Replace `pytest_args = ["backend/src/benchmarks/sweevo/live_test/tests/", "-q"]` → `pytest_args = ["backend/src/live_e2e/tests/sweevo/", "-q"]`.
  - Optionally rename tier 7 from `sweevo_mock_framework` → `live_e2e_sweevo` for clarity. (If renamed, audit any tier-id consumers — `run_tiered.py` uses ids, not names, so this is safe.)
- Tier loads cleanly: `.venv/bin/python -m backend.tests.live_e2e_test._tools.run_tiered --tier 7 --help` exits 0.
- `docs/wiki/live-e2e-testing-framework-design.md`:
  - Update the "Source — current location" + "Hand-off API for SWE-EVO consumers" sections with a note that the shim was deleted in this follow-up. Add a back-reference to this document.
- `docs/wiki/index.md` — add an entry for this document under the "decision" section.

### Phase F — Final verification + reviewer signoff

**S-6: Architect signoff + ai-slop-cleaner + post-deslop regression**

- Reviewer: `oh-my-claudecode:architect` (Opus tier — touches 6+ files including production entry points and tier config). Review against the per-story acceptance criteria.
- `Skill('ai-slop-cleaner')` runs on the changed-file set (live_e2e/sweevo_adapter.py, live_e2e/tests/sweevo/, the 4 unit-test files, `benchmarks/sweevo/__main__.py`, `tiers.toml`).
- Post-deslop regression:
  - `.venv/bin/pytest backend/src/live_e2e/tests --collect-only -q` exits 0.
  - `.venv/bin/pytest backend/tests/unit_test/test_benchmarks -q` exits 0.
  - `.venv/bin/pytest backend/src/live_e2e/tests/sweevo/test_correctness.py -q` PASSES against real Daytona.
- `.venv/bin/ruff check backend/src/live_e2e/ backend/src/benchmarks/sweevo/ backend/tests/unit_test/test_benchmarks/` exits 0.

## Risk + rollback

| Risk | Likelihood | Mitigation |
|---|---|---|
| External codebase pulls `benchmarks.sweevo.live_test` as a dependency | Low — internal repo, no external consumers known | Phase B keeps tests passing through aliased imports; Phase D is the irreversible step. If a consumer surfaces post-merge, restore via revert + reintroduce a slim shim file. |
| Tier 7 path change breaks CI | Low — tiers.toml is single source of truth | Phase E updates tier 7 in the same PR; no out-of-band CI references. |
| Import-path churn breaks an unindexed call site | Medium | Phase D's grep gate + the `.venv/bin/pytest backend/tests/unit_test/test_benchmarks` regression catches missed imports. |
| `__main__.py` SCENARIO_REGISTRY change | Low | The registry contract is unchanged; only the import path moves. Smoke test via `python -m benchmarks.sweevo --help`. |
| Renaming tier 7 from `sweevo_mock_framework` → `live_e2e_sweevo` | N/A if not renamed | Optional change — keep the old name unless explicitly asked. |

Rollback strategy: this is a sequence of `git mv` + import rewrites + a final `git rm -r`. Any individual story can be reverted with `git revert`. The framework code in `live_e2e/` is untouched — the rollback surface is the test glue + four import-rewrite files + tiers.toml.

## Out of scope

- Renaming the `.sweevo_runs/` artifact root or the `EOS_SWEEVO_*` env vars. The original spec parked this in "Open questions"; the recommendation there (dual-read with `EOS_E2E_*` precedence) is a separate follow-up.
- Generalizing `live_e2e/sweevo_adapter.py` into a multi-dataset adapter pattern (e.g. an `lsp_adapter.py`, `arc_adapter.py`). One adapter per dataset is fine for now.
- Physical relocation of `benchmarks/sweevo/{dataset,models,prompt,sandbox,evaluation}.py` into `live_e2e/datasets/sweevo/`. The original spec explicitly leaves these in `benchmarks/sweevo/` and this follow-up preserves that decision.

## Cross-references
- [[live-e2e-testing-framework-design]] — the source migration this completes.
- [[task-center-pipeline]] — what the framework asserts on; unchanged by this PR.
- [[sandbox-subsystem]] — what the framework drives; unchanged by this PR.
