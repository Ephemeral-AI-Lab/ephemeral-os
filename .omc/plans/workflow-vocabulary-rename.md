# Plan: Reframe TaskCenter durable model "Goal" → "WorkflowGoal" (behavior-preserving rename)

> **PENDING USER REVISION (2026-05-29) — being integrated:**
> 1. **Axis rename target is `WorkflowGoal`, NOT `Workflow`.** Replace `Goal` →
>    `WorkflowGoal` (so `WorkflowGoalStarter`, `WorkflowGoalStatus`,
>    `WorkflowGoalLifecycle`, `WorkflowGoalRecord`, `nested_workflow_goal_depth`,
>    etc.). Every "→ Workflow*" rename below should read "→ WorkflowGoal*".
> 2. **Remove `statement` vocabulary** (user finds it confusing). The
>    context-engine `ContextBlockKind.GOAL_STATEMENT`/`ITERATION_STATEMENT`
>    (members + values `"goal_statement"`/`"iteration_statement"`),
>    `_goal_statement_block`, and the renderer `_TAG_MAP`/`_DEFAULT_TAGS` keys are
>    renamed to a non-"statement" word. This is a SECOND rename axis, scoped
>    below. The rendered XML tags (`<goal>`, `<iteration_goal>`) are unaffected.

## Context

