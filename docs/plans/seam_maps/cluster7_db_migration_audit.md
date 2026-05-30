# Cluster 7 — WS6/DB migration + stores + audit (edit manifest)

Scope: DB migration choreography (`db/engine.py`), ORM models
(`db/models/{workflow,iteration,attempt,task_center}.py`), stores
(`db/stores/{workflow,iteration,attempt,task_center}_store.py`), and the
`task_center_runner/audit/*` projections (`recorder.py`, `node_id.py`,
`events.py`) plus `task_center/_core/audit.py`. Maps to plan WS2 / WS4 / WS6 /
WS7 / WS9 (audit) / WS8 (root path touches `task_center_store`).

Authoritative spec: `docs/plans/reducers_outcomes_redesign_PLAN.md`. Verified
against current code on 2026-05-30 at HEAD `fabce1b70`. Line anchors below are
the REAL current lines (I re-read each file), not the plan's numbers.

---

## CRITICAL CROSS-CLUSTER CONTRACT (read first)

The stores import and construct **DTOs** that live in *another* cluster's files
(`task_center/{workflow,iteration,attempt}/state.py` → consolidating into
`_core/state.py` per D11/WS6), and depend on the **Protocol** in
`task_center/_core/persistence.py`. My store edits MUST match the target DTO
field names that the state-consolidation cluster produces. The pinned target DTO
shapes (from plan §2) are reproduced in each store section so this cluster can be
executed without reading the other cluster's output — but the two clusters MUST
agree on these names:

| DTO | field renames (target, plan §2) |
|---|---|
| `Workflow` | `goal`→`workflow_goal`; ADD `parent_task_id: str\|None`; DROP `final_outcome`, `origin_kind`, `requested_by_task_id` (+ `WorkflowOrigin*`, `WorkflowClosureReport*`) |
| `Iteration` | `goal`→`iteration_goal`; `task_summary`→`outcomes` (json `list[Outcome]`); DROP `plan_spec` |
| `Attempt` | ADD `reducer_task_ids: tuple[str]`; DROP `evaluation_criteria`, `evaluator_task_id`, `plan_spec`; `deferred_goal`-backed field name unchanged (`deferred_goal_for_next_iteration`); `generator_task_ids` unchanged |

The DB **model PK column stays `id`** for all four record classes
(`WorkflowRecord.id`, `IterationRecord.id`, `AttemptRecord.id`,
`TaskCenterTaskRecord.id`). The plan's `id`→`task_id` (D5/FLAG-2) is the
**serialized-row dict key** + the **store kwarg/DTO field**, NOT the SQLAlchemy
column name. Renaming the column would require a `_RENAMED_COLUMNS` entry on a
primary key (risky, no benefit). Keep the column `id`; the `_serialize_task` dict
key becomes `task_id` (WS4/D5). See open_decisions OD-1.

---

## FILE 1 — `backend/src/db/engine.py`  (CORE: migration choreography)

### Current shape (verified)
- `_DROPPED_COLUMNS: dict[str, set[str]]` at **L40-69**. Tables present:
  `agent_runs`, `task_center_tasks` (already drops `summary` + 11 legacy cols),
  `task_center_runs`. **No `attempts`, `iterations`, or `workflows` keys yet.**
- `_RENAMED_COLUMNS: dict[str, dict[str, str]]` at **L71-78**. Currently:
  `iterations: {task_specification: plan_spec}`, `attempts: {task_specification: plan_spec}`.
- `_LEGACY_TABLES_TO_DROP: set[str]` at **L80-82** = `{"task_center_attempt"}`.
- `_rename_columns(engine)` L163-195: handles both pure rename and the
  "both columns exist → backfill new from old WHERE new IS NULL" merge case.
- `_add_missing_columns(engine)` L198-224: ADDs any ORM column not in DB, then
  DROPs stale columns (sqlite path rebuilds the table via `_rebuild_sqlite_table`).
- Migration order in `initialize_db` L280-289: `create_all` → `_rename_columns` →
  `_add_missing_columns` → `_drop_legacy_tables`.

### Target shape (plan WS2/WS4/WS7)
Add THREE keys to `_DROPPED_COLUMNS` and ONE rename to `_RENAMED_COLUMNS`.
ADDs (`reducer_task_ids`, `parent_task_id`) are handled automatically by
`_add_missing_columns` once the ORM model carries the column — **do NOT** put
ADDs in `_RENAMED_COLUMNS`.

