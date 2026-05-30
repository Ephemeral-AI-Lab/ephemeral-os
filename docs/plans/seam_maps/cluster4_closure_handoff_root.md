# Cluster 4 — WS7+WS8 closure-removal / child-workflow handoff / root run path / WS6 dissolutions

Edit manifest for a coherence-bound implementer. Scope: closure abstraction
removal, the three child-workflow handoff orchestrator methods, the M1
orphan-guard, the C1/COND-1 root run path (`run_controller.py`), and the WS6
dissolutions of `deps.py` + `ancestry.py` + the 3× `state.py` consolidation.

All line anchors below were re-verified against current code (HEAD =
`fabce1b70`). Where the plan's `:line` differs from current code, it is flagged
in the DRIFT section.

Baseline: 428 unit tests pass; the one pre-existing failure
(`test_attempt_harness_records_runner_token_usage`) is NOT ours.

---

## 0. Sequencing note (intra-cluster)

WS6 store-signature changes (MN3) must land **before** the run controller (§6)
and the new handoff path call the stores, or they pass now-removed kwargs. But
WS6 state-consolidation (3×`state.py`→`_core/state.py`) is shared with **other
clusters** (WS1/WS2/WS4 own the enum/field bodies). This cluster:

- **OWNS:** closure router deletion, `closure_report_router.py` delete,
  `WorkflowClosureReport`/`WorkflowOrigin` family deletion, `starter.py`
  rewrite, `lifecycle.py` close path rewrite, `ancestry.py`→`terminal_routing.py`
  fold, `deps.py` delete/split, the three orchestrator handoff methods +
  orphan-guard, `run_controller.py` authoring, `bootstrap.py` rewire.
- **COORDINATES (does not solely own):** the literal `Workflow`/`Iteration`/
  `Attempt` dataclass bodies in `_core/state.py` (field churn is WS2/WS4),
  `Task.outcomes`/`terminal_tool_result` field rename (WS4), `AttemptFailReason`
  collapse to `TASK_FAILED|STARTUP_FAILED` (WS2), `Outcome` algebra in
  `_core/outcomes.py` (WS4). This manifest specifies the SHAPE those types must
  have for the handoff/root path to compile, and flags overlaps.

---

## CORE FILES (hand-edited logic)

### C-1. `backend/src/task_center/workflow/closure_report_router.py` — **DELETE**

Whole file (100 lines). The `WorkflowClosureReportRouter` class + both
`deliver`/`_deliver_entry_origin` go away. Its two responsibilities split:

- entry-origin delivery (`_deliver_entry_origin`, `finish_run`) → moves to
  `run_controller.py` root-close handler (§6 / C-7).
- task-origin delivery (route to parent attempt orchestrator) → becomes the
  workflow-close fork in `lifecycle.py` (C-3), calling
  `orchestrator_registry.get(attempt_id).apply_child_workflow_outcome(...)`.

Importers to fix: `starter.py:15-17,155,162,268` and the `workflow/__init__.py:5`
docstring mention.

---

### C-2. `backend/src/task_center/workflow/state.py` — **gut to Workflow + WorkflowStatus only**

Current shape (verified):
- `WorkflowOriginKind(StrEnum)` `:11-13` — **DELETE**
- `WorkflowOrigin` dataclass + `.entry()`/`.task()`/`__post_init__` `:16-41` — **DELETE**
- `WorkflowStatus(StrEnum)` `:44-48` — KEEP (moves to `_core/state.py` per D11).
- `Workflow` dataclass `:51-77` — KEEP+EDIT: drop `final_outcome` `:60`,
  `origin_kind` `:64`, `requested_by_task_id` `:65`, the `.origin` property
  `:72-77`. **ADD** `parent_task_id: str | None = None` (D12). Keep `is_open`.
  Rename field `goal`→`workflow_goal` (D2 — coordinate; WS-vocab).
- `WorkflowClosureReport` `:80-101` (+ `to_final_outcome`) — **DELETE**
- `WorkflowClosureDeliveryStatus` / `WorkflowClosureDeliveryResult` `:104-111` — **DELETE**

Per D11 the surviving `Workflow` + `WorkflowStatus` move to `_core/state.py`;
this file is deleted. The package `workflow/__init__.py` stays (lifecycle/starter
still live there) but its docstring `:3-7` loses the `ancestry`/
`closure_report_router`/state mentions.

Target `Workflow` fields (matches plan §2):
`workflow_id · task_center_run_id · workflow_goal · status · iteration_ids ·
parent_task_id: str|None · created/updated/closed_at · is_open`.

> NOTE the plan renames `Workflow.id`→`workflow_id`. Current code uses `.id`
> pervasively (`created_workflow.id`, `workflow.id`). That rename is a large
> mechanical sweep owned by WS6/WS10; this cluster's edits must use whatever the
> final field name is. **OPEN DECISION (see below): keep `.id` or rename to
> `workflow_id`.** The plan's §2 table says `workflow_id`; treat as target.

---

### C-3. `backend/src/task_center/workflow/lifecycle.py` — rewrite close path

Current (verified):
- imports `WorkflowClosureReport, WorkflowOrigin` `:30`; iteration closure DTOs
  `AttemptPlanFailed, IterationClosureReport, SuccessDeferred, TerminalSuccess`
  `:36-44`.