The TaskCenter durable model is **Goal → Iteration → Attempt**. The user wants the
top axis reframed to **Workflow** vocabulary ("nested workflow rather than nested
goal"), motivated by workflow semantics being more intuitive. This is a
**behavior-preserving semantic rename only**. NO new dynamic-workflow / DAG /
authoring capability. Any behavioral change is out of scope and recorded only as
optional follow-ups.

## The organizing principle (the single discriminator)

For every `goal` occurrence, ask one question:

> Does this string get **persisted**, **serialized into tool/audit metadata**,
> **string-matched by a test or the mock runner**, or **rendered to the LLM as
> work-statement content**?

- **YES → KEEP `goal`.** It is part of the storage / contract / work-statement
  layer. (table name `goals`, enum *values*, metadata keys, XML tags, the `goal`
  work-statement field, tool names, arg names, audit-event values, `goal_id`.)
- **NO — it is a pure in-process Python symbol naming the *axis* → RENAME to
  Workflow.** (`Goal` DTO, `GoalStatus`, `GoalOrigin(Kind)`, `GoalClosureReport*`,
  `GoalStarter`/`StartedGoal`, `GoalLifecycle`, `GoalClosureReportRouter`,
  `nested_goal_depth`, `GoalStoreProtocol`, `GoalStore`, `GoalRecord` class,
  the `goal/` package directory.)

This generalizes the smallest-safe default ("keep the table name, rename the
symbol") into one reviewable rule. The two registers of the word `goal`:

- **Axis register** = the top level of the durable model → becomes **Workflow**.
- **Work-statement register** = the literal "what to accomplish" text
  (`<goal>`, `<iteration_goal>`, `deferred_goal_for_next_iteration`, the
  `Goal.goal: str` field, the `goal_handoff` arg) → stays `goal`. The user's own
  sentence confirms this split: "submit_execution_handoff to handoff complex
  **goal** for **workflow** execution" — goal is the work-statement noun, workflow
  is the axis.

## Verified ground-truth facts (re-confirmed against the checkout)

1. **Cross-package *import* ripple is small but NOT one file; one method-name
   ripple widens it further.** Production modules outside
   `backend/src/task_center/` that *import* `task_center.goal`:
   `backend/src/db/stores/goal_store.py`. Cross-package *tests* that import
   `task_center.goal.state` directly: at least
   `test_tools/test_submission_terminal_routing.py:7` (`GoalStatus`) — so the
   earlier "ONLY one cross-package importer" claim was wrong; it is low-risk
   because the `task_center.goal → 0` grep backstops any miss, but the count is
   not one. The `GoalRecord` class additionally has production consumers in
   `db/models/__init__.py` (re-export) and
   `task_center_runner/audit/recorder.py` (type at :102/295/300/552) — see #3.
   Other `goal` substrings in `backend/src/sandbox/`, `backend/src/engine/` are
   metadata-key strings (`task_center_goal_id`), NOT imports, and are KEEP.
   **Method-name widening:** `start_delegated_goal` lives on the submission
   context (`tools/submission/context/executor.py:71`), called by the handoff
   tool (`submit_execution_handoff.py:82`) — renaming it reaches into
   `tools/submission/` (Phase 4b). (Imports stay nearly contained; method-name
   renames do not.)
2. **Iteration/attempt own no cross-import to `goal/` beyond their normal
   coupling** — they import `task_center.goal.state` symbols (`Goal`, etc.)
   inside `task_center/`, which the rename handles.
3. **`goal_id` is in the KEEP bucket.** It is a `ScopeField` literal
   (`Literal["goal_id", ...]`), is string-looked-up via
   `scope.require_field("goal_id")` in `recipes/planner.py` and
   `recipes/generator.py`, is a serialized metadata key in the handoff tool and
   closure report, is pinned in test fixtures (`goal_id="recursive-goal"`,
   `goal_id="goal-id"`), and is the id column of the `goals` table. No consumer
   forces `goal_id`→`workflow_id`. Renaming the attribute while keeping the
   `"goal_id"` key would only create a translation seam. **Keep `goal_id`
   verbatim everywhere** (attribute + string).
4. **`_nested_goal_depth_gt_1` has two lockstep consumers:**
   `test_domain/test_ancestry.py` (imports the symbol) and
   `test_agent_launch/test_terminal_tool_router.py` (monkeypatches it by full
   module path `task_center._core.terminal_tool_routing._nested_goal_depth_gt_1`,
   5 call sites). Any rename of this symbol must update both in the same commit.
5. **Tag dictionary pins work-statement tags.**
   `test_context_engine/test_tag_dictionary.py` asserts `goal`, `iteration_goal`,
   `deferred_goal_for_next_iteration` entries — all KEEP.
6. **DB has no Alembic.** Tables via `Base.metadata.create_all` in
   `backend/src/db/engine.py`; legacy-drop precedent exists
   (`_LEGACY_TABLES_TO_DROP`, `init_db_with_legacy_check`,
   `test_migration_drops_legacy_table.py`). We do NOT touch the table. NOTE:
   `test_migration_drops_legacy_table.py::test_initialize_db_drops_legacy_attempt_table`
   drops a legacy `task_center_attempt` table and only incidentally asserts
   `"goals" in tables` (normal-creation check) — it passes identically whether or
   not we rename `GoalRecord`, so it is a **vacuous guard for A1**, kept only as a
   generic regression check. **The real A1 guard is `test_goal_store.py` (round-
   trips the `goals` table) plus the `grep '"goals"'` sanity check.**
7. **The delegated/waiting-workflow method cluster** (the conceptual heart of
   "nested workflow") — verified locations and decisions:
   - `WAITING_GOAL = "waiting_goal"` (`_core/task_state.py:30`). Its `.value` is
     **persisted** and string-compared in `attempt/deps.py` (2),
     `attempt/orchestrator.py` (2), `goal/closure_report_router.py` (1),
     `goal/starter.py` (1) and asserted in ~7 test sites
     (`test_submission_terminal_routing.py`, `test_lifecycle/test_phase04_*`,
     `test_attempt_orchestrator.py`). **KEEP the value** `"waiting_goal"`; the
     enum *member name* `WAITING_GOAL` rename is OPTIONAL (deferred).
   - `apply_goal_closure_report` (in-process method) — full consumer set:
     protocol `attempt/orchestrator_registry.py:33`, `attempt/deps.py:128/136`,
     `attempt/orchestrator.py:166`, `goal/closure_report_router.py:72`, and tests
     `test_lifecycle/test_phase04_close_report_delivery.py` (2),
     `test_lifecycle/test_attempt_orchestrator.py` (2). RENAME →
     `apply_workflow_closure_report` (lockstep across all 8).
   - `parent_task_for_delegated_goal`, `mark_waiting_goal`,
     `restore_running_after_failed_goal_start`, `delegated_goal_id` (param) —
     all on `attempt/deps.py` (parent-task handle protocol + impl), consumed by
     the moved `starter.py` / `closure_report_router.py`. In-process methods →
     RENAME to `*_workflow` forms (see decisions table). KEEP the `"goal_id"`
     metadata key written from `delegated_goal_id` (`deps.py:150`).
   - `start_delegated_goal(goal_handoff=...)` — submission-context method
     (`tools/submission/context/executor.py:71`) called by the handoff tool
     (`submit_execution_handoff.py:82`). In-process method → RENAME →
     `start_delegated_workflow`; KEEP the `goal_handoff` arg. This is the one
     rename that reaches into `tools/submission/` (Phase 4b).

## Plan assumptions (adopted; do not re-litigate)

- **A1** Keep `goals` DB table name and all persisted string VALUES
  (`status`: open/succeeded/failed/cancelled; `origin_kind`: entry/task;
  `final_outcome` keys; `submission_kind="goal_start"`). Rename Python symbols
  only.
- **A2** Keep tool names `submit_execution_handoff`, `submit_plan_closes_goal`,
  `submit_plan_defers_goal` and the `goal_handoff` arg name (all contract-tested).
  Rewrite only docstrings / prompt prose to workflow semantics.
- **A3** Package move: `backend/src/task_center/goal/` →
  `backend/src/task_center/workflow/`. **Iteration and attempt stay where they
  are** (smaller move — see Option A tradeoff). They remain the sub-axes a
  Workflow owns.
- **A4** Keep `goal_id` token everywhere (attribute + string), per fact #3.
- **A5** Keep audit-event *values* (`"goal_started"`, `"planner_full_plan"`,
  etc.). EventType member-name renames are OPTIONAL (see Phase 6).
- **A6 (internal-helper scope rule)** PascalCase axis *types* and the
  delegation-cluster *methods* are renamed in this pass (gated). Two pure-internal
  axis-typed dataclasses are also renamed because they are gated-clean:
  `_PreparedGoalOrigin`→`_PreparedWorkflowOrigin`,
  `AttemptDelegatedGoalParentTask`→`AttemptDelegatedWorkflowParentTask`. The
  remaining internal helper *methods/functions* carrying the goal axis stem are
  **DEFERRED to optional follow-up** (cosmetic, no contract, would inflate the
  diff): `list_for_goal` (`persistence.py:111`),
  `child_outcomes_for_goal` (`generator_summaries.py:211`),
  `assert_iteration_id_unique_in_goal` (`invariants.py:30`),
  `_build_goal_lifecycle` (`starter.py:146`). The grep gates in Acceptance
  deliberately do NOT include these four — gating on a deferred symbol would
  produce a false failure.

## Execution notes (recommended, not mandated)

- **Prefer LSP-driven rename for the PascalCase axis types.** Run `lsp_rename`
  (or `mcp__cclsp__rename_symbol`) per axis symbol (`Goal`, `GoalStatus`,
  `GoalStarter`, `GoalRecord`, `GoalStore`, …). LSP follows the call graph and
  auto-updates ALL importers — it would have caught the
  `audit/recorder.py` / `db/models/__init__.py` / `conftest.py` consumers that a
  text grep can miss. Use manual edits only for the package-move path rewrites and
  the prose. Verify with the grep sanity checks afterward.
- **Renames vs. KEEP strings:** LSP rename touches symbols only, so it will NOT
  disturb the KEEP string literals (`"goals"`, `"<goal>"`, `"goal_closure_report"`,
  `"goal_start"`, `goal_handoff`) — exactly the safety property we want.

---

## Plan (phased, ordered)

**Commit atomicity (important — no green intermediate exists mid-rename).** A
Python symbol + package rename breaks every importer at once; there is NO
compiling/test-passing tree between Phase 1 and Phase 4b. Therefore:

- **Phases 1–4b are ONE uncommitted sweep.** Do them contiguously; the tree is
  expected RED throughout. **The single commit happens only after Phase 4b is
  green** (full importer + test set updated). Do not commit between these phases.
- **Phases 5–7** (prose, docs, verification) can be a **second commit**.
- This contiguous sweep is ALSO the correct concurrent-agent mitigation: one fast
  uninterrupted pass minimizes the RED window versus committing piecemeal.

Sequencing principle: package move first as an atomic rename (import graph breaks
loudly), then ripple identifier renames outward across all importers, then
prose/docs, then verify. Stage with explicit file paths only (never `git add
<dir>`); verify your changeset against HEAD before each commit because other
agents edit concurrently.

### Phase 0 — Baseline & safety net (no edits)

- Capture current green baseline for the affected suites so we can diff:
  - `.venv/bin/pytest backend/tests/unit_test/test_task_center -q`
  - `.venv/bin/pytest backend/src/task_center_runner/tests/mock/contracts -q`
- Record the KEEP-string inventory greps (for the acceptance gate later):
  - `grep -rn '"goals"\|<goal>\|goal_id\|goal_handoff\|goal_start' backend/src`
- **Verify:** baseline suites green; note any pre-existing failures from parallel
  agents so they are not attributed to this work.

### Phase 1 — Package move + internal axis-symbol rename (atomic)

**Files/dirs:**
- `git mv backend/src/task_center/goal/` → `backend/src/task_center/workflow/`
  (move `__init__.py`, `state.py`, `ancestry.py`, `lifecycle.py`,
  `starter.py`, `closure_report_router.py`).
- **Add the two-register convention** to `workflow/__init__.py` and the top of
  `workflow/state.py`: a short docstring stating "The durable axis is **Workflow**
  (Workflow → Iteration → Attempt). The tokens `goal` / `goal_id` / `goals` /
  `<goal>` are the stable **work-statement** and **storage/contract** layer and
  intentionally retain the legacy noun — do NOT rename them for consistency."
  This makes the discriminator self-documenting at the package boundary so future
  editors don't "fix" the KEEPs.
- Inside the moved package, rename axis symbols (KEEP work-statement `goal`
  field, `goal_id`, the `goal` arg/param names that carry work-statement text):
  - `state.py`: `Goal`→`Workflow`, `GoalStatus`→`WorkflowStatus`,
    `GoalOrigin`→`WorkflowOrigin`, `GoalOriginKind`→`WorkflowOriginKind`,
    `GoalClosureReport`→`WorkflowClosureReport`,
    `GoalClosureDeliveryResult`→`WorkflowClosureDeliveryResult`,
    `GoalClosureDeliveryStatus`→`WorkflowClosureDeliveryStatus`.
    KEEP: the `goal: str` work-statement field on the DTO; enum *values*
    `"entry"/"task"/"open"/...`; `to_final_outcome()` keys.
  - `starter.py`: `GoalStarter`→`WorkflowStarter`, `StartedGoal`→`StartedWorkflow`,
    and the pure-internal dataclass `_PreparedGoalOrigin`→`_PreparedWorkflowOrigin`
    (:312, RENAME per #6 — included in the PascalCase gate).
    Update delegation-cluster call sites to the renamed parent-task handle
    methods: `parent_task_for_delegated_goal`→`parent_task_for_delegated_workflow`,
    `mark_waiting_goal`→`mark_waiting_workflow`,
    `restore_running_after_failed_goal_start`→`restore_running_after_failed_workflow_start`,
    `delegated_goal_id=`→`delegated_workflow_id=` (kwarg to the handle).
    KEEP method/param names that carry work-statement text (`prompt`, the
    `goal` text variables); KEEP the `goal_id=` kwarg where it feeds the KEEP
    `goal_id` metadata/scope; KEEP `WAITING_GOAL` member reference (value stays).
    KEEP the internal helper name `_build_goal_lifecycle` (:146, deferred per #6).
  - `lifecycle.py`: `GoalLifecycle`→`WorkflowLifecycle`,
    `GoalClosureCallback`→`WorkflowClosureCallback`; method names
    `create_goal`→`create_workflow`, `close_goal`→`close_workflow`,
    `_require_goal`→`_require_workflow`. KEEP `create_deferred_iteration_*`,
    `deferred_goal_for_next_iteration` (work-statement), `goal_id` params.
  - `closure_report_router.py`: `GoalClosureReportRouter`→`WorkflowClosureReportRouter`;
    update call sites `parent_task_for_delegated_goal`→`*_workflow` and
    `apply_goal_closure_report`→`apply_workflow_closure_report`. KEEP
    `WAITING_GOAL.value` comparison.
  - `ancestry.py`: `nested_goal_depth`→`nested_workflow_depth` (and its
    `__all__`). KEEP the cycle-detection messages' wording is cosmetic; update
    to "workflow" prose.
- Update intra-package imports to the new `task_center.workflow.*` paths.

**Verify (intra-package only; tree stays RED until the 1–4b sweep completes):**
- `.venv/bin/python -c "import task_center.workflow.state"` succeeds.
- `.venv/bin/ruff check backend/src/task_center/workflow`
- The tree will NOT fully import yet (consumers still reference old paths) —
  expected; Phases 2–4b close it. Do NOT run the full suite or commit mid-phase.

### Phase 2 — Ripple consumers inside `task_center/` + the one cross-package store

**Files (import-path + symbol updates only):**
- `backend/src/task_center/__init__.py` — re-export paths.
- `backend/src/task_center/_core/persistence.py` —
  `GoalStoreProtocol`→`WorkflowStoreProtocol`; import `Workflow`/`WorkflowOrigin`/
  `WorkflowStatus` from `task_center.workflow.state`; KEEP `goal_id` param names
  on protocol methods, KEEP the `goal:` work-statement param.
- `backend/src/task_center/_core/terminal_tool_routing.py` — import
  `nested_workflow_depth`; rename `_nested_goal_depth_gt_1`→
  `_nested_workflow_depth_gt_1` and `_depth`'s docstring; KEEP `scope.goal_id`
  reads, KEEP terminal-tool name strings (`submit_plan_closes_goal`, etc.).
- `backend/src/task_center/_core/invariants.py` — rename `assert_goal_open`→
  `assert_workflow_open` and the `Goal`-typed signatures.
  `assert_predecessor_has_deferred_goal_for_next_iteration` KEEPS its name
  (work-statement). `assert_iteration_id_unique_in_goal` (:30) is DEFERRED per #6
  (internal helper carrying the axis stem) — KEEP its name; update only its
  `Goal`→`Workflow` type annotation. Update `Goal`→`Workflow` type imports.
- `backend/src/task_center/entry/bootstrap.py` — `GoalStarter`→`WorkflowStarter`,
  `GoalOrigin`→`WorkflowOrigin` usages.
- `backend/src/task_center/iteration/state.py` + `attempt_coordinator.py` — KEEP
  `goal_id` field + `goal` work-statement field; update only `Goal` type-import
  references if any (verify; iteration owns `goal_id` as a KEEP id).
- `backend/src/task_center/attempt/deps.py`, `launch.py`,
  `orchestrator_registry.py` — update `Goal*` axis-symbol imports. Rename the
  pure-internal axis dataclass `AttemptDelegatedGoalParentTask`→
  `AttemptDelegatedWorkflowParentTask` (`deps.py:120`, RENAME per #6; constructed
  at `deps.py:111`, returned at `:107`, in the module docstring `:4` — included
  in the PascalCase gate). Rename the in-process delegation-cluster methods (NOT
  serialized) on the parent-task handle protocol + impl (`deps.py`,
  `orchestrator_registry.py`): `apply_goal_closure_report`→
  `apply_workflow_closure_report` (consumes a `WorkflowClosureReport`),
  `parent_task_for_delegated_goal`→`parent_task_for_delegated_workflow`,
  `mark_waiting_goal`→`mark_waiting_workflow`,
  `restore_running_after_failed_goal_start`→
  `restore_running_after_failed_workflow_start`, param `delegated_goal_id`→
  `delegated_workflow_id`. (`orchestrator.py`'s rename of `apply_goal_closure_report`
  + the **CRITICAL** `"goal_closure_report"` string KEEP is covered by its own
  bullet above.) **KEEP**: the `"goal_id"` metadata key emitted at `deps.py:150`
  (written from `delegated_workflow_id`), `goal_id=` at `launch.py:397`, and
  every `WAITING_GOAL.value` comparison (value stays; member name kept).
- `backend/src/task_center/context_engine/recipes/iterations.py` — update
  `Goal`→`Workflow` type import + the `goal: Workflow` param of
  `goal_iteration_blocks`. **KEEP** function name `goal_iteration_blocks`
  (cosmetic, internal — optional rename deferred), **KEEP** `<goal>` tag,
  `source_kind="goal"`, `metadata={"tag":"goal"}`, `<iteration_goal>`,
  `(identical to <goal>)`.
- `backend/src/task_center/context_engine/recipes/planner.py`,
  `generator.py`, `iterations.py` — KEEP `require_field("goal_id")`,
  `_REQUIRED_FIELDS` literals, `scope.goal_id`. Update only `deps.goal_store`
  attribute IF the deps attribute is renamed (see ContextEngineDeps below).
- `backend/src/task_center/context_engine/core.py` /
  `ContextEngineDeps` — `goal_store` attribute: it names the axis store. Decision:
  rename attribute `goal_store`→`workflow_store` for axis consistency, OR keep
  it. **Default: KEEP `goal_store` attribute name** to minimize ripple across
  every recipe (`deps.goal_store.get(...)`) — it is internal, but renaming it
  touches ~6 recipe files for zero contract gain. Record as optional follow-up.
- `backend/src/db/stores/goal_store.py` (cross-package): update imports to
  `task_center.workflow.state`; rename `GoalStore`→`WorkflowStore`; KEEP all
  `GoalRecord` ORM usage, `.value` enum writes, the `goal=` column write,
  `goal_id` params. (File itself may stay named `goal_store.py` — file rename is
  optional cosmetic; default KEEP filename to avoid churn, rename the class only.)
- `backend/src/db/models/goal.py` — rename class `GoalRecord`→`WorkflowRecord`;
  **KEEP** `__tablename__ = "goals"`, the `goal` Text column, all column names.
  (File itself may stay `goal.py`; default KEEP filename.)
- `backend/src/db/models/__init__.py` — update the re-export
  `from db.models.goal import GoalRecord` (line 4) and `__all__` entry (line 17)
  `GoalRecord`→`WorkflowRecord`. (Production re-export facade; `recorder.py`
  imports the class via `db.models.goal`, but other code may import via the
  package root — update both for safety.)
- `backend/src/task_center/attempt/orchestrator.py` — the in-process method
  `apply_goal_closure_report`→`apply_workflow_closure_report` (defined at :166).
  **CRITICAL KEEP:** the serialized payload at :204-205 stays verbatim —
  `"goal_closure_report": asdict(report)` (payload key) and
  `"submission_kind": "goal_closure_report"` (value). The method renames; the
  string does NOT. (It shares a stem with the method but is a mock/test contract
  read at `runner.py:1968` — see decisions table.) KEEP `child_outcomes_for_goal`
  call (:226, deferred per #6) and `report.goal_id`.
- Update every `from task_center.goal...` importer found in the import scan:
  `_core/persistence.py`, `_core/terminal_tool_routing.py`, `_core/invariants.py`,
  `entry/bootstrap.py`, `context_engine/recipes/iterations.py`,
  `attempt/{deps,orchestrator_registry,launch,orchestrator}.py`,
  `db/stores/goal_store.py`.

**Verify:**
- `.venv/bin/ruff check backend/src/task_center backend/src/db`
- `.venv/bin/pytest backend/tests/unit_test/test_task_center/test_domain -q`
  (after Phase 3 test updates these go green; if running here, expect the
  test-import failures for renamed symbols — fix in Phase 3).
- Sanity import: `.venv/bin/python -c "import task_center; import db.stores.goal_store"`.

### Phase 3 — Update tests that import axis symbols (lockstep)

**Files (16 importers; the load-bearing ones):**
- `backend/tests/unit_test/test_task_center/test_domain/test_goal_dto.py` —
  `Goal`/`GoalStatus`/`GoalOrigin` → `Workflow*`. (Optional file rename to
  `test_workflow_dto.py`; default KEEP filename to ease review.)
- `backend/tests/unit_test/test_task_center/test_domain/test_ancestry.py` —
  import `nested_workflow_depth`, `_nested_workflow_depth_gt_1`.
- `backend/tests/unit_test/test_task_center/test_agent_launch/test_terminal_tool_router.py`
  — update 5 monkeypatch target strings to
  `...terminal_tool_routing._nested_workflow_depth_gt_1`.
- `backend/tests/unit_test/test_task_center/conftest.py` and other
  `test_context_engine/*` / `test_agent_launch/*` fixtures referencing
  `GoalStore`/`Goal`/`GoalStarter` → `Workflow*`. KEEP all `<goal>`, `goal_id`,
  `goal=` fixture data.
- Any `submission_test_utils.py` / `test_tools/*` that constructs `Goal` or
  `GoalStarter` → `Workflow*`. KEEP tool-name and `goal_handoff` assertions.
- `backend/tests/unit_test/test_task_center/test_persistence/test_goal_store.py`
  (:7-9 imports `Goal`,`GoalStatus`) → `Workflow*`. This test round-trips the
  `goals` table and is the REAL A1 guard (proves the table/values survived) —
  must be in this phase and pass. (Optional file rename deferred.)
- `backend/tests/unit_test/test_task_center/test_context_engine/test_role_context_matches_diagram.py`
  (:41 imports `Goal`,`GoalOriginKind`,`GoalStatus`) → `Workflow*`. KEEP `<goal>`
  / `goal=` diagram fixture data.
- `backend/tests/unit_test/test_tools/conftest.py` (:21 imports `GoalStore` from
  `db.stores.goal_store`; fixture `goal_store` at :62-63) → `WorkflowStore`. KEEP
  fixture name `goal_store` if other tests reference it (verify), or rename
  consistently.
- `backend/tests/unit_test/test_benchmarks/test_sweevo_audit_recorder.py` (:31
  imports `GoalStore`, :39 `GoalStatus`, :52/:73 usage) → `Workflow*`. NOTE:
  `test_benchmarks/` is its own suite — add it to the Phase 7 sweep.
- `backend/tests/unit_test/test_tools/test_submission_terminal_routing.py` (:7
  `from task_center.goal.state import GoalStatus`) → `WorkflowStatus`. KEEP the
  `WAITING_GOAL.value` and `payload["goal_closure_report"]` assertions (:350).
- `backend/tests/unit_test/test_task_center/test_lifecycle/test_phase04_close_report_delivery.py`
  (2 sites) and `test_lifecycle/test_attempt_orchestrator.py` (2 sites) — update
  `orchestrator.apply_goal_closure_report(...)`→`apply_workflow_closure_report`.
  KEEP all `WAITING_GOAL.value` assertions (~7 sites across `test_lifecycle/*`,
  `test_submission_terminal_routing.py`). Also update any `GoalClosureReport`
  construction in these files → `WorkflowClosureReport`.

**Verify:**
- `.venv/bin/pytest backend/tests/unit_test/test_task_center -q` GREEN
  (includes `test_lifecycle/*`).
- `.venv/bin/pytest backend/tests/unit_test/test_tools -q` GREEN.

### Phase 4 — Mock runner / task_center_runner ripple (string-match safe)

**Files:**
- `backend/src/task_center_runner/audit/recorder.py` (PRODUCTION — must be in
  this phase or the `test_correctness.py` gate below goes RED): update
  `from db.models.goal import GoalRecord` (:35) and the `GoalRecord` type
  annotations at :102 (`_serialize_goal`), :295, :300, :552 (`_handle_goal`) →
  `WorkflowRecord`. KEEP the serialized audit-event keys/values it emits and the
  `_serialize_goal`/`_handle_goal` method names (internal; deferred). Update the
  module docstring mention at :4.
- `backend/src/task_center_runner/agent/mock/runner.py` — update only axis-symbol
  imports/usages (`runtime.goal_store.get(...)` follows the ContextEngineDeps
  decision — KEEP if attribute kept; if renamed, update here too). **KEEP**
  every prompt string-match: `"<goal>" in prompt`,
  `"submit_plan_closes_goal" in prompt`, `"submit_plan_defers_goal" ...`; KEEP
  `"goal_handoff"`, `"goal_id"` dict keys, `"request_recursive_goal:"` token,
  `recursive_handoff_goal(ctx)` (work-statement), `task_center_goal_id`, and the
  `payload.get("goal_closure_report")` read at :1968 (the string is a KEEP
  contract even though the method that writes it is renamed — see #5).
- `backend/src/task_center_runner/agent/mock/probes.py`,
  `scenario_adapter.py`, `scenario_loop_runner.py` — same rule: KEEP all
  string-matched / metadata literals; update only direct axis-symbol imports.
- `backend/src/task_center_runner/core/{runner,engine,stores}.py` — update
  `Goal*` axis-symbol imports if present; KEEP `goal_id`/store-key strings.
- `backend/src/task_center_runner/tests/mock/contracts/test_runner_imports.py`,
  `test_advisor_gate_wiring.py`, `_project_build_contracts.py` — **KEEP**
  `"submit_execution_handoff"`, `goal_id="recursive-goal"` fixtures; update only
  if they import a renamed axis symbol.

**Verify:**
- `.venv/bin/pytest backend/src/task_center_runner/tests/mock/contracts -q` GREEN.
- `.venv/bin/pytest backend/src/task_center_runner/tests/mock/task_center/test_correctness.py -q` GREEN.

### Phase 4b — Submission-context delegation method (the one tools/submission touch)

This is the only `tools/submission/` *code* touch (the rest of Phase 5 is prose).
The submission-context method `start_delegated_goal` is the call surface for
"hand off a complex goal for workflow execution".

**Files:**
- `backend/src/tools/submission/context/executor.py` — rename method
  `start_delegated_goal`→`start_delegated_workflow` (definition + the `:meth:`
  docstring cross-reference at line 25). KEEP arg `goal_handoff`.
- `backend/src/tools/submission/executor/submit_execution_handoff/submit_execution_handoff.py`
  — update the call `submission_context.start_delegated_goal(goal_handoff=...)`→
  `start_delegated_workflow(goal_handoff=...)`. KEEP everything else (tool name,
  arg, metadata keys, `submission_kind="goal_start"`).
- Any test in `backend/tests/unit_test/test_tools/` that calls or stubs
  `start_delegated_goal` (grep to confirm) → `start_delegated_workflow`.

**Verify:**
- `.venv/bin/pytest backend/tests/unit_test/test_tools -q` GREEN
  (submission terminal-routing + helper tools exercise this path).
- `grep -rn 'start_delegated_goal' backend/src backend/tests | grep -v __pycache__`
  → 0.

### Phase 5 — Prose / docstring / prompt vocabulary

**Files (human-facing text only; zero behavior change):**
- `backend/src/tools/submission/executor/submit_execution_handoff/prompt.py` and
  `submit_execution_handoff.py` docstring (lines 5-8) — reframe to "hand off a
  complex goal for **workflow** execution". KEEP arg `goal_handoff`, metadata
  `submission_kind="goal_start"`, keys `goal_id`/`initial_iteration_id`/
  `initial_attempt_id`.
- `backend/src/tools/_terminals/registry.py` descriptions — optional prose touch
  for `submit_execution_handoff` only ("starts a delegated workflow"). KEEP all
  `name=` strings and the `<iteration_goal>` references in
  `submit_plan_*` descriptions (work-statement).
- Package/module docstrings in the moved `workflow/*.py` files: "Goal package" →
  "Workflow package", "origin axis of harness work" → "origin axis (Workflow)".
- `backend/src/db/models/goal.py` module docstring — "Goal persistence model" →
  "Workflow persistence model"; note the table stays `goals` for compatibility.

**Verify:**
- `.venv/bin/ruff check backend/src/tools backend/src/db`
- `.venv/bin/pytest backend/tests/unit_test/test_tools -q` GREEN
  (prompt/registry contract tests must still pass).

### Phase 6 — Architecture docs refresh + CLAUDE.md memory note

**Files:**
- `docs/architecture/task_center/*.html` (index, lifecycle, bridges,
  context-engine, agent-roles, terminal-tools, attempt-harness, maintenance) and
  `docs/architecture/index.html`: update the durable-model prose to
  **Workflow → Iteration → Attempt**, rename axis-symbol mentions
  (`GoalStarter`→`WorkflowStarter`, etc.), and update each touched page's
  `data-last-reviewed-commit` to the rename commit and `data-evidence-paths` to
  the new `task_center/workflow/...` anchors. KEEP doc references to the `goals`
  table, `<goal>`/`<iteration_goal>` tags, and tool names (note they are stable
  contracts).
- `CLAUDE.md` (project) — update the durable-model sentence ("Goal -> Iteration
  -> Attempt") and the anchor list (`backend/src/task_center/goal/state.py` →
  `.../workflow/state.py`; `submit_execution_handoff` and `GoalStarter.start`
  mentions → `WorkflowStarter.start`). KEEP the table/contract-string notes.
- `.omc/plans/open-questions.md` — append open questions (see below).

**Optional (record as follow-up, do NOT do unless asked):**
- EventType member renames (`GOAL_STARTED`→`WORKFLOW_STARTED`, etc.) — values
  stay; member renames ripple into `scenarios/*.py` and 6 mock test files for
  cosmetic gain.
- `ContextEngineDeps.goal_store`→`workflow_store` attribute rename.
- `goal_iteration_blocks`→`workflow_iteration_blocks` function rename.
- Physical table rename `goals`→`workflows` + `goal` column rename + migration
  via the `engine.py` legacy-drop precedent (data-migration blast radius; zero
  conceptual gain now).

**Verify:**
- `grep -rn "Goal -> Iteration\|GoalStarter\|nested_goal_depth" docs CLAUDE.md`
  returns zero (except intentional historical-background note in
  `docs/task_center_harness_and_context_engine.html`, which CLAUDE.md flags as
  stale-comparison material).

### Phase 7 — Full verification sweep

- `.venv/bin/pytest backend/tests/unit_test/test_task_center backend/tests/unit_test/test_tools -q`
- `.venv/bin/pytest backend/src/task_center_runner/tests/mock/contracts backend/src/task_center_runner/tests/mock/task_center/test_correctness.py -q`
- `.venv/bin/pytest backend/tests/unit_test/test_task_center/test_persistence/test_goal_store.py -q`
  (the REAL A1 guard — round-trips the `goals` table; must pass after Phase 3).
- `.venv/bin/pytest backend/tests/unit_test/test_task_center/test_persistence/test_migration_drops_legacy_table.py -q`
  (generic regression check; vacuous for A1 — see fact #6).
- `.venv/bin/pytest backend/tests/unit_test/test_benchmarks -q`
  (`test_sweevo_audit_recorder.py` imports `GoalStore`; added per #4).
- `.venv/bin/ruff check backend/src backend/tests`
- Grep gates (see Acceptance criteria).

---

## Contract & serialized-string decisions

| String / tag / key | Decision | Rationale | Consumers to update on rename |
|---|---|---|---|
| `goals` (DB `__tablename__`) | **KEEP** | Physical rename = data-migration blast radius, zero conceptual gain (A1). | none |
| `status` enum values `open/succeeded/failed/cancelled` | **KEEP** | Persisted values. | none |
| `origin_kind` values `entry`/`task` | **KEEP** | Persisted values. | none |
| `final_outcome` keys (`outcome`,`final_iteration_id`,`final_attempt_id`) | **KEEP** | Persisted JSON contract. | none |
| `submission_kind="goal_start"` | **KEEP** | Tool-metadata contract; mock + audit read it. | none |
| `"goal_closure_report"` (payload KEY at `orchestrator.py:204` + `submission_kind` VALUE at :205) | **KEEP** | **Silent-break hazard:** serialized payload contract read at `runner.py:1968` (`payload.get("goal_closure_report")`) and asserted in `test_submission_terminal_routing.py:350` + `test_attempt_orchestrator.py:495`. Shares a stem with the RENAMED method `apply_goal_closure_report` — an executor "renaming for consistency" breaks the mock with NO import error. The method renames; the string does NOT. | none (consumers read the unchanged string) |
| Metadata keys `goal_id`,`initial_iteration_id`,`initial_attempt_id` | **KEEP** | Serialized handoff-tool metadata; mock `result.metadata.get("goal_id")`. | none |
| Tool name `submit_execution_handoff` | **KEEP** | Contract-tested (`_project_build_contracts.py`, `test_advisor_gate_wiring.py`). | none |
| Tool names `submit_plan_closes_goal`, `submit_plan_defers_goal` | **KEEP** | String-matched by router + mock (`"submit_plan_defers_goal" in prompt`); in `_names.py`. | none |
| Arg `goal_handoff` (+ validator) | **KEEP** | Contract; work-statement noun. | none |
| XML tag `<goal>` (+ `source_kind="goal"`, `metadata={"tag":"goal"}`) | **KEEP** | Mock matches `"<goal>" in prompt`; tag-dictionary pins it; work-statement. | none |
| XML tag `<iteration_goal>`, marker `(identical to <goal>)` | **KEEP** | Tag-dictionary pinned; work-statement. | none |
| `deferred_goal_for_next_iteration` (field + tag) | **KEEP** | Tag-dictionary pinned; work-statement field. | none |
| `Goal.goal: str` work-statement field / `goal=` params/columns | **KEEP** | The "what to accomplish" content noun, not the axis. | none |
| `goal_id` (attribute + `"goal_id"` string + `ScopeField` literal) | **KEEP** | Serialized key + `require_field("goal_id")` + fixtures + table id; no consumer forces `workflow_id` (fact #3). | none |
| Audit EventType **values** (`"goal_started"`,`"planner_full_plan"`,…) | **KEEP** | Audit-stream contract. | none |
| Audit EventType **member names** (`GOAL_STARTED`,`PLANNER_COMPLETES_GOAL_PLAN`) | **KEEP (optional follow-up)** | Cosmetic; rename ripples into scenarios + 6 mock files. | (deferred) |
| `goal/` package directory | **RENAME → `workflow/`** | Axis package; user-requested. | all `task_center.goal` importers + `db/stores/goal_store.py` |
| `Goal` DTO, `GoalStatus`, `GoalOrigin(Kind)`, `GoalClosureReport*`, `GoalClosureDelivery*` | **RENAME → `Workflow*`** | In-process axis symbols. | persistence, invariants, stores, recipes, attempt, runner, tests |
| `GoalStarter` / `StartedGoal` | **RENAME → `WorkflowStarter`/`StartedWorkflow`** | In-process axis symbols. | `entry/bootstrap.py`, runner, tests |
| `GoalLifecycle`, `GoalClosureCallback`, `create_goal`,`close_goal`,`_require_goal` | **RENAME → `Workflow*`/`create_workflow`/`close_workflow`** | In-process axis symbols/methods. | starter, lifecycle internals, tests |
| `GoalClosureReportRouter` | **RENAME → `WorkflowClosureReportRouter`** | In-process axis symbol. | starter, attempt orchestrator, tests |
| `nested_goal_depth`, `_nested_goal_depth_gt_1` | **RENAME → `nested_workflow_depth`/`_nested_workflow_depth_gt_1`** | In-process axis symbols. | `terminal_tool_routing.py`, `test_ancestry.py` (import), `test_terminal_tool_router.py` (5 monkeypatch strings) |
| `GoalStoreProtocol`, `GoalStore` (class), `GoalRecord` (class) | **RENAME → `Workflow*`** | In-process axis symbols (table name & files stay). | persistence, `goal_store.py`, `models/goal.py` |
| `WAITING_GOAL` enum *value* `"waiting_goal"` | **KEEP** | Persisted task-status value; `.value` string-compared in 3 src files + ~7 test asserts. | none |
| `WAITING_GOAL` enum *member name* | **KEEP (optional follow-up)** | Cosmetic; rename ripples into ~12 source + test sites for zero contract gain. | (deferred) |
| `apply_goal_closure_report` (method) | **RENAME → `apply_workflow_closure_report`** | In-process method; not serialized. | `attempt/orchestrator_registry.py:33` (protocol), `attempt/deps.py:128/136`, `attempt/orchestrator.py:166`, `goal/closure_report_router.py:72`, `test_phase04_close_report_delivery.py` (2), `test_attempt_orchestrator.py` (2) |
| `start_delegated_goal` (submission-context method) | **RENAME → `start_delegated_workflow`** | In-process method; the "handoff complex goal for workflow execution" surface. KEEP arg `goal_handoff`. | `tools/submission/context/executor.py:71` + docstring:25, `submit_execution_handoff.py:82`, any `test_tools/*` stub |
| `parent_task_for_delegated_goal` (method) | **RENAME → `parent_task_for_delegated_workflow`** | In-process method. | `attempt/deps.py:105`, `goal/closure_report_router.py:58`, `goal/starter.py:192/280` |
| `mark_waiting_goal` (method) | **RENAME → `mark_waiting_workflow`** | In-process method. | `attempt/deps.py:138`, `goal/starter.py:200` |
| `restore_running_after_failed_goal_start` (method) | **RENAME → `restore_running_after_failed_workflow_start`** | In-process method. | `attempt/deps.py:169`, `goal/starter.py:284` |
| `delegated_goal_id` (param/kwarg) | **RENAME → `delegated_workflow_id`** | In-process param; KEEP the `"goal_id"` metadata key it writes (`deps.py:150`). | `attempt/deps.py:141`, `goal/starter.py:201` |
| `assert_goal_open` | **RENAME → `assert_workflow_open`** | In-process invariant. | invariants, lifecycle, tests |
| `_PreparedGoalOrigin` (`starter.py:312`) | **RENAME → `_PreparedWorkflowOrigin`** | Pure-internal axis dataclass; gated-clean. | `starter.py:124/141` constructors, `:120` annotation |
| `AttemptDelegatedGoalParentTask` (`deps.py:120`) | **RENAME → `AttemptDelegatedWorkflowParentTask`** | Pure-internal axis dataclass; gated-clean. | `deps.py:4` (docstring), `:107` (return type), `:111` (constructor) |
| Internal helpers `list_for_goal`, `child_outcomes_for_goal`, `assert_iteration_id_unique_in_goal`, `_build_goal_lifecycle` | **KEEP (deferred, optional follow-up)** | Internal axis-stem methods/functions; no contract; renaming inflates diff. NOT gated (per A6). | (deferred) |
| `ContextEngineDeps.goal_store` attribute | **KEEP (optional follow-up)** | Internal; rename touches ~6 recipe files for zero contract gain. | (deferred) |
| `goal_iteration_blocks` function name | **KEEP (optional follow-up)** | Internal; cosmetic. | (deferred) |
| Files `db/stores/goal_store.py`, `db/models/goal.py` | **KEEP filenames** | File rename is cosmetic churn; rename classes inside. | (deferred) |

---

## RALPLAN-DR summary

**Mode:** SHORT (behavior-preserving rename; locked scope, no new capability).

**Principles (organizing rules):**
1. One discriminator decides every occurrence: persisted/serialized/string-matched/LLM-work-statement ⇒ KEEP; pure in-process axis symbol ⇒ RENAME.
2. Two registers of "goal": axis (→Workflow) vs work-statement text (stays "goal").
3. Smallest safe move: rename symbols + move one package; never touch the table, enum values, tool names, or string-matched tags.
4. Behavior preservation is the acceptance bar — same tests pass; completeness is proved by pytest + `task_center.goal → 0` + ruff, NOT by a blanket `Goal` grep (unsatisfiable: ≥52 KEEPs survive).
5. Land as ONE contiguous RED→green sweep (Phases 1–4b, single commit); dirty-worktree-tolerant; stage explicit paths only.

**Decision drivers (top 3):**
1. **Contract/serialization stability** — string-matched tags, tool metadata, persisted values, `goal_id`, and the `"goal_closure_report"` payload string must not move or the mock runner / audit / DB break silently (no import-time failure).
2. **Blast-radius containment** — cross-package *imports* are nearly contained (`db/stores/goal_store.py` + a couple of test importers + `GoalRecord` in `recorder.py`/`db/models/__init__.py`); keeping the work-statement register stable keeps recipes/mock untouched.
3. **User intent fidelity** — axis becomes Workflow; "goal" survives exactly where the user used it as a content noun.

**Viable options:**
- **Option A (CHOSEN): rename in place + move `goal/`→`workflow/`; iteration/attempt stay.**
  Pros: matches user intent; nearly-contained ripple; the dir name `workflow/`
  signals the axis. Cons: no green intermediate (Phases 1–4b are one RED sweep,
  single commit); dir no longer visually contains its sub-axes (iteration/attempt
  live as siblings); a handful of in-process method renames
  (`apply_workflow_closure_report`).
- **Option B: thin facade/alias package — keep `goal/`, add `workflow/` re-exporting renamed symbols.**
  Pros: zero import-path churn; reversible. Cons: two names for one concept =
  exactly the confusion the rename is meant to remove; permanent dead alias layer
  violates project "no compatibility shims / no speculative abstraction" rules;
  doesn't satisfy "nested workflow rather than nested goal" because the package
  is still `goal`. **Invalidated** by the project's surgical/no-shim constraints.
- **Option C: full physical rename incl. `goals`→`workflows` table + `goal`
  column + migration.** Pros: total vocabulary consistency. Cons: data-migration
  blast radius, persisted-value churn, breaks the `test_goal_store` round-trip and
  every persisted-value contract, zero conceptual gain for a rename task. **Out of
  chosen scope** — recorded as optional follow-up (the `engine.py` legacy-drop
  precedent makes it feasible later if ever wanted).
- **Option D: rename symbols but KEEP the `goal/` directory.**
  Pros: zero package-move / import-path churn. Cons: the package *name* is itself
  part of "nested workflow rather than nested goal" — a `workflow.WorkflowStarter`
  living in `task_center/goal/` is internally contradictory and undercuts the
  user's stated intent. **Rejected** — the directory carries the axis vocabulary
  the user explicitly asked to change.
- **Co-location sub-option (within A): also move iteration/attempt into
  `workflow/`.** Pros: dir contains its sub-axes. Cons: rewrites two more
  packages' import paths across the codebase for zero behavior gain; larger,
  riskier diff. **Not chosen** — no concrete coupling forces co-location.

---

## Acceptance criteria (testable)

**Test gates (all must pass):**
- `.venv/bin/pytest backend/tests/unit_test/test_task_center/test_domain/test_goal_dto.py -q`
- `.venv/bin/pytest backend/tests/unit_test/test_task_center/test_domain/test_ancestry.py -q`
- `.venv/bin/pytest backend/tests/unit_test/test_task_center/test_agent_launch/test_terminal_tool_router.py -q`
- `.venv/bin/pytest backend/tests/unit_test/test_task_center/test_persistence/test_goal_store.py -q` (the REAL A1 guard — round-trips the `goals` table).
- `.venv/bin/pytest backend/tests/unit_test/test_task_center/test_context_engine/test_renderer.py backend/tests/unit_test/test_task_center/test_context_engine/test_tag_dictionary.py backend/tests/unit_test/test_task_center/test_context_engine/test_recipes_planner_closes_or_defers.py backend/tests/unit_test/test_task_center/test_context_engine/test_role_context_matches_diagram.py -q`
- `.venv/bin/pytest backend/tests/unit_test/test_tools -q`
- `.venv/bin/pytest backend/tests/unit_test/test_benchmarks -q` (`test_sweevo_audit_recorder.py` imports `GoalStore`).
- `.venv/bin/pytest backend/src/task_center_runner/tests/mock/contracts/test_runner_imports.py backend/src/task_center_runner/tests/mock/contracts/test_advisor_gate_wiring.py -q`
- `.venv/bin/pytest backend/src/task_center_runner/tests/mock/task_center/test_correctness.py -q` (exercises `audit/recorder.py` → `WorkflowRecord`).
- `.venv/bin/pytest backend/tests/unit_test/test_task_center/test_persistence/test_migration_drops_legacy_table.py -q` (generic regression; vacuous for A1 per fact #6 — NOT a "didn't touch the table" proof).
- `.venv/bin/ruff check backend/src backend/tests` clean.

**Where completeness actually comes from.** A blanket `grep -rn 'Goal'` → 0 is
**mathematically unsatisfiable** and is NOT used: the checkout has ~442 mixed-case
`Goal` hits and ≥52 are KEEPs that survive a correct rename — scenario classes
`InitialGoal`/`NestedGoal`/`NestedGoalFailure`/`PlannerDefersWithoutDeferredGoal`
(string-pinned in `test_scenario_suite_imports.py:133-143`), tool DTOs
`SubmitPlanClosesGoalInput`/`SubmitPlanDefersGoalInput`, and (unless renamed in
change-set below) internal `_PreparedGoalOrigin`/`AttemptDelegatedGoalParentTask`.
**The completeness proof is: `task_center.goal` import grep → 0, the `goal/` dir
absent, full pytest green, and ruff clean.** The symbol greps below are
**sanity checks** on top of that, scoped to exactly the symbols this plan renames
(they must AGREE with the classification in #6 — never gate on a deferred symbol).

**Grep sanity checks:**
- **PascalCase axis-symbol allowlist** (exactly the types this plan renames; an
  unanchored `Goal` would false-hit the ≥52 KEEPs above):
  `grep -rnE '\b(Goal|GoalStatus|GoalOrigin|GoalOriginKind|GoalClosureReport|GoalClosureDelivery\w*|GoalStarter|StartedGoal|GoalLifecycle|GoalClosureCallback|GoalClosureReportRouter|GoalStoreProtocol|GoalStore|GoalRecord|_PreparedGoalOrigin|AttemptDelegatedGoalParentTask)\b' backend/src backend/tests | grep -v __pycache__`
  → **0**. (`_PreparedGoalOrigin`/`AttemptDelegatedGoalParentTask` included
  because #6 RENAMES them; if either is deferred there, strike it from this gate.)
- **Renamed snake_case methods — explicit allowlist** (a blanket `_goal` pattern
  false-positives on KEEPs `deferred_goal_for_next_iteration`, `goal_handoff`,
  `recursive_handoff_goal`, `goal_closure_report` (string), so enumerate exactly
  what was renamed):
  `grep -rnE '\b(nested_goal_depth|_nested_goal_depth_gt_1|assert_goal_open|create_goal|close_goal|_require_goal|apply_goal_closure_report|start_delegated_goal|parent_task_for_delegated_goal|mark_waiting_goal|restore_running_after_failed_goal_start|delegated_goal_id)\b' backend/src backend/tests | grep -v __pycache__`
  → **0**. (Excludes `list_for_goal`, `child_outcomes_for_goal`,
  `assert_iteration_id_unique_in_goal`, `_build_goal_lifecycle` — those are
  deferred per #6, so they are intentionally NOT gated.)
- **The `goal/` package path is gone (the real completeness backstop):**
  `test -d backend/src/task_center/goal && echo FAIL || echo OK` → **OK**; and
  `grep -rn 'task_center\.goal' backend/src backend/tests | grep -v __pycache__` → **0**.
- KEEP strings still present (proof we didn't over-rename):
  `grep -rn '"goals"' backend/src/db/models/goal.py` → present;
  `grep -rn '<goal>\|goal_id\|goal_handoff\|submission_kind="goal_start"\|"submit_execution_handoff"' backend/src` → present.
- Mock string-matches untouched:
  `grep -rn '"<goal>" in prompt\|"submit_plan_defers_goal" in prompt' backend/src/task_center_runner/agent/mock/runner.py` → present.

**Doc gates:**
- `grep -rn 'Goal -> Iteration\|GoalStarter\|nested_goal_depth' CLAUDE.md docs/architecture` → **0** (excluding the flagged historical `docs/task_center_harness_and_context_engine.html`).
- Each touched `docs/architecture/task_center/*.html` has an updated `data-last-reviewed-commit`.

---

## Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Silent contract breakage — renaming a string the mock/audit/DB matches (no import error) | Medium | The discriminator + decisions table fix the boundary; grep gates assert KEEP strings still present AND axis symbols gone; mock `test_correctness.py` exercises the matched paths. |
| `goal_id` over-renamed to `workflow_id` | Medium | Explicitly KEEP (fact #3); grep gate asserts `goal_id` still present. |
| Missed monkeypatch target string in `test_terminal_tool_router.py` (5 sites) | Medium | Listed as lockstep consumer in Phase 3; test fails loudly if a target string is stale. |
| Parallel-agent dirty worktree collides with the rename | Medium | Phased landing; stage explicit paths only; re-verify at HEAD before declaring done; never `git add <dir>`. The probe_bridge.py / mock_event_source files already dirty are unrelated — do not touch. |
| Migration test accidentally edited / table touched | Low | Acceptance bar requires it pass UNCHANGED; Phase 0 captures baseline. |
| Scope creep into dynamic-workflow capability | Low | Locked scope; all capability ideas parked as optional follow-ups only. |
| Over-renaming internal cosmetics (EventType members, `goal_store` attr, function/file names) inflates diff | Medium | All explicitly deferred to optional follow-ups; Phase boundaries forbid them. |

## Open questions (also append to `.omc/plans/open-questions.md`)

1. Confirm `ContextEngineDeps.goal_store` attribute stays `goal_store` (default
   KEEP) vs rename to `workflow_store` — affects ~6 recipe files. Default chosen:
   KEEP.
2. Confirm files `db/models/goal.py` and `db/stores/goal_store.py` keep their
   filenames (rename classes only). Default chosen: KEEP filenames.
3. Confirm EventType member-name renames are deferred (values stay regardless).
   Default chosen: DEFER.