```python
_DROPPED_COLUMNS = {
    # ... existing agent_runs / task_center_tasks / task_center_runs ...
    "task_center_tasks": {  # EXTEND the existing set (do not replace):
        ...existing...,                # keep "summary" + the 11 legacy cols
        "fix_target_id",               # WS6/D5
        "context_packet_id",           # WS6/D5
        "task_center_attempt_id",      # WS6/D5 (task_id now encodes the attempt)
        "spawn_reason",                # WS1/D5
        # NOTE: "summaries" is the rename source → outcomes (see _RENAMED_COLUMNS)
    },
    "attempts": {"evaluation_criteria", "evaluator_task_id", "plan_spec"},  # WS2 + §3
    "iterations": {"plan_spec"},                                            # WS4/§3
    "workflows": {"final_outcome", "origin_kind", "requested_by_task_id"},  # WS7
}

_RENAMED_COLUMNS = {
    "iterations": {"task_summary": "outcomes"},   # WS4 (REPLACE the task_specification entry)
    "task_center_tasks": {"summaries": "outcomes"},  # WS4/D5 (NEW)
    # attempts: REMOVE the task_specification→plan_spec entry (plan_spec is dropped)
}
```

### Risks / decisions
- **DRIFT-A (load-bearing):** WS2's DB bullet says only
  `attempts:{evaluation_criteria, evaluator_task_id}`, but §3's model annotation
  AND the Attempt DTO both drop `plan_spec` too. The attempts DROP set MUST
  include `plan_spec` (3 cols), otherwise a stale `plan_spec` column lingers and
  `_rebuild_sqlite_table` keeps copying it. Decided: include `plan_spec`. Flagged.
- **DRIFT-B:** plan §3 says `iterations.py(task_summary→outcomes; ✗plan_spec)`.
  So iterations RENAMES `task_summary→outcomes` AND DROPS `plan_spec`. Current
  `_RENAMED_COLUMNS["iterations"]` maps `task_specification→plan_spec`; since
  `plan_spec` is now dropped, **replace** that rename with `task_summary→outcomes`.
  Likewise `_RENAMED_COLUMNS["attempts"]["task_specification"]` must be removed.
- **DRIFT-C (memory note vs code):** auto-memory says engine has
  `_RENAMED_TABLES/_RENAMED_COLUMNS/_DROPPED_COLUMNS`. **There is no
  `_RENAMED_TABLES` in the current file** — only `_RENAMED_COLUMNS`,
  `_DROPPED_COLUMNS`, `_LEGACY_TABLES_TO_DROP`. No table rename is needed here
  (no DB table renames in this refactor; table names `workflows`/`iterations`/
  `attempts`/`task_center_tasks` are stable). The memory note is stale re: this
  file having `_RENAMED_TABLES`.
- **Per memory (confirmed correct):** enum string-VALUE changes (e.g.
  `evaluator→reducer` role value, `GENERATE/EVALUATE→RUN` stage value,
  `*_FAILED→TASK_FAILED`) need **NO** migration entry. Only column rename/drop
  needs engine code. Durable app DBs are empty in dev; real rows live only in
  disposable `task_center_runner/*.db` + `.sweevo_runs/` scratch (per memory
  `db_engine_no_enum_value_migration_hook`). So no value-migration shim is owed.
- **Backward-compat shim INTENTIONALLY kept:** `_RENAMED_COLUMNS` IS the
  migration shim for `summaries→outcomes` and `task_summary→outcomes`. Keep
  `_rename_columns`'s "both columns exist → backfill" branch — it lets a DB that
  already created the new `outcomes` column (via `create_all`) backfill from the
  legacy column. This pairs with WS4's `Outcome.from_record` reading legacy
  `"summary"` (a *value-shape* shim in `outcomes.py`, a different cluster).
- Migration ORDER is correct as-is: `create_all` adds the new `outcomes`/
  `parent_task_id`/`reducer_task_ids` columns first, then `_rename_columns`
  backfills `outcomes` from the legacy `summaries`/`task_summary`, then
  `_add_missing_columns` drops the stale legacy columns. Do not reorder.

Classification: **CORE** (data-table edits with semantic drift to resolve).

---

## FILE 2 — `backend/src/db/models/workflow.py`  (CORE)

### Current (verified)
- `WorkflowRecord` __tablename__ `"workflows"`, PK `id` (L23).
- L29 `origin_kind: Mapped[str|None]`; L30-32 `requested_by_task_id: Mapped[str|None]` (indexed);
  L33 `goal: Mapped[str]`; L36 `final_outcome: Mapped[dict|None]` (JSON).
- Module docstring L1-6 references `submit_execution_handoff(goal)`.

### Target (plan §3/WS7/WS6)
- DROP columns: `origin_kind`, `requested_by_task_id`, `final_outcome`.
- ADD column: `parent_task_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)`
  (backward link, plan §1/§2; mirrors old `requested_by_task_id` width/index —
  it effectively *replaces* it but is NOT a rename, per §4 vocab table line
  `requested_by_task_id`→`parent_task_id`). **DECISION OD-2:** model this as a
  DROP(`requested_by_task_id`)+ADD(`parent_task_id`), NOT a `_RENAMED_COLUMNS`
  rename — semantics differ (forward/backward link redesign) and the value of
  carrying old rows is nil in dev. WS7 DB bullet explicitly lists it under
  DROP + new `parent_task_id`, confirming the drop+add framing.