- `WorkflowClosureCallback = Callable[[WorkflowClosureReport], object]` `:49`.
- `create_workflow(... origin: WorkflowOrigin ...)` `:76-87` → calls
  `workflow_store.insert(origin=origin)`.
- `close_workflow(...)` `:132-159` builds a `WorkflowClosureReport`, calls
  `set_status(final_outcome=report.to_final_outcome())`, then
  `self._deliver_closure_report(report)`.
- `_route_iteration_closure` `:198-224` dispatches on `ClosureOutcome` union
  (`SuccessDeferred`→new iteration; `TerminalSuccess|AttemptPlanFailed`→
  `close_workflow`).

Target:
1. **`create_workflow`** signature `origin: WorkflowOrigin`→`parent_task_id:
   str | None`. Calls `workflow_store.insert(parent_task_id=parent_task_id)`
   (see C-9 store rename). Plan: workflow `goal`→`workflow_goal` arg.
2. **`close_workflow`**: remove the `WorkflowClosureReport` construction and
   `to_final_outcome`. New body:
   - `set_status(workflow_id, status=SUCCEEDED|FAILED, closed_at=...)` (no
     `final_outcome` — store sig drops it, C-9).
   - Then **the close-routing fork** (replaces the old router `.deliver`):
     fetch the fresh workflow, read `workflow.parent_task_id`:
       - `parent_task_id is None` → impossible for a real workflow after this
         refactor (root has `"<run_id>:root"`); keep as a hard invariant.
       - parent task **has an attempt prefix** (`_parent_attempt_id(task) is not
         None`, i.e. `task_id` contains an attempt-id segment) → resolve the
         attempt id, `orchestrator_registry.get(attempt_id)` → call
         `.apply_child_workflow_outcome(generator_task=<row>, child_workflow=
         <closed workflow>)`. Missing orchestrator = hard
         `TaskCenterInvariantViolation` (mirrors the old router's
         "not registered" guard at `closure_report_router.py:67-71`).
       - parent task has **no attempt prefix** (`"<run_id>:root"`) → call the
         **root close handler** owned by `run_controller` (C-7). Lifecycle needs
         a `run_close_handler` callback injected (mirror the existing
         `deliver_closure_report` injection seam at `__init__` `:63`).
   - **Idempotency:** the old router checked the parent task status (DONE/FAILED
     →`already_delivered`; `:43-52`). Preserve: if the parent task is already
     off `WAITING_WORKFLOW`, the orchestrator method is a no-op (mirror
     `orchestrator.apply_workflow_closure_report` early-return at `:182-184`).
3. **`_route_iteration_closure`**: the `ClosureOutcome` union (`TerminalSuccess
   /SuccessDeferred/AttemptPlanFailed`) is deleted by WS4/WS6 (iteration/state.py
   DTOs). Whatever replacement the iteration cluster lands (status enum or a
   slimmer report), this method's two branches (defer→new iteration; terminal→
   close_workflow) must survive. **COORDINATE with WS4** — the `outcome` object
   shape is owned there. Keep the `close_workflow` call sites `:215,252`.
4. The lifecycle now needs to pass the **closed workflow's `parent_task_id`** to
   the fork. `final_attempt_id` survives as a param to the orchestrator method
   (used to build failed-handoff outcomes + `fail_reason`; WS7 plan line 401).

`WorkflowClosureCallback` typedef `:49,261` → replace with the new
`run_close_handler` callback type and the orchestrator-routing closure.

---

### C-4. `backend/src/task_center/workflow/starter.py` — rewrite `start` to take `parent_task_id`

This is the largest single rewrite in the cluster. Current (verified):
- `StartedWorkflow` dataclass `:37-49` has `origin: WorkflowOrigin` +
  `.parent_task_id` property reading `origin.task_id`.
- `start(*, prompt, origin: WorkflowOrigin)` `:69-123`.
- `_prepare_origin` `:125-149` — entry vs task branch.
- `_build_workflow_lifecycle` `:151-165` — constructs the
  `WorkflowClosureReportRouter` and passes `deliver_closure_report=router.deliver`.
- `_assert_parent_running_and_no_open_child` `:167-184` — **`:171` RUNNING guard**,
  `:143` attempt-bound guard (in `_prepare_origin`).
- `_mark_parent_waiting` `:186-211` — calls
  `parent_task.mark_waiting_workflow(...)` (the deps.py method).
- `_compensate_failed_start` `:213-281` — best-effort rollback; **`:265-279`
  synthetic-failed-close-report compensation** (the M1 predecessor).
- `_restore_parent` `:283-296`.
- `_close_unstarted_attempt` `:298-309`.
- `_parent_attempt_id(task)` helper `:312-314` reads `task_center_attempt_id`.

Target:
1. `start(*, prompt, parent_task_id: str)` — single path (no entry/task fork;
   D7: all workflows generator-spawned, root included with
   `parent_task_id="<run_id>:root"`).
2. **Atomic RUNNING→WAITING_WORKFLOW + link**: the plan (§6 step 3, WS8) says
   `start` "atomically flips the parent task `RUNNING → WAITING_WORKFLOW` while
   setting `child_workflow_id`" in one transaction, mirroring
   `_mark_parent_waiting`. This becomes a call into the orchestrator's
   `start_child_workflow` (C-5 / D14) for attempt-prefixed parents, OR a direct
   store CAS for the root parent. **The flip + link must be one store write**
   (see OPEN DECISION: new store method `set_task_status_if_current` does NOT
   currently write `child_workflow_id`).
