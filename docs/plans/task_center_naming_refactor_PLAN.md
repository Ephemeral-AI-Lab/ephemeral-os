# TaskCenter naming/location refactor — `what_in_context`, `task_guidance/`, `task_state`

Status: PLAN (not yet executed). Author scope: rename/relocate three mis-named
TaskCenter modules with zero behavior change. Land as **one atomic commit when
the worktree is quiet** (concurrent agents edit this tree). Verify with
`.venv/bin/pytest`, never global pytest.

## Why these three are wrong

Sibling conventions, established by the surrounding code:

- `context_engine/` modules are all **nouns**: `packet.py`, `renderer.py`,
  `scope.py`, `tag_dictionary.py`, `role_directives.py`, `recipes_registry.py`,
  `core.py`. → `what_in_context.py` is the lone **phrase**.
- Multi-module concerns are **packages with descriptive module names**
  (`attempt/{deps,generator_dag,launch,orchestrator,stage_advancer,state}.py`).
  A package whose `__init__.py` "re-exports nothing" and holds a single
  `builders.py` is neither — it's a flat module wearing a package costume.
- Durable entity state lives in a **subpackage `state.py`**: `goal/state.py`,
  `iteration/state.py`, `attempt/state.py`. There is **no `task/` package** and
  **no `task_store.py`** — "task" is modeled as a `_core` primitive
  (`TaskRow = dict[str, Any]`, `TaskStoreProtocol` in `_core/persistence.py`).
  So a **top-level** `task_state.py` is doubly off: it neither follows the
  subpackage-`state.py` pattern nor sits with the `_core` task primitives, and
  it conflates two unrelated concerns (see Item 3).

| Item | Current | Problem |
|---|---|---|
| 1 | `context_engine/what_in_context.py` | phrase filename among noun siblings |
| 2 | `task_guidance/` (`__init__.py` + `builders.py`) | single-module package; vague `builders.py` |
| 3 | `task_state.py` (top level) | wrong layer; mixes task vocabulary + submission DTOs |

---

## Item 1 — `what_in_context.py` → `context_outline.py`

The module renders the deterministic "What's in context" outline for the
`<Task Guidance>` body. Rename to the noun it produces.

**Recommended**
- `context_engine/what_in_context.py` → `context_engine/context_outline.py`
- Function `render_what_in_context` → `render_context_outline` (also update
  `__all__` and module docstring). Low risk — only 3 files reference the symbol.
- The literal header string `"What's in context:"` in the guidance builder
  **stays** (it is user-facing prose, not tied to the module name) — so no
  behavior link is lost by the rename.

**Alternative (smaller):** rename the file only, keep `render_what_in_context`.
Rejected as the default because the function name carries the same phrase smell.

**Edits**
- `git mv` the module; rename the function + `__all__` + docstring inside it.
- Importer: `task_guidance/builders.py` (the one real import; line ~28).
- Test mirror: `test_context_engine/test_what_in_context.py` →
  `test_context_outline.py` (import + ~10 call sites).

---

## Item 2 — collapse `task_guidance/` to a flat module

`task_guidance/__init__.py` is docstring-only ("re-exports nothing");
`builders.py` holds `build_task_guidance` + `build_explorer_task_guidance`.
Collapse the package to one flat module and drop the empty `__init__.py`.

**Layering (verified):** `task_guidance` *imports from* `context_engine`
(`role_directives`, `context_outline`) and is *imported by* `agent_launch`,
`tools/subagent`, and a smoke script. The `context_engine/recipes/{generator,
planner}.py` references are **docstring mentions only — not real imports**, so
they impose no layering constraint. Direction is:
`context_engine → task_guidance → agent_launch`.

**Recommended home:** `context_engine/task_guidance.py`. Its only dependencies
(`role_directives`, `context_outline`) already live in `context_engine`, so this
groups the three "render the guidance body" modules together with no new
cross-package edges. Confirm the lazy-facade cycle guard still holds after the
move with `python -c "import task_center"` (the package documents a known
import-cycle sensitivity).

**Alternative (minimal):** flat top-level `task_center/task_guidance.py` — a
pure package→module collapse, no relocation. Pick this if grouping under
`context_engine` feels like scope creep on review.

**Edits (real imports only)**
- `agent_launch/task_guidance_dispatch.py` (~line 19)
- `tools/subagent/run_subagent/run_subagent.py` (~line 223)
- `backend/scripts/smoke_two_user_message.py` (~line 47)
- Test mirror: `test_task_center/test_task_guidance/test_builders.py` → move to
  match new path (e.g. `test_context_engine/test_task_guidance.py`).
- Docstring path mentions to update (not imports): `recipes/generator.py`,
  `recipes/planner.py`, `task_center_runner/scenarios/pipeline/initial_messages_capture.py`,
  and the self-path line in the moved module's docstring.
- The `KeyError` message in the builder points at `role_directives.py` (which is
  **not** moving) — leave it unchanged.

---