- `goal: Mapped[str]` column stays the **DB column name `goal`** (the DTO field
  renames to `workflow_goal`; the store `_to_dto` maps `workflow_goal=record.goal`).
  Renaming the DB column adds churn with no benefit; keep column `goal`. OD-3.
- Update docstring `submit_execution_handoff`→`submit_workflow_handoff`; drop the
  "entry" mention (no more entry origin).

Classification: **CORE** (column add/drop).

---

## FILE 3 — `backend/src/db/models/iteration.py`  (CORE)

### Current (verified)
- PK `id` (L22); `goal: Mapped[str]` (L30); `deferred_goal: Mapped[str|None]` (L34).
- L50-52 `plan_spec: Mapped[str|None]` (Text); L53 `task_summary: Mapped[str|None]` (Text).
- `UniqueConstraint(workflow_id, sequence_no)` L54-60.

### Target (plan §3/WS4)
- DROP column `plan_spec` (L50-52).
- RENAME column `task_summary` → `outcomes`, retype to JSON:
  `outcomes: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)`
  (was Text; now stores `list[Outcome]` json — WS4). Import `JSON` is already
  present (L11). The `_RENAMED_COLUMNS["iterations"]["task_summary"]="outcomes"`
  entry (FILE 1) handles the live-DB rename.
- DB column name `goal` stays (DTO field → `iteration_goal`).
- Keep `deferred_goal` column name (DTO field is `deferred_goal_for_next_iteration`;
  store maps it — unchanged from today).
- Update the L48-49 comment that says "task_summary" denormalization.

### Risk
- **Type change Text→JSON on rename:** sqlite is typeless so the
  `_rename_columns` ALTER RENAME + later JSON reads work; the
  `_rebuild_sqlite_table` path (triggered by the iterations DROP of `plan_spec`)
  recreates the table from ORM metadata with the JSON column, copying `outcomes`
  by name. Order: `_rename_columns` (task_summary→outcomes) runs BEFORE
  `_add_missing_columns` (which drops plan_spec and rebuilds), so `outcomes`
  exists as a copy_column at rebuild time. OK.

Classification: **CORE**.

---

## FILE 4 — `backend/src/db/models/attempt.py`  (CORE)

### Current (verified)
- PK `id` (L22); L31 `planner_task_id`; L32 `plan_spec: Mapped[str|None]`;
  L33 `evaluation_criteria: Mapped[list[str]]` (JSON, default list);
  L34 `generator_task_ids: Mapped[list[str]]` (JSON, default list);
  L35 `evaluator_task_id: Mapped[str|None]` (String(96)).
- Module docstring L1-5: "planner -> generator -> evaluator run".

### Target (plan §2/§3/WS2)
- DROP columns: `plan_spec` (L32), `evaluation_criteria` (L33), `evaluator_task_id` (L35).
- KEEP `generator_task_ids` (unchanged — JSON, default list).
- ADD column `reducer_task_ids: Mapped[list[str]] = mapped_column(JSON, default=list)`
  (NEW column; mirror `generator_task_ids` shape; **not** a rename of
  `evaluator_task_id`). `_add_missing_columns` adds it from ORM.
- Update docstring "evaluator"→"reducer"; "planner -> generator -> reducer".

### Risk
- The two-tuples design (C2): `generator_task_ids` + `reducer_task_ids`. Do NOT
  collapse to a single `node_task_ids` (D4 explicitly rejects that). Keep both
  JSON columns distinct so the run-stage scheduler unions them.

Classification: **CORE**.

---

## FILE 5 — `backend/src/db/models/task_center.py`  (CORE)

### Current (verified)
- `TaskCenterTaskRecord` __tablename__ `"task_center_tasks"`, PK `id: String(96)` (L76).
- L82 `role: String(32)`; L83 `agent_name`; L84 `context_message`; L85 `status`.
- L86 `summaries: Mapped[list[dict]]` (JSON, default list).
- L87 `needs: Mapped[list[str]]` (JSON, default list).
- L88-90 `task_center_attempt_id: Mapped[str|None]` (String(96)).
- L91 `context_packet_id: Mapped[str|None]` (String(36)).
- L92-95 `fix_target_id` + `spawn_reason`.
- L106-110 relationship `agent_run` (back_populates="task").
- `TaskCenterRequestRecord` (L21-44), `TaskCenterRunRecord` (L47-70) — both
  unchanged by this refactor; `TaskCenterRunRecord.status` is the run status the
  root path's `finish_run` writes (WS8).

### Target (plan §2 Task / D5)
- RENAME column `summaries` → `outcomes`, keep JSON:
  `outcomes: Mapped[list[dict]] = mapped_column(JSON, default=list)`.
  (`_RENAMED_COLUMNS["task_center_tasks"]["summaries"]="outcomes"` in FILE 1.)
- ADD column `terminal_tool_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)`
  (the raw terminal payload — WS4/D5; `_add_missing_columns` adds it).