3. **Relax `:171` RUNNING guard + `:143` attempt-bound guard for the run-level
   case**: the root parent `"<run_id>:root"` is seeded RUNNING (COND-1) and is
   NOT attempt-bound (no attempt prefix). `_prepare_origin`'s attempt-bound
   rejection `:141-145` must allow the root id; `_assert_parent_running_and_no_
   open_child` RUNNING check `:171` stays (root IS running) but the
   "requires a generator task / attempt-bound" path `:143` must permit the root.
   Concretely: parse the attempt id from the parent `task_id`; if `None` AND the
   id ends in `:root`, it is the run-level root → skip attempt-orchestrator
   routing and use the run controller's close handler.
4. `StartedWorkflow.origin`/`.parent_task_id` property → store
   `parent_task_id` directly.
5. `_build_workflow_lifecycle`: drop the `WorkflowClosureReportRouter`; inject
   the new close-routing closure + the `run_close_handler` (C-3, C-7).
6. `_compensate_failed_start`: drop the synthetic-close-report branch `:265-279`
   (no `WorkflowClosureReport` exists). Replace with the **M1 orphan-guard**:
   force the parent `WAITING_WORKFLOW → FAILED` via the orchestrator's
   `cancel_child_workflow` (C-5), and if THAT fails, the state-level last resort
   `set_task_status_if_current(expected=WAITING_WORKFLOW, status=FAILED)` with
   empty/failed outcomes (plan WS7 line 411-416). Keep the attempt/iteration/
   workflow CANCELLED rollback steps (drop the `final_outcome=None` kwarg at
   `:254` — store sig change).
7. `_restore_parent` `:283-296` → folds into `cancel_child_workflow`.

---

### C-5. `backend/src/task_center/attempt/orchestrator.py` — three handoff methods + orphan-guard

Current handoff surface (verified):
- `apply_workflow_closure_report(report: WorkflowClosureReport)` `:166-213` —
  resumes the WAITING_WORKFLOW parent: early-return if not WAITING_WORKFLOW
  `:182-184`; asserts GENERATE stage `:186`; on `report.outcome` writes
  DONE/FAILED via `set_task_status_if_current` with a `handoff_rollup` summary.
- `_build_handoff_rollup(report)` `:215-240` — builds nested child `<task>`
  records via `child_outcomes_for_workflow` + `generator_outcomes` +
  `attempt_failure_line` (all from `_core.generator_summaries`).
- There is **no** `start_child_workflow`/`cancel_child_workflow` on the
  orchestrator today; those live as `mark_waiting_workflow`/
  `restore_running_after_failed_workflow_start` on
  `AttemptDelegatedWorkflowParentTask` in `deps.py` (C-6).

Target (D14 — three methods on the orchestrator, no wrapper class, no "wake"):
1. **`start_child_workflow(self, *, generator_task: dict, child_workflow:
   Workflow)`** (was `AttemptDelegatedWorkflowParentTask.mark_waiting_workflow`):
   atomically set the parent generator task `RUNNING → WAITING_WORKFLOW` AND
   `child_workflow_id = child_workflow.workflow_id` in one store write (forward
   link). CAS-miss → `TaskCenterInvariantViolation` (mirror deps.py `:163-167`).
2. **`apply_child_workflow_outcome(self, *, generator_task: dict, child_workflow:
   Workflow, final_attempt_id: str | None)`** (was
   `apply_workflow_closure_report`): on child close, write the generator
   `outcomes = ` ONE `Outcome` whose `children = child_workflow.outcomes` (MN2,
   plan §1/§2), `terminal_tool_result` accordingly, mark DONE/FAILED
   (success→DONE, failure→FAILED), advance the DAG (`_stage_advancer.
   advance_ready_tasks()`). Keep the early-return idempotency (status already
   off WAITING_WORKFLOW → no-op, current `:182-184`). For the **failed-child**
   case the `Outcome` carries the failed last-iteration outcomes + `fail_reason`
   (generalizes `_build_handoff_rollup`'s failure branch `:229-239` using
   `attempt_failure_line` over `final_attempt_id`). The `child_workflow.outcomes`
   is the WS4 derived `workflow.outcomes` = last iteration's outcomes.
3. **`cancel_child_workflow(self, *, generator_task: dict)`** (was
   `restore_running_after_failed_workflow_start`): force WAITING_WORKFLOW→
   RUNNING (failed-start rollback) OR, per M1, WAITING_WORKFLOW→FAILED when the
   workflow can't be restored. The plan's WS7 line 408 says
   `cancel_child_workflow` = the old restore; the **orphan-guard** (line 411) is
   the FAILED last-resort. Spell these as: `cancel_child_workflow` restores to
   RUNNING; the orphan-guard (called by `starter._compensate_failed_start` when
   both start and cancel fail) forces FAILED.
4. **Delete** `apply_workflow_closure_report` `:166-213` and
   `_build_handoff_rollup` `:215-240`. The `WorkflowClosureReport` import `:43`
   goes.