## Item 3 — split `task_state.py` by layer

`task_state.py` holds two unrelated things:

1. **Task vocabulary (internal):** `TaskCenterTaskRole`, `SpawnReason`,
   `TaskCenterTaskStatus`, `TERMINAL_GENERATOR_STATUSES`. Describes the
   `TaskRow` that already lives in `_core/persistence.py`. **Never re-exported**
   by the package root; imported only via direct module path, all within
   `task_center` (`_core/invariants.py`, `goal/*`, `attempt/*`).
2. **Submission DTOs (public contract):** `PlannedGeneratorTask`,
   `PlannerSubmission`, `PlannerFailureSubmission`, `GeneratorSubmission`,
   `EvaluatorSubmission`. The tools↔TaskCenter terminal-outcome contract.
   **Already re-exported via the `task_center` root facade** — the entire
   `tools/submission/*` layer imports them as `from task_center import
   GeneratorSubmission`, etc.

The re-export asymmetry (DTOs in `_EXPORTS`, enums absent) and the layering
constraint (`_core/invariants.py` imports `TaskCenterTaskRole`, and `_core`
cannot depend upward) both point to the same split:

**Recommended**
- Enums → **`_core/task_state.py`** (keeps the accurate name; sits beside
  `_core/persistence.py`'s `TaskRow`/`TaskStoreProtocol`).
- DTOs → **`task_center/submissions.py`** (top level — public surface).
- Delete `task_center/task_state.py`.

**Alternative (minimal):** move the file wholesale to `_core/task_state.py`,
unsplit. Keeps every import single-line, but drags the **public** submission
contract into `_core` and points the root facade at a `_core` path — wrong
altitude for a tools-facing contract. Offered only as the low-effort floor.

**Edits**
- Facade `task_center/__init__.py`: repoint the 4 DTO entries in `_EXPORTS`
  (`EvaluatorSubmission`, `GeneratorSubmission`, `PlannedGeneratorTask`,
  `PlannerSubmission`) and the `TYPE_CHECKING` block (lines ~61–66) from
  `task_center.task_state` → `task_center.submissions`. **This is the edit that
  silently breaks external consumers if missed.** (`PlannerFailureSubmission`
  is not in `_EXPORTS` today — leave that as-is.)
- `tools/submission/*`: **no changes** — they go through the root facade.
- Internal enum/DTO importers — repoint, splitting combined imports into two
  lines where a file pulls from both groups:

  | File | imports | after split |
  |---|---|---|
  | `_core/invariants.py` | role | `_core.task_state` |
  | `goal/closure_report_router.py` | status | `_core.task_state` |
  | `goal/starter.py` | status | `_core.task_state` |
  | `attempt/deps.py` | role, status | `_core.task_state` |
  | `attempt/generator_dag.py` | status (+DTO?) | `_core.task_state` (+`submissions`) |
  | `attempt/launch.py` | DTOs + enums | both |
  | `attempt/orchestrator.py` | DTOs + enums | both |
  | `attempt/orchestrator_registry.py` | DTOs + enums (TYPE_CHECKING) | both |
  | `attempt/stage_advancer.py` | `SpawnReason` + … | both |

  (Verify each file's exact symbol set at edit time; several attempt files mix
  both groups and need two import lines.)
- Tests — repoint 10 files that do `from task_center.task_state import …`
  (several mix both groups: `test_attempt_orchestrator.py`,
  `test_phase04_deferred_retry.py`, `test_integration_phase02.py`, …). Prefer
  routing test imports through the public root (`from task_center import
  PlannerSubmission`) where they only need DTOs, to reduce future churn.

---

## Execution order

1. Item 1 (self-contained, 3 files).
2. Item 2 (depends on Item 1 if `render_*` renamed — the moved builder imports it).
3. Item 3 (independent of 1 & 2).
4. Sweep `docs/architecture/*.html` for stale module paths — CLAUDE.md treats
   these as the curated memory layer. Candidates found:
   `task_center/context-engine.html`, `agent-roles.html`, `lifecycle.html`,
   `attempt-harness.html`, `tools/subagent.html`, `tools/submission.html`,
   `agent_loops/prompt-context.html`, plus `assets/search-index.js`. Update only
   the pages whose `data-evidence-paths` actually name a moved module.

## Verification

- `python -c "import task_center"` — facade + cycle guard intact.
- `grep -rn "what_in_context\|task_guidance/builders\|task_center\.task_state" backend --include='*.py'`
  returns nothing (excluding `__pycache__`).
- `.venv/bin/ruff check backend/src/task_center backend/src/tools`
- `.venv/bin/pytest backend/tests/unit_test/test_task_center backend/tests/unit_test/test_tools -q`
- Run `backend/scripts/smoke_two_user_message.py` if it's part of the smoke set.

## Out of scope (noted, not touched)

- `task_store: TaskCenterStore` is a misleading parameter name (it's the whole
  store, not a task-specific one) — separate cleanup, leave as a note.