- ADD column `child_workflow_id: Mapped[str | None] = mapped_column(String(36), nullable=True)`
  — forward link (plan §1/§2 `Task.child_workflow_id`; WS7 handoff). **DECISION
  OD-4:** this column is required by WS7's bidirectional link but is NOT in the
  §3 model-annotation shorthand; it IS in the §2 `Task` field list
  (`child_workflow_id: str|None`). Add it. `agent_run_id` is provided by the
  `agent_run` relationship today (no own column); leave as-is.
- DROP columns: `task_center_attempt_id`, `context_packet_id`, `fix_target_id`,
  `spawn_reason` (all in FILE 1's `_DROPPED_COLUMNS["task_center_tasks"]`).
- PK column **stays `id`** (OD-1). DTO/serialized key becomes `task_id`.

### Risk
- `list_tasks_for_attempt` / `list_generator_tasks_for_attempt` in the store
  filter on `task_center_attempt_id`. With that column gone, those queries must
  change to derive the attempt from the `id` prefix, OR be removed if no caller
  remains. See FILE 9.

Classification: **CORE**.

---

## FILE 6 — `backend/src/db/stores/workflow_store.py`  (CORE)

### Current (verified)
- Imports `WorkflowOrigin, WorkflowOriginKind, Workflow, WorkflowStatus` from
  `task_center.workflow.state` (L10-15) — repoint to `task_center._core.state`
  (WS6), and DROP `WorkflowOrigin`/`WorkflowOriginKind` imports (deleted).
- `insert(*, task_center_run_id, origin=None, requested_by_task_id=None, goal)`
  L21-51 — calls `_resolve_origin`, writes `origin_kind`, `requested_by_task_id`,
  `final_outcome=None`.
- `set_status(workflow_id, *, status, final_outcome, closed_at=None)` L72-90 —
  writes `record.final_outcome`.
- `list_for_parent_task(parent_task_id)` L92-102 — filters
  `WorkflowRecord.requested_by_task_id == parent_task_id`.
- `_to_dto` L118-131 — builds `Workflow(... origin_kind=..., requested_by_task_id=...,
  final_outcome=...)`.
- `_resolve_origin` helper L134-144 — DELETE entirely (origin concept gone).

### Target
- `insert(*, task_center_run_id, parent_task_id: str | None, workflow_goal: str)`:
  write `WorkflowRecord(id=uuid, task_center_run_id=..., parent_task_id=parent_task_id,
  goal=workflow_goal, status=OPEN, iteration_ids=[], created_at, updated_at)`.
  Remove `origin_kind`/`requested_by_task_id`/`final_outcome` writes. (WS8: the
  store keeps the DB column `goal`; param/DTO are `workflow_goal`.)
- `set_status(workflow_id, *, status: WorkflowStatus, closed_at=None)`:
  DROP the `final_outcome` param + write (plan WS7 / persistence.py L59).
- `list_for_parent_task` → filter `WorkflowRecord.parent_task_id == parent_task_id`.
- `_to_dto` → `Workflow(id=..., task_center_run_id=..., workflow_goal=record.goal,
  status=..., iteration_ids=..., parent_task_id=record.parent_task_id,
  created_at, updated_at, closed_at)`. Drop `origin_kind`/`requested_by_task_id`/
  `final_outcome`.
- DELETE `_resolve_origin`.

### Risk
- Callers of `insert(origin=...)` and `set_status(final_outcome=...)` live in the
  workflow-lifecycle cluster (`workflow/starter.py:254`, `workflow/lifecycle.py:151`).
  This store change must land in lockstep with those callers + the
  `_core/persistence.py` Protocol (L46-66) signature drop of `final_outcome` /
  `origin`. Coordinate: the Protocol edit is in WS6 (persistence.py).

Classification: **CORE** (signature + DTO changes, deletes a helper).

---

## FILE 7 — `backend/src/db/stores/iteration_store.py`  (CORE)

### Current (verified)
- Imports from `task_center.iteration.state` (L10-14) — repoint to `_core.state`.
- `insert(... goal, ...)` L20-47 writes `goal=goal`.
- `close_succeeded(iteration_id, *, plan_spec, task_summary, closed_at=None)`
  L126-152 — writes `record.plan_spec`, `record.task_summary`.
- `_to_dto` L154-170 — builds `Iteration(... goal=record.goal,
  deferred_goal_for_next_iteration=record.deferred_goal, plan_spec=..., task_summary=...)`.

### Target (plan WS4)
- `insert`: param stays `goal` at the store boundary OR rename to
  `iteration_goal`? **DECISION OD-5:** rename the store kwarg to `iteration_goal`
  to match the DTO field + planner recipe vocab (§4 `goal`→`iteration_goal`);
  write `record.goal = iteration_goal` (DB column stays `goal`). Coordinate with
  `IterationStoreProtocol.insert` (persistence.py L76-84) and the
  `attempt_coordinator`/`starter` callers (other clusters). If the consolidation
  cluster keeps the kwarg `goal`, defer — flag OD-5.
- `close_succeeded(iteration_id, *, outcomes: list[dict], closed_at=None)`:
  DROP the `plan_spec` param; rename `task_summary`→`outcomes`; write
  `record.outcomes = outcomes`. (Plan WS4: iteration.outcomes is the persisted
  canonical reducer-outcomes projection; failure-aware writes go through the same
  setter — confirm with attempt_coordinator cluster whether a separate
  `close_failed(outcomes, fail_reason)` is needed; this store only needs the
  column setter.)
- `_to_dto` → `Iteration(... iteration_goal=record.goal,
  deferred_goal_for_next_iteration=record.deferred_goal,
  outcomes=record.outcomes)`. Drop `plan_spec`, drop `task_summary`.
- Protocol (`persistence.py` L102-109) `close_succeeded` signature: drop
  `plan_spec`, rename `task_summary`→`outcomes` (coordinate WS6).

### Risk
- The iteration DTO's `outcomes` is `list[Outcome]`, but the store reads/writes
  raw json (`list[dict]`). Whether the store deserializes to `Outcome` objects or
  hands raw dicts to the DTO is an Outcome-cluster decision; this store should
  pass `record.outcomes` (raw list) and let the DTO/`Outcome.from_record`
  reconstruct (mirrors how `summaries` was raw json today). Flag OD-6.

Classification: **CORE**.

---

## FILE 8 — `backend/src/db/stores/attempt_store.py`  (CORE)

### Current (verified)
- Imports from `task_center.attempt.state` (L10-15) — repoint to `_core.state`.
- `insert` L21-45 writes `plan_spec=None, evaluation_criteria=[], generator_task_ids=[],
  evaluator_task_id=None`.
- `set_plan_contract(attempt_id, *, plan_spec, evaluation_criteria, deferred_goal_for_next_iteration)`
  L64-81 — writes `plan_spec` + `evaluation_criteria`.
- `set_evaluator_task_id(attempt_id, evaluator_task_id)` L95-105.
- `_to_dto` L163-184 — builds `Attempt(... plan_spec=..., evaluation_criteria=...,
  generator_task_ids=..., evaluator_task_id=...)`.

### Target (plan WS2/C2)
- `insert`: write `generator_task_ids=[], reducer_task_ids=[]`; drop
  `plan_spec=None, evaluation_criteria=[], evaluator_task_id=None`.
- Replace `set_plan_contract(... plan_spec, evaluation_criteria, ...)` with a
  setter that records `deferred_goal_for_next_iteration` only (plan_spec +
  evaluation_criteria are gone). **DECISION OD-7:** the planner now submits
  reducers as plan tasks (their ids land in `reducer_task_ids` via a separate
  setter), and there is no `plan_spec`/`evaluation_criteria` to store. Rename
  `set_plan_contract`→`set_deferred_goal` (or keep the name and shrink it to one
  param). Pin: provide `set_reducer_task_ids(attempt_id, task_ids)` mirroring
  `set_generator_task_ids`, and a `set_deferred_goal_for_next_iteration(attempt_id,
  deferred_goal)` setter. Coordinate with the orchestrator/plan-dag cluster that
  calls these. Flag OD-7.
- Replace `set_evaluator_task_id` → `set_reducer_task_ids(attempt_id, task_ids)`
  (list, not scalar — reducers are ≥1, plural like generators). Writes
  `record.reducer_task_ids = list(task_ids)`.
- `_to_dto` → `Attempt(... generator_task_ids=tuple(record.generator_task_ids or ()),
  reducer_task_ids=tuple(record.reducer_task_ids or ()), ...)`. Drop `plan_spec`,
  `evaluation_criteria`, `evaluator_task_id`.
- Protocol (`persistence.py` L114-149): `set_evaluator_task_id`→`set_reducer_task_ids`;
  `set_plan_contract` signature shrinks (drop plan_spec + evaluation_criteria);
  coordinate WS6.

Classification: **CORE**.

---

## FILE 9 — `backend/src/db/stores/task_center_store.py`  (CORE)

### Current (verified)
- `_serialize_task(record)` L44-60 — dict keys `"id"`, `"summaries"`,
  `"task_center_attempt_id"`, `"context_packet_id"`, `"fix_target_id"`, `"spawn_reason"`.
- `upsert_task(*, task_id, task_center_run_id, role, context_message, status,
  summaries, needs, task_center_attempt_id, agent_name=None, context_packet_id=None,
  fix_target_id=None, spawn_reason=None)` L126-175 — writes all of the above.
- `list_tasks_for_attempt` L191-203, `list_generator_tasks_for_attempt` L205-218 —
  filter on `task_center_attempt_id`.
- `set_task_status(task_id, *, status, summary=None)` L220-237 — appends `summary`
  to `record.summaries`.
- `set_task_status_if_current(task_id, *, expected_status, status, summary=None)`
  L255-281 — appends `summary` to `record.summaries`.
- `set_task_context_packet_id(task_id, *, context_packet_id)` L239-253.

### Target (plan §2/D5/WS4/WS6/MN3)
- `_serialize_task`: dict key `"id"`→`"task_id"` (D5/FLAG-2, value still
  `record.id`); `"summaries"`→`"outcomes"` (value `record.outcomes or []`); ADD
  `"terminal_tool_result": record.terminal_tool_result`; ADD `"child_workflow_id":
  record.child_workflow_id`; DROP `"task_center_attempt_id"`, `"context_packet_id"`,
  `"fix_target_id"`, `"spawn_reason"`.
  **Coordination risk:** every reader of the serialized row dict that does
  `row["id"]` / `row["summaries"]` / `row["task_center_attempt_id"]` must switch to
  `row["task_id"]` / `row["outcomes"]` and derive attempt from the id prefix. The
  audit recorder's `_serialize_task` (FILE 11) is a SEPARATE serializer over the
  ORM record and also needs the same key changes. (See FILE 11 + DRIFT-D.)
- `upsert_task`: rename kwarg `summaries`→`outcomes`; ADD kwarg
  `terminal_tool_result: dict | None = None` and `child_workflow_id: str | None = None`;
  DROP kwargs `task_center_attempt_id`, `context_packet_id`, `fix_target_id`,
  `spawn_reason`. Write `record.outcomes = outcomes`,
  `record.terminal_tool_result = terminal_tool_result`,
  `record.child_workflow_id = child_workflow_id`. **MN3 (WS6):** this kwarg change
  MUST land BEFORE the submit path + run controller call it, or they pass removed
  kwargs. Current internal callers: `attempt/stage_advancer.py:232`,
  `attempt/orchestrator.py:103,284` pass `summaries=[]` — those are other-cluster
  files that must switch to `outcomes=[]` in lockstep (they are propagation-ish
  but live in orchestrator cluster, not here).
- `set_task_status(task_id, *, status, outcome=None)` (rename `summary`→`outcome`
  param? **DECISION OD-8:** the param appends one entry to the list. Rename
  `summary`→`outcome` to match vocab, append to `record.outcomes`. Coordinate the
  Protocol (`persistence.py` L193) + callers. If churn too high, keep `summary`
  kwarg name and just retarget to `record.outcomes` — flag OD-8.)
- `set_task_status_if_current`: same param-rename + retarget to `record.outcomes`
  (this is the WS7 orphan-guard M1 setter — keep it; only the field changes).
- DROP `set_task_context_packet_id` (column gone). **CAUTION:** it has a LIVE
  caller `task_center/attempt/stage_advancer.py:166` (other cluster). Removing
  this method depends on the stage-advancer rewrite (`stage_advancer.py`→
  `run_stage.py`) dropping context-packet wiring. Do NOT delete the store method
  until that caller is gone, or stage-advance breaks. **DRIFT-E:** plan removes
  `context_packet_id` (D5) so this method + its Protocol entry (persistence.py
  L204) + the `stage_advancer.py:166` call are a coordinated 3-site deletion.
- `list_tasks_for_attempt` / `list_generator_tasks_for_attempt`: the
  `task_center_attempt_id` column is gone. CONFIRMED task-id scheme
  (`_core/primitives.py` L24-33): `planner_task_id = f"{attempt_id}:planner"`,
  `generator_task_id = f"{attempt_id}:gen:{local}"`, `evaluator_task_id =
  f"{attempt_id}:evaluator"` (→ WS1 adds `reducer_task_id = f"{attempt_id}:red:{local}"`).
  So `attempt_id` IS the literal id prefix. **DECISION OD-9:** rewrite the filter
  to `TaskCenterTaskRecord.id.like(f"{attempt_id}:%")` (and for generators, also
  `role == "generator"`). The root bootstrap task `"<run_id>:root"` has no
  attempt prefix so it never matches an `attempt_id` LIKE — correct. If the
  orchestrator rewrite (other cluster) drops the list-by-attempt callers
  entirely, delete these methods instead. Flag OD-9 to confirm caller survival.
- `Protocol` updates (persistence.py L172-204): `upsert_task` kwargs, `set_task_status`
  param, drop `set_task_context_packet_id`. Coordinate WS6.

Classification: **CORE** (key renames + filter logic + method deletion).

---

## FILE 10 — `backend/src/task_center/_core/audit.py`  (CORE-light)

### Current (verified)
- Event-name constants `TASK_READY/TASK_LAUNCHED/TASK_FAILED` (L17-19).
- `task_failed(..., summary: str = "")` L79-98 — payload key `"summary"`.
- `_task_node(task, *, attempt_id)` L101-107 — reads
  `task.get("task_center_attempt_id")` and `task.get("id")`.
- `_task_payload(task)` L110-119 — reads `task.get("task_center_attempt_id")`,
  `task.get("id")`, `task.get("context_packet_id")`.

### Target (plan WS9 audit)
- `_task_node`: `task.get("id")` → `task.get("task_id")` (serialized key rename
  D5); `attempt_id or task.get("task_center_attempt_id")` → derive attempt from
  the task_id prefix OR just use the passed `attempt_id` arg (the
  `task_center_attempt_id` serialized key is gone). **DECISION OD-10:** keep the
  `attempt_id` param (callers pass it), drop the `task.get("task_center_attempt_id")`
  fallback. Flag OD-10.
- `_task_payload`: `task.get("id")`→`task.get("task_id")`; drop
  `task.get("task_center_attempt_id")` → use passed attempt or omit;
  drop `"context_packet_id"` key (column gone). Optionally rename payload key
  `"summary"`→? in `task_failed` (the failure text). **DECISION OD-11:** the
  `task_failed(summary=...)` arg is a free-text failure string, not the outcomes
  list — keep param name OR rename to `fail_text`/`failure`. Lowest-churn: keep
  `summary` param (it is a payload label, not the storage field). Flag OD-11.
- `AuditNode` field is `attempt_id` (from `audit.base`) — unchanged; only the
  source lookups change.

### Risk
- This file's `task_failed`/`task_ready`/`task_launched` are called with the
  serialized task row dict. Whether callers pass `row["id"]` or `row["task_id"]`
  depends on FILE 9's serializer rename landing first. Land FILE 9 + FILE 10
  together.

Classification: **CORE** (small, but reads renamed serialized keys).

---

## FILE 11 — `backend/src/task_center_runner/audit/recorder.py`  (CORE + propagation)

### Current (verified) — this is the heaviest audit file
- `PRIMARY_ROLES = frozenset({"planner", "executor", "verifier", "evaluator"})` L86-88.
- `_ATTEMPT_CHILD_ROLES = frozenset({"planner","executor","verifier","evaluator","generator"})` L93-95.
- `_serialize_workflow(record)` L102-115 — keys `origin_kind`, `requested_by_task_id`,
  `goal`, `final_outcome`.
- `_serialize_iteration(record)` L118-134 — keys `goal`, `deferred_goal`,
  `plan_spec`, `task_summary`.
- `_serialize_attempt(record)` L137-154 — keys `plan_spec`, `evaluation_criteria`,
  `generator_task_ids`, `evaluator_task_id`.
- `_serialize_task(record)` L157-173 — keys `id`, `summaries`,
  `task_center_attempt_id`, `context_packet_id`, `fix_target_id`, `spawn_reason`.
- `_resolve_task_dir(target)` L672-686 — reads `target.task_center_attempt_id`.
- `_display_role(target)` L688-695 — special-cases `agent_name in {"executor","verifier"}`.

### Target (plan WS3/WS9 audit)
- `PRIMARY_ROLES`: drop `"verifier"` AND `"evaluator"`; add `"reducer"`.
  Target: `frozenset({"planner", "executor", "reducer"})`. (WS3 removes verifier;
  WS1 renames evaluator→reducer. The role value is the message-recorder allowlist
  — reducer agents should get a `message.jsonl`.) **DECISION OD-12:** confirm
  reducer is a "primary" role that runs through the engine loop (it does — it's a
  real agent with terminals). Include `"reducer"`. Flag OD-12.
- `_ATTEMPT_CHILD_ROLES`: drop `"verifier"`, `"evaluator"`; add `"reducer"`.
  Target: `frozenset({"planner", "executor", "reducer", "generator"})`.
- `_serialize_workflow`: drop `origin_kind`, `requested_by_task_id`, `final_outcome`;
  add `parent_task_id`. (Mirrors FILE 2.)
- `_serialize_iteration`: drop `plan_spec`; rename `task_summary`→`outcomes`. (FILE 3.)
- `_serialize_attempt`: drop `plan_spec`, `evaluation_criteria`, `evaluator_task_id`;
  add `reducer_task_ids`. (FILE 4.)
- `_serialize_task`: key `id`→`task_id`; `summaries`→`outcomes`; drop
  `task_center_attempt_id`, `context_packet_id`, `fix_target_id`, `spawn_reason`;
  add `terminal_tool_result`, `child_workflow_id`. (FILE 5/9.)
- `_resolve_task_dir`: `target.task_center_attempt_id` is gone. Derive the attempt
  from `target.id`. Given the CONFIRMED scheme (FILE 9: `{attempt}:planner`,
  `{attempt}:gen:{local}`, `{attempt}:red:{local}`), the attempt id is NOT a
  simple `rsplit(":",1)` (gen/red have a 3-part id). **DECISION OD-13:** parse by
  the role suffix — strip the trailing `:planner` / `:gen:{local}` / `:red:{local}`
  / (legacy `:evaluator`). A robust parse: `attempt_id = target.id.split(":gen:")[0]`
  for generators, `.split(":red:")[0]` for reducers, `removesuffix(":planner")`
  for planners. Better: add a `attempt_id_of(task_id)` helper in primitives
  (other cluster, WS1) and import it here. The root task `"<run_id>:root"` has no
  attempt → `_resolve_task_dir` returns None (it is not an `_ATTEMPT_CHILD_ROLES`
  member under an attempt dir). Flag OD-13 to pin the helper location.
- `_display_role`: drop the `"verifier"` branch; the `"executor"` mapping for
  generators stays (executor is the only generator profile per §1). Target:
  `if target.role == "generator" and target.agent_name == "executor": return "executor"`.
- Module docstring mentions five listeners over the four record classes — keep;
  no listener-count change (still Workflow/Iteration/Attempt/Task + AgentRun).

### Risk / DRIFT-D
- **Two parallel `_serialize_task` implementations exist** — one in
  `db/stores/task_center_store.py` (FILE 9) and one here (FILE 11). They are
  independent (this one reads the ORM record directly; the store one too). BOTH
  must get the same key renames. The plan does not call this out; flagged.
- The audit recorder reads ORM record attributes directly (`record.summaries`,
  `record.task_center_attempt_id`, `record.plan_spec`, etc.). Once FILES 2-5 drop
  those columns, accessing `record.<dropped>` raises `AttributeError`. So FILE 11
  MUST land in the SAME change as the model edits — it is NOT optional or
  deferrable. This is the tightest coupling in the cluster.

Classification: **CORE** for the `_resolve_task_dir`/`_display_role`/role-set
logic; the four `_serialize_*` functions are **propagation** (mechanical key
renames mirroring the models) but live in the same file, so treat the file as
CORE for sequencing.

---

## FILE 12 — `backend/src/task_center_runner/audit/node_id.py`  (CORE-light)

### Current (verified)
- `PrimaryRole = Literal["planner", "executor", "verifier", "evaluator"]` L12-17.
- `NodeId.agent_role: PrimaryRole | None` L31.

### Target (plan WS3/WS9: `node_id.py:15`)
- `PrimaryRole = Literal["planner", "executor", "reducer"]` — drop `"verifier"`
  and `"evaluator"`, add `"reducer"` (consistent with recorder PRIMARY_ROLES).
- `NodeId` field unchanged in name; the Literal type narrows.

### Risk
- Any emitter passing `agent_role="evaluator"`/`"verifier"` becomes a type error.
  Grep `agent_role=` across `task_center_runner` to find call sites (likely none
  hardcode it; it's filled from `record.role`). Flag.

Classification: **CORE-light** (one Literal edit).

---

## FILE 13 — `backend/src/task_center_runner/audit/events.py`  (NO-OP / verify)

### Current (verified)
- `EventType` enum L44-92. Lifecycle members: `ATTEMPT_PASSED`/`ATTEMPT_FAILED`
  (L57-58) — these are STATUS names, unaffected (status vocab `PASSED/FAILED`
  unchanged). No `evaluator`/`verifier`/`summary`/`outcome` string literals in
  the enum values.
- `Event` dataclass L95-103.

### Target
- **No required change.** The plan's WS9 lists "events.py" in the audit projection
  bullet, but the enum has no role/summary vocabulary to rename. The
  `node_id.PrimaryRole` import (L41) is the only coupling, handled in FILE 12.
- Optional: if a new lifecycle event for reducer is wanted, none is specified;
  do nothing.

Classification: **propagation / verify-only** (no edit expected; confirm grep is
clean).

---

## Migration shims KEPT vs surfaces DELETED (informs the cleanup stage)

**KEPT (intentional backward-compat):**
- `db/engine.py` `_RENAMED_COLUMNS` mechanics + the "both columns exist →
  backfill" branch in `_rename_columns` — the live-DB rename shim for
  `summaries→outcomes` (task_center_tasks) and `task_summary→outcomes` (iterations).
- `_rebuild_sqlite_table` — unchanged; it is the generic drop-column path that the
  new DROP sets ride on.
- (Value-shape shim, OTHER cluster:) `Outcome.from_record` reads legacy `"summary"`
  key — pairs with the column rename here.

**DELETED (replaced surfaces — cleanup-stage targets):**
- `_resolve_origin` helper (workflow_store.py L134-144) — origin concept gone.
- `set_task_context_packet_id` (task_center_store.py L239-253) + its Protocol entry.
- `set_plan_contract`'s plan_spec/evaluation_criteria params; `set_evaluator_task_id`.
- `close_succeeded`'s `plan_spec` param.
- `final_outcome` everywhere (model col, store writes, DTO, serializers, Protocol).
- `WorkflowOrigin`/`WorkflowOriginKind` imports in workflow_store.
- The `attempts`/`iterations` `task_specification→plan_spec` `_RENAMED_COLUMNS`
  entries (now obsolete — plan_spec dropped).
- `_RENAMED_TABLES` — does not exist; nothing to delete (DRIFT-C).

---

## Partition summary

CORE (hand-edited logic): engine.py, models {workflow,iteration,attempt,task_center}.py,
stores {workflow,iteration,attempt,task_center}_store.py, _core/audit.py,
audit/recorder.py, audit/node_id.py.

PROPAGATION (mechanical key/string renames mirroring the models): the four
`_serialize_*` helpers inside recorder.py (same file as CORE, so sequenced with
it); events.py (verify-only).