5. Other current orchestrator edits that THIS cluster depends on but are
   **owned by WS1/WS2/WS4** (do not author the bodies, but they must land for
   compile): `assert_evaluator_task_for_submission`→reducer `:14`;
   `_core.generator_summaries`→`_core.outcomes` `:19`; `stage_advancer`→
   `run_stage`, `generator_dag`→`plan_dag` `:30-34`; `SpawnReason` removal `:46`;
   `EvaluatorSubmission`→`ReducerSubmission` `:50`; the `AttemptStage.GENERATE`→
   `RUN` references `:140,186,293,308`; `AttemptFailReason.PLANNER_FAILED`→
   `TASK_FAILED` `:154`; `summaries=[]`/`task_center_attempt_id`/`spawn_reason`
   kwargs in `upsert_task` `:96-108,277-288` (MN3). Flag these as cross-cluster.
6. `from task_center.attempt.deps import AttemptDeps` `:36` → after `deps.py`
   delete, import `AttemptDeps` from `attempt/launch.py` (D13).

---

### C-6. `backend/src/task_center/attempt/deps.py` — **DELETE** (split per D13)

Current contents (verified):
- `AgentLaunch` dataclass `:39-65` → **move to `attempt/launch.py`** (cycle-free:
  launch.py has no edge to orchestrator.py; launch.py already imports
  `AgentLaunch` from deps at `:16`).
- `AttemptDeps` dataclass `:68-116` (incl. `run_id_for_attempt`,
  `require_composer`, `parent_task_for_delegated_workflow`) → **move to
  `attempt/launch.py`**. Drop `parent_task_for_delegated_workflow` `:105-116`
  (its product, `AttemptDelegatedWorkflowParentTask`, is dissolved).
- `AttemptDelegatedWorkflowParentTask` `:119-174` (`apply_workflow_closure_
  report`/`mark_waiting_workflow`/`restore_running_after_failed_workflow_start`)
  → **dissolved into the three orchestrator methods (C-5)**. Delete.

Importers to repoint (`from task_center.attempt.deps import ...`):
- `task_center/__init__.py:34,76` (`AttemptDeps` export → `attempt.launch`).
- `entry/bootstrap.py:30`.
- `workflow/starter.py:30`.
- `workflow/closure_report_router.py:14` (file deleted anyway).
- `attempt/stage_advancer.py:22-25` (`AgentLaunch, AttemptDeps`) — becomes
  `run_stage.py`; import from `launch.py`.
- `attempt/launch.py:16` (`AgentLaunch, AttemptDeps`) — now local; remove import.
- `attempt/orchestrator.py:36`.
- `tools/submission/context/attempt.py:11` (`AttemptDeps`).
- `tools/submission/context/executor.py:17` (TYPE_CHECKING `AttemptDeps`).
The plan's WS6 list (line 376) names `__init__.py:34`, `entry/bootstrap.py:30`,
`starter.py:30`, `stage_advancer.py:22`, `orchestrator.py:36`,
`tools/submission/context/executor.py:17` — **DRIFT: it omits
`tools/submission/context/attempt.py:11` and `launch.py:16`** (both are real
importers, verified). Add them.

---

### C-7. `backend/src/task_center/run_controller.py` — **NEW FILE** (§6, C1/COND-1)

Owns the root run path. Distinct from `entry/bootstrap.py` (process/sandbox
wiring). Design:

```
class RunController:
    def __init__(self, *, runtime: AttemptDeps, starter_factory):  # or runtime + WorkflowStarter
        ...

    def start_root_run(self, *, prompt: str, task_center_run_id: str) -> StartedWorkflow:
        root_task_id = f"{task_center_run_id}:root"
        try:
            # 1. seed the synthetic bootstrap generator task (COND-1)
            runtime.task_store.upsert_task(
                task_id=root_task_id,
                task_center_run_id=task_center_run_id,
                role=TaskCenterTaskRole.GENERATOR.value,
                agent_name=None,            # synthetic — no agent
                context_message="",
                status=TaskCenterTaskStatus.RUNNING.value,   # NOT WAITING_WORKFLOW
                outcomes=[], needs=[],
                # NO task_center_attempt_id (root recognized by absence of attempt prefix)
            )
            # 2. start the root workflow; WorkflowStarter.start atomically flips
            #    root_task RUNNING->WAITING_WORKFLOW + sets child_workflow_id
            return WorkflowStarter(runtime=runtime).start(
                prompt=prompt, parent_task_id=root_task_id,
            )
        except Exception:
            # seed/start failsafe (replaces bootstrap.py:137 _finish_run_if_open)
            self._finish_run_if_open(task_center_run_id, status="failed")
            raise

    def on_root_workflow_closed(self, *, child_workflow: Workflow) -> None:
        # root close handler (replaces _deliver_entry_origin)
        run = runtime.task_store.get_run(child_workflow.task_center_run_id)
        if run is None or run["status"] in ("done","failed"):
            return  # already delivered idempotency
        root_task_id = f"{child_workflow.task_center_run_id}:root"
        succeeded = child_workflow.status == WorkflowStatus.SUCCEEDED
        runtime.task_store.set_task_status_if_current(
            root_task_id,
            expected_status=TaskCenterTaskStatus.WAITING_WORKFLOW.value,
            status=(DONE if succeeded else FAILED).value,
            # outcomes = child_workflow.outcomes  (root bootstrap outcomes = root workflow.outcomes)
        )
        runtime.task_store.finish_run(
            child_workflow.task_center_run_id,
            status="done" if succeeded else "failed",
        )

    def _finish_run_if_open(self, run_id, *, status):  # mirrors bootstrap.py:207-210
        ...
```

**Exact transaction order (seed → start failsafe):**
1. `_create_top_level_run()` (request+run+sandbox binding) — stays in
   `bootstrap.py:150-168`, runs BEFORE the controller.
2. `upsert_task(root_task_id, status=RUNNING)` — seed (COND-1: RUNNING, the link
   doesn't exist yet).
3. `WorkflowStarter.start(parent_task_id=root_task_id)` — creates root workflow +
   atomically `RUNNING→WAITING_WORKFLOW` + `child_workflow_id` (one txn; this is
   the only place the root link is set).
4. Any throw in 2-3 → `finish_run(run_id, "failed")` so the run can't stay OPEN
   nor the bootstrap stay stranded WAITING_WORKFLOW.

**Close-routing fork (where it's wired):** `lifecycle.close_workflow` (C-3)
inspects `parent_task_id`. Attempt-prefix → orchestrator
`apply_child_workflow_outcome`. No attempt-prefix (`:root`) → `RunController.
on_root_workflow_closed`. The fork lives in lifecycle; the root branch CALLS the
controller (controller injected as the `run_close_handler` callback into
`WorkflowLifecycle`, mirroring the deleted `deliver_closure_report` seam).

**Why synthetic:** no profile/sandbox/`agent_run_id`/terminal — it only holds
`parent_task_id` uniformity + the run result as `outcomes`. Plan §6 final para.

---

### C-8. `backend/src/task_center/entry/bootstrap.py` — rewire `:120-148` to RunController

Current (verified): `start()` `:120-148` calls
`WorkflowStarter(runtime).start(origin=WorkflowOrigin.entry(...))` `:132-135`,
wrapped in try/except → `_finish_run_if_open(run_id, "failed")` `:136-138`.
Imports `WorkflowOrigin` `:38`, `AttemptDeps` from `attempt.deps` `:30`.

Target:
- Replace `:131-138` with `started = RunController(runtime=runtime).start_root_
  run(prompt=self._prompt, task_center_run_id=run_id)`. The seed failsafe +
  finish-run-on-throw moves INTO the controller (C-7), so the try/except here
  can thin or stay as a belt-and-suspenders (`_finish_run_if_open` `:207-210`
  can move to the controller or stay; plan §6 says the failsafe is the
  controller's). **Decision: move `_finish_run_if_open` to the controller**;
  keep `bootstrap.py` to just call `start_root_run`.
- Drop `from task_center.workflow.state import WorkflowOrigin` `:38`.
- `from task_center.attempt.deps import AttemptDeps` `:30` → `attempt.launch`.
- `from task_center.context_engine.core import ...` `:31` → `context_engine.
  engine` (WS10 rename — coordinate).
- `_finish_run_if_open` `:207-210` → moves to RunController (or stays;
  see decision above).

---

### C-9. `backend/src/db/stores/workflow_store.py` — drop origin/final_outcome, add parent_task_id

Current (verified): `insert(... origin / requested_by_task_id ...)` `:21-51`
calls `_resolve_origin` `:134-144`; `set_status(... final_outcome ...)` `:72-90`
writes `record.final_outcome`; `list_for_parent_task` filters
`requested_by_task_id` `:92-102`; `_to_dto` `:118-131` maps `origin_kind`/
`requested_by_task_id`/`final_outcome`.

Target:
- `insert`: signature `parent_task_id: str | None` (replaces
  `origin`/`requested_by_task_id`); drop `_resolve_origin`; set
  `record.parent_task_id`. Drop `final_outcome=None` write `:44`.
- `set_status`: **drop `final_outcome` param** `:77,85` (signature + body).
- `list_for_parent_task`: filter `WorkflowRecord.parent_task_id` `:97`.
- `_to_dto`: drop `origin_kind`/`requested_by_task_id`/`final_outcome`; add
  `parent_task_id=record.parent_task_id`; rename `goal`→`workflow_goal`.
- Imports `:10-15` drop `WorkflowOrigin, WorkflowOriginKind`.

This is hand-edited (signature + body), so CORE.

---

### C-10. `backend/src/db/models/workflow.py` — column swap + migration

Current (verified): `origin_kind` `:29`, `requested_by_task_id` (indexed)
`:30-32`, `final_outcome` (JSON) `:36`. Target: **drop** `origin_kind`,
`final_outcome`; **rename or add** `requested_by_task_id`→`parent_task_id`.

Plan WS7 line 397: `_DROPPED_COLUMNS["workflows"] += {final_outcome, origin_kind,
requested_by_task_id}` AND add `workflows.parent_task_id` (new). So
`requested_by_task_id` is **dropped** (not renamed) and `parent_task_id` is a
NEW column. The model column: add `parent_task_id: Mapped[str|None] =
mapped_column(String(96), nullable=True, index=True)` (mirror the old
requested_by index). Update the `:1-6` docstring (mentions
`submit_execution_handoff` — now `submit_workflow_handoff`, WS-vocab).

**db/engine.py migration** (`:40-78`): add a `"workflows"` entry to
`_DROPPED_COLUMNS` = `{"final_outcome","origin_kind","requested_by_task_id"}`.
`parent_task_id` is added by `create_all`/`_rebuild_sqlite_table` automatically
(new nullable column). No `_RENAMED_COLUMNS` entry for workflows (it's a drop +
new, NOT a rename — the values differ: old held the task id of the requester,
new holds the same conceptually but the plan treats it as new; keeping it a
drop+add avoids carrying stale origin_kind logic). **OPEN DECISION:** rename vs
drop+add for `requested_by_task_id`→`parent_task_id` (plan says drop+add; the
data is semantically identical so a rename would preserve dev rows — but per the
MEMORY note durable app DBs are empty in dev, so drop+add is safe). Follow plan:
drop+add.

This is mechanical-ish but the `_DROPPED_COLUMNS` edit + nullable-column add is
schema logic → CORE.

---

### C-11. `backend/src/task_center/_core/terminal_tool_routing.py` → `terminal_routing.py` + fold `nested_workflow_depth`

Current (verified): imports `nested_workflow_depth` from `workflow.ancestry`
`:21`; `_depth(ctx)` `:32-47` calls it; `_nested_workflow_depth_gt_1` `:50-58`
is the patched test seam. `core`→`engine` import `:15` (WS10).

Target (D10 — `ancestry.py` dissolved here):
- **Rename file** → `terminal_routing.py` (WS10).
- **Fold** `nested_workflow_depth` (from `ancestry.py`, C-12) as a private helper
  `_nested_workflow_depth(...)` in this module (its sole caller is `_depth`).
- The folded walk-up uses `Workflow.parent_task_id` (direct) and parses the
  parent attempt from the parent task's `task_id` prefix
  (`task_center_attempt_id` is gone — D5). See C-12 for the rewritten walk.
- Keep `_nested_workflow_depth_gt_1` as the named test seam (tests patch its full
  module path — now `task_center._core.terminal_routing._nested_workflow_depth_
  gt_1`; **flag for WS9 test path updates**).
- `from task_center.context_engine.core import ...` `:15` → `.engine` (WS10).

CORE (the folded walk is hand-edited).

---

### C-12. `backend/src/task_center/workflow/ancestry.py` — **DELETE** (folded into C-11)

Current (verified): `nested_workflow_depth(...)` `:19-62` walks up via
`current_workflow.requested_by_task_id` `:41`, `task_store.get_task(...)` `:43`,
`parent_task.get("task_center_attempt_id")` `:46`. Both anchors **change**:
- `requested_by_task_id` → `parent_task_id` (Workflow field rename).
- `parent_task.get("task_center_attempt_id")` → parse attempt id from the parent
  task's `task_id` PREFIX (D5 removes the `task_center_attempt_id` column;
  generator task ids are `{attempt_id}:gen:{local}`, so attempt id is everything
  before `:gen:` / `:planner` / `:red:`). The root parent `"<run_id>:root"` has
  no attempt segment → walk terminates (depth boundary, same as old
  `if not parent_attempt_id: return depth` `:47-48`).
- `parent_iteration.workflow_id` chain stays.

The folded helper lives in `terminal_routing.py`. Delete `ancestry.py` +
`workflow/__init__.py:5` mention.

---

### C-13. `backend/src/task_center/_core/persistence.py` — protocol signature edits

Current (verified):
- imports `Workflow, WorkflowOrigin, WorkflowStatus` `:34` (and Attempt/Iteration
  state — those move to `_core.state`, WS6).
- `WorkflowStoreProtocol.insert(... origin / requested_by_task_id ...)` `:46-53`.
- `WorkflowStoreProtocol.set_status(... final_outcome ...)` `:59-66`.
- `TaskStoreProtocol.upsert_task(... summaries, task_center_attempt_id,
  context_packet_id, fix_target_id, spawn_reason ...)` `:172-187` (MN3).
- `set_task_status`/`set_task_status_if_current` take `summary: Any` `:193-202`.

Target:
- `insert`: `origin`/`requested_by_task_id` → `parent_task_id: str | None`.
- `set_status`: **drop `final_outcome`** (mirror C-9).
- `upsert_task` (MN3): `summaries`→`outcomes`; **drop** `task_center_attempt_id`,
  `fix_target_id`, `spawn_reason` (D5); add `terminal_tool_result`. **This must
  land before** the run controller (C-7) and submit path call `upsert_task`, or
  they pass removed kwargs. The root-seed (C-7) passes `outcomes=[]`, `needs=[]`,
  NO `task_center_attempt_id`.
- imports `:34` drop `WorkflowOrigin`; repoint state imports to `_core.state`
  (WS6).
- **OPEN DECISION (cross-cluster):** the `child_workflow_id` forward link has NO
  store-write path today. `set_task_status_if_current` `:195-202` writes status
  + appends a summary but NOT `child_workflow_id`. The atomic
  "RUNNING→WAITING_WORKFLOW + set child_workflow_id" (plan §6 step 3) needs
  EITHER (a) a new param `child_workflow_id` on `set_task_status_if_current`, OR
  (b) a dedicated `set_task_waiting_on_workflow(task_id, *, child_workflow_id)`
  method. **Recommend (a):** add `child_workflow_id: str | None = None` to
  `set_task_status_if_current` (and the concrete store), since the CAS is already
  the idempotency primitive and the flip+link is genuinely one transition. Flag
  in open_decisions — the plan never pins this.

CORE (protocol + concrete store, C-14).

---

### C-14. `backend/src/db/stores/task_center_store.py` — MN3 + child_workflow_id

Current (verified): `upsert_task` `:126-175` writes `summaries`,
`task_center_attempt_id`, `context_packet_id`, `fix_target_id`, `spawn_reason`.
`set_task_status` `:220-237` and `set_task_status_if_current` `:255-281` append
to `record.summaries`. `_serialize_task` reads `summaries`/`task_center_
attempt_id`/`spawn_reason` `:52,54,57`.

Target (MN3, D5):
- `upsert_task`: `summaries`→`outcomes`; add `terminal_tool_result`; drop
  `task_center_attempt_id`, `fix_target_id`, `spawn_reason`.
- `set_task_status`/`set_task_status_if_current`: write `outcomes`/
  `terminal_tool_result` instead of appending to `summaries`. **ADD**
  `child_workflow_id` write to `set_task_status_if_current` per C-13 decision.
- `_serialize_task` + the `TaskCenterTaskRecord` model (`db/models/
  task_center.py`) gain `outcomes`/`terminal_tool_result`/`child_workflow_id`,
  drop `summaries`/`task_center_attempt_id`/`spawn_reason`/`fix_target_id`.
  **The model + `_DROPPED_COLUMNS["task_center_tasks"]` edits are WS4/WS6-owned**
  (this cluster needs `child_workflow_id` added — flag the overlap).

This is heavily shared with WS4 (outcomes) — **COORDINATE**. This cluster's
unique ask: the `child_workflow_id` column + its atomic write path.

---

## PROPAGATION FILES (mechanical vocab/string-match only)

### P-1. `backend/src/task_center/workflow/__init__.py`
Docstring `:3-7` — drop `ancestry`, `closure_report_router` mentions; note state
moved to `_core/state.py`. No code.

### P-2. `backend/src/task_center/attempt/__init__.py`
`:3-15` re-exports `Attempt/AttemptFailReason/AttemptStage/AttemptStatus` from
`task_center.attempt.state`. After D11, source becomes `_core.state` (or a
re-export shim). Mechanical import path swap. (Body churn is WS6.)

### P-3. `backend/src/task_center/__init__.py`
- `:34,76` `AttemptDeps` source `attempt.deps`→`attempt.launch`.
- `:60,103-104` drop `WorkflowOrigin`/`WorkflowOriginKind` exports + the
  TYPE_CHECKING import `:60`.
- `:21` docstring cycle note: `terminal_tool_routing -> workflow.ancestry` →
  `terminal_routing` (ancestry gone).
- `:32,127-130` `ordered_generator_tasks`/`generator_dag` → `ordered_plan_tasks`/
  `plan_dag` (WS2/WS10 — coordinate).
- `:100` `EvaluatorSubmission` → `ReducerSubmission` (WS2 — coordinate).
- `:41` `context_engine.core` → `.engine` (WS10).
String-match repoints only.

### P-4. `backend/src/task_center_runner/core/runner.py` (`:127-137`)
Run-report dict builds `origin_kind`, `requested_by_task_id`, `final_outcome`
`:131-133` from the workflow. Per WS4/WS7 the report surfaces `workflow.status` +
derived `workflow.outcomes` (no `final_outcome`/`origin_kind`/
`requested_by_task_id`). Also `attempt.generator_task_ids` `:111` + the
`"task_ids"` key — fine, stays. This is a vocab/projection swap → propagation,
but the derived-`workflow.outcomes` computation is WS4-owned. **Flag overlap.**

### P-5. `backend/src/task_center_runner/audit/recorder.py` (`:106-111`)
Emits `origin_kind`/`requested_by_task_id`/`final_outcome`. Swap to
`parent_task_id` + status/outcomes. Mechanical (audit projection). WS9-owned;
listed here because it consumes my deleted fields.

### P-6. `backend/src/task_center_runner/scenarios/_scenario_helpers/workflow_origin.py`
Reads `workflow.origin_kind`/`requested_by_task_id` `:13-16` to classify
entry-vs-task. After origin removal, "entry vs task" collapses (root is just a
`:root` parent_task_id). This helper likely deletes or re-expresses via
`parent_task_id.endswith(":root")`. **Hand judgement needed → arguably CORE-lite,
but logic is test-harness classification; WS9-owned.** Listed for awareness.

### P-7. Mock scenario tests asserting deleted fields (WS9-owned; cross-reference)
`test_full_case_user_input.py:172,233-234`,
`test_deferred_parent_planner_terminal_routing.py:64-74`,
`test_scenario_loop_runner_planner_submit.py:106`,
`test_full_system_capacity_matrix.py:94-99`,
`test_full_stack_adversarial.py:203-211`,
`test_capacity_scenario_packs.py:128-138`,
`_focused_scenario_contracts.py:77-83`,
`test_runner_imports.py:217,248`,
`test_context_message_scenarios.py:36-45`.
All assert `origin_kind`/`requested_by_task_id`. These are WS9 propagation; this
cluster's deletions break them. Mechanical: switch to `parent_task_id`.

---

## OPEN DECISIONS (plan leaves unpinned)

1. **`child_workflow_id` atomic write path.** No store method writes it today.
   Recommend adding `child_workflow_id: str | None = None` to
   `set_task_status_if_current` (it's already the CAS idempotency primitive), so
   "RUNNING→WAITING_WORKFLOW + set link" is one transaction. Alternative: a
   dedicated `set_task_waiting_on_workflow` method. Plan §6 says "one
   transaction" but never names the method.

2. **`Workflow.id`→`workflow_id` rename scope.** Plan §2 table says
   `workflow_id`; current code uses `.id` everywhere. Is the rename in-scope for
   this refactor (WS6/WS10) or deferred? Affects every `created_workflow.id` /
   `workflow.id` site this cluster touches. Pick one and apply consistently.

3. **`run_close_handler` injection.** How does `WorkflowLifecycle` reach
   `RunController.on_root_workflow_closed`? Recommend injecting it as a callback
   into `WorkflowLifecycle.__init__` (mirroring the deleted `deliver_closure_
   report` seam at `lifecycle.py:63`), constructed in `starter._build_workflow_
   lifecycle`. The attempt-orchestrator routing is the other fork branch
   (registry lookup). Confirm the wiring owner: starter builds lifecycle, so
   starter must hold/forward the controller's close handler.

4. **`requested_by_task_id`→`parent_task_id`: drop+add vs rename in
   `db/engine.py`.** Plan says drop+add (new column). Data is semantically the
   same; rename would preserve dev rows. Per MEMORY (durable app DBs empty in
   dev), drop+add is safe. Following plan = drop+add; confirm.

5. **`_finish_run_if_open` ownership.** Move from `bootstrap.py:207-210` into
   `RunController` (recommended, since the seed failsafe is the controller's per
   §6) vs leave a thin copy in bootstrap. Pick one.

6. **Root recognition predicate.** "no attempt prefix" — concretely
   `parent_task_id.endswith(":root")` vs "no `:gen:`/`:planner`/`:red:` segment".
   Recommend the explicit `:root` suffix check (matches the seed id
   `"<run_id>:root"`) for the close-routing fork, and reuse the
   attempt-id-prefix parse for the nested-depth walk (C-12). Pin the helper
   location (likely `_core/primitives.py` alongside the task-id constructors
   `:24-33` — add `root_task_id(run_id)` + an `attempt_id_from_task_id(task_id)`
   parser).

---

## DRIFT (plan vs current code)

- **Plan WS6 line 376** lists deps.py importers but **OMITS**
  `tools/submission/context/attempt.py:11` and `attempt/launch.py:16`, both real
  importers of `AttemptDeps`/`AgentLaunch` (verified). Add them.
- **Plan §6 / WS8** says `starter.py:206-211` is `_mark_parent_waiting`'s
  `mark_waiting_workflow` call — verified the CALL is at `:206-211`, but the
  actual `mark_waiting_workflow` BODY lives in `deps.py:138-167`
  (`AttemptDelegatedWorkflowParentTask`), not in starter. The plan's "mirrors
  `starter.py:206-211`'s `_mark_parent_waiting`" is directionally right; the
  atomic flip+link logic to mirror is in `deps.py:157-162`.
- **Plan §6** "`starter.py:171` RUNNING / `:143` attempt-bound guards" — verified:
  `:171` is the RUNNING check in `_assert_parent_running_and_no_open_child`;
  `:143` is the attempt-bound rejection in `_prepare_origin` (`is not attempt-
  bound`). Both correct.
- **Plan §6 step 1** "`bootstrap.py:150-168`" for `_create_top_level_run` —
  verified exactly `:150-168`. "`bootstrap.py:132`" for the entry-origin start —
  verified the call is `:132-135` (`WorkflowStarter(...).start(origin=
  WorkflowOrigin.entry(...))`). "`bootstrap.py:137`" `_finish_run_if_open` —
  verified `:137` is the call, body at `:207-210`.
- **Plan WS7 line 411-416 (M1 orphan-guard)** says "Replaces the deleted
  `starter.py:265-279` synthetic-failed-report compensation" — verified that
  block is `:265-279` (the `WorkflowClosureReportRouter(...).deliver(...)`
  synthetic report). Correct.
- **Plan §2** Task field `child_workflow_id` + Workflow `parent_task_id`: neither
  exists on the current models (`db/models/task_center.py`,
  `db/models/workflow.py` has `requested_by_task_id`). Both are NEW.
- **Plan WS7 line 401** "set_status signature drops final_outcome" — verified
  `persistence.py:59-66` + `workflow_store.py:72-90` + `lifecycle.py:151-156`
  all pass `final_outcome`. Correct.
- The `apply_workflow_closure_report` Protocol decl in
  `orchestrator_registry.py:33` (verified) also needs updating to the new method
  names (`start/apply/cancel_child_workflow`) — **not mentioned in the plan's WS7
  seam list**; add it. The `RegisteredAttemptOrchestrator` Protocol `:25-33` is
  the contract the registry stores.
- `lifecycle.py` close path: the plan frames "workflow-close handler inspects
  parent_task_id" (§6 Resolve). Verified the close path is reached via
  `_route_iteration_closure → close_workflow → deliver_closure_report`
  (`lifecycle.py:198-224,157-158`). The fork replaces `deliver_closure_report`.
