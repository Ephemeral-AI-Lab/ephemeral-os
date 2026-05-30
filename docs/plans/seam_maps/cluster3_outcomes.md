# Seam Map — Cluster WS4: Unified Outcome algebra; retire `summary`

**Plan source:** `docs/plans/reducers_outcomes_redesign_PLAN.md` §1 (outcomes algebra),
§2 (class ref), §4 (vocab table), §7 WS4, §10 (round-trip verify).
**Scope partition (D5/D15/MN2/MN3):** define the `Outcome` dataclass in
`_core/outcomes.py` (← `generator_summaries.py`), retire `TaskOutcome.summary`→`text`,
remove `latest_task_summary`, rewire the submit path to write `outcomes` +
`terminal_tool_result`, project **reducer** outcomes into `iteration.outcomes`
(failure-aware), surface `workflow.outcomes` in the run report, and migrate the
`Task`/`Iteration`/`Workflow` storage columns + store signatures.

**Cross-cluster contract (READ THIS FIRST):**
- WS1/WS2 own the `Attempt` DTO change `evaluator_task_id`→`reducer_task_ids`
  (`attempt/state.py`, store, `AttemptStoreProtocol`). WS4 only *consumes*
  `attempt.reducer_task_ids` in the new `_iteration_outcomes_for`. Treat
  `attempt.reducer_task_ids: tuple[str,...]` as a precondition; if it does not exist yet
  at edit time, the aggregation helper cannot be written — coordinate ordering (§12 of the
  plan: WS1→WS2→WS4 back-to-back).
- WS6 owns moving `Workflow`/`Iteration`/`Attempt` DTOs into `_core/state.py`. WS4 adds
  the **`outcomes` field** to the `Iteration` DTO and the **`outcomes` derivation** on
  `Workflow`; whichever cluster lands first defines the dataclass, the other repoints. This
  manifest specifies the *field shape*; the *file location* is WS6's call.
- WS7 owns closure removal. WS4's `_handoff_rollup` / `child_outcomes_for_workflow` /
  `_build_handoff_rollup` / `to_final_outcome` are **handoff/closure** concerns and are
  REMOVED/RESHAPED by WS7 (MN2 child-outcome nesting). WS4 keeps the `Outcome.children`
  field + `to_record`/`from_record` nesting; WS7 supplies the children at handoff time.
  See "Cross-ref WS7" notes inline.
- The context-engine recipe consumers of `TaskOutcome.summary`
  (`recipes/_task_xml.py`, `recipes/iterations.py`, `recipes/attempts.py`,
  `recipes/generator.py`) are WS5/context-recipe territory. The `summary`→`text` field
  rename **cascades** into them; this manifest lists them as propagation so the
  field-rename does not silently break them, but the recipe *logic* edits belong to the
  context cluster.

---

## DRIFT (plan line claims vs current code)

| Plan claim | Reality |
|---|---|
| WS4: "run report surfaces `workflow.status` + derived `workflow.outcomes` (`task_center_runner/core/runner.py:130-133`)" | The only `runner.py` under `task_center_runner` is `core/runner.py`, the **mock scenario** shim. The consumer block is `_graph_summary` **lines 127-137** (the `workflows.append({...})` dict), not 130-133. It currently emits `origin_kind` (127:131), `requested_by_task_id` (132), `final_outcome` (133). There is no separate production "run report" module here — this IS the report surface for WS4's verify. |
| WS4: "The submit path (`orchestrator.py:~348` `_write_submission_status`)" | `_write_submission_status` is at **orchestrator.py:327-349**; the `set_task_status` call with the `summary={...}` dict is at **345-349**. Close. |
| WS4: "`_achieved_record_for`→`_iteration_outcomes_for` projects reducer outcomes (`iteration/attempt_coordinator.py:215-223`)" | `_achieved_record_for` is at **attempt_coordinator.py:215-223** exactly. It currently projects **generator** outcomes via `generator_outcomes(attempt, …)`. Target = **reducer** outcomes. |
| WS4: "Persistence: `Iteration.task_summary`→`outcomes` (`_RENAMED_COLUMNS` += `iterations:{task_summary→outcomes}`)" | `_RENAMED_COLUMNS` (db/engine.py:71) currently maps only `task_specification→plan_spec` for `iterations`/`attempts`. The `task_summary` column is real (iteration.py:53). Need to ADD the rename entry. |
| WS7/WS6: "`_core/persistence.py:59` (the `set_status` signature drops `final_outcome`)" | `_core/persistence.py` is a **Protocols** file (no module-level functions). Line **59** is `WorkflowStoreProtocol.set_status` (kwarg `final_outcome`); line **90** is `IterationStoreProtocol.set_status` (no `final_outcome`, only `status`+`closed_at`). The "drop `final_outcome`" edit targets the **WorkflowStoreProtocol.set_status** at :59. Owned by WS7 but listed here because WS4's `iteration.outcomes` write goes through `IterationStoreProtocol.close_succeeded` (:102) / `set_status` (:90). |
| WS4: "Task `summaries`→`outcomes` + `terminal_tool_result`" | The legacy `summary` column is ALREADY in `_DROPPED_COLUMNS["task_center_tasks"]` (db/engine.py:61) from a prior migration — that's a *different* column (old single `summary`, not `summaries`). The live JSON column is `summaries` (task_center.py:86). No existing drop/rename for `summaries`. |
| MN2 "handoff generator emits ONE Outcome whose children = child workflow's outcome list" | Today handoff nesting is done via `payload.handoff_rollup` read by `_handoff_rollup` (generator_summaries.py:226) + `_build_handoff_rollup` (orchestrator.py:215). Under MN2 the generator's `outcomes` list IS the single nesting `Outcome`; the rollup-via-payload indirection is replaced by WS7's `apply_child_workflow_outcome` writing `outcomes` directly. WS4 keeps only the `Outcome.children` + record nesting; the *plumbing* is WS7. |

---

## CORE FILES (hand-edited logic)

### 1. `backend/src/task_center/_core/generator_summaries.py` → `_core/outcomes.py` (RENAME + REWRITE)
**Current shape (verified):**
- `TaskOutcome` frozen dataclass: `local_id, status, summary: str|None, children: tuple[TaskOutcome,...]=(), failure: str|None=None, raw_status: str|None=None`; `is_terminal` property (lines 49-69).
- `latest_task_summary(summaries)` (72-84) — REMOVE per plan.
- `present_status` (87-94), `local_id_of` (97-103) — keep.
- `task_outcome_from_row(task_id, task)` (106-123) — reads `task.get("summaries")`, calls `latest_task_summary` + `_handoff_rollup`.
- `generator_outcomes(attempt, *, task_store)` (126-135) — iterates `attempt.generator_task_ids`.
- `attempt_failure_line(attempt, task_store)` (138-156) — switches on `AttemptFailReason.{STARTUP_FAILED,PLANNER_FAILED,EVALUATOR_FAILED,GENERATOR_FAILED}`.
- `to_record(outcome)` (162-173): emits `{local_id, status, summary, children?, failure?}`.
- `from_record(record)` (176-190): reads `summary`/`failure`/`children`.
- `parse_achieved_record(task_summary)` (193-208): JSON list → outcomes; **legacy free-text → single `TaskOutcome(local_id="summary", status="success", summary=text)`**.
- `child_outcomes_for_workflow(workflow_id, iteration_store)` (211-220) — **Cross-ref WS7** (handoff/closure).
- `_handoff_rollup` (226-245), `_is_terminated` (248-258), `_stage_failure_line` (261-270), `_generator_failure_lines` (273-288).
- Imports (21-23): `from task_center.attempt.state import Attempt, AttemptFailReason`; `from task_center.iteration.state import IterationStatus`; `TERMINAL_GENERATOR_STATUSES`.

**Target shape:**
1. **Rename file** `generator_summaries.py`→`outcomes.py`. Update its 4 src importers + 4 test importers (see propagation).
2. **`TaskOutcome`→`Outcome`**, field `summary`→`text`. Final fields per §2:
   `Outcome : { local_id: str, status: str, text: str|None, children: tuple[Outcome,...]=(), failure: str|None=None, raw_status: str|None=None }`. Keep `is_terminal`.
3. **Remove `latest_task_summary`**. Its three former call sites
   (`task_outcome_from_row`, `_stage_failure_line`, `_evaluator_summary_if_ran` in attempts.py)
   must inline "last summaries entry's text" OR read off the new `outcomes` projection.
   DECISION (open_decisions #1): inline a private `_text_of(summaries)` in this module
   *or* have readers project `Outcome` directly. Plan §7 WS4 says "readers project `Outcome`s
   directly" — so the `attempts.py`/`task_outcome_from_row` readers should build an `Outcome`
   and read `.text`. Recommend: keep a tiny private `_latest_text(rows)` helper (NOT exported)
   to avoid duplicating the `summaries[-1]` dict-walk in 3 places.
4. **`to_record`**: emit `text` key (not `summary`). MN2: when `children` present, nest them
   (already does). For a handoff generator the single `Outcome.children` = child workflow's
   outcome list — `to_record` already recurses; no change beyond the key rename.
5. **`from_record` legacy "summary" path (MN2-adjacent, plan §10 round-trip):**
   read `record.get("text")` **falling back to** `record.get("summary")` so pre-migration
   serialized rows (which used `summary`) round-trip. Concretely:
   `text = record.get("text"); text = record.get("summary") if text is None else text`.
   This is THE "from_record reads legacy 'summary' for pre-migration rows" requirement.
6. **`parse_achieved_record`** legacy free-text branch: keep, but build
   `Outcome(local_id="summary", status="success", text=str(task_summary))`.
   Rename param `task_summary`→`outcomes_json` (or leave; it now reads `iteration.outcomes`).
   **DECISION (open #2):** keep this function name or rename to `parse_outcomes_record`?
   Plan §4 retires `task_summary` but does not pin this fn name. Recommend
   `parse_outcomes_record` for vocab coherence; flag it.
7. **`attempt_failure_line`**: `AttemptFailReason` collapses to `TASK_FAILED | STARTUP_FAILED`
   (WS2/§4). So the `PLANNER_FAILED`/`EVALUATOR_FAILED`/`GENERATOR_FAILED` branches collapse:
   `STARTUP_FAILED → "agent_launch_failed"`; `TASK_FAILED →` render the failed *task(s)* —
   role of the failed task says which. **Cross-ref WS2** (it owns the enum). WS4 must rewrite
   the body to read the failed tasks (any role) generically rather than per-role branches.
   `_stage_failure_line`/`_generator_failure_lines` fold into one
   `_failed_task_lines(attempt, task_store)` over the union of plan task ids
   (generator+reducer). **DECISION (open #3):** the failed-task-line generalization is
   shared with WS5 retry rendering ("generalizes `attempt_failure_line`"); pin that this
   helper lives in `outcomes.py` and WS5's planner recipe calls it.
8. `EVALUATOR_FAILED` branch + `_evaluator_summary_if_ran` references die with the evaluator role.
9. **`is_terminal`** uses `TERMINAL_GENERATOR_STATUSES`; reducers terminate on DONE/FAILED too —
   confirm the terminal-status set covers reducer terminals (cross-ref WS1 `task_state.py`).
10. **`child_outcomes_for_workflow` / `_handoff_rollup`** — Cross-ref WS7. WS7 removes
    `_handoff_rollup` (payload indirection) and reshapes `child_outcomes_for_workflow` into the
    `workflow.outcomes` derivation (last-iteration). WS4 must NOT independently delete these;
    coordinate so the `Outcome` type rename lands first, WS7 reshapes the algebra.
11. Rename `GeneratorDagSummary`→`DagStatus` / `summarize_generator_dag`→`dag_status` (D15) —
    **NOT in this file** (those live in `generator_dag.py`→`plan_dag.py`, WS2/WS10). Listed in
    plan WS4 bullet but the symbols are not here; pure cross-ref.
12. Update `__all__` (291-302): drop `latest_task_summary`; `TaskOutcome`→`Outcome`;
    keep/rename the rest per decisions above.
13. **Module docstring** (1-13): rewrite — no longer "generator" summaries; it's the unified
    `Outcome` algebra; the `<task …>`/`summaries` references become `outcomes`.

**Risk:** This file is the type root for the whole algebra; every other WS4 + WS5 + WS7 edit
depends on its final field names. Land the `Outcome`/`text` rename + `from_record` legacy
fallback FIRST, then let consumers repoint. The `attempt_failure_line` rewrite is entangled
with WS2's `AttemptFailReason` collapse — do not rewrite it until `TASK_FAILED` exists.

---

### 2. `backend/src/task_center/attempt/orchestrator.py` (submit path — CORE)
**Current shape (verified):**
- Import block (19-24): `from task_center._core.generator_summaries import (attempt_failure_line, child_outcomes_for_workflow, generator_outcomes, to_record)`.
- `_write_submission_status` (327-349): writes a single dict
  `summary={"outcome": outcome, "summary": summary, "payload": payload}` via
  `task_store.set_task_status(task_id, status=…, summary=…)`. Maps `outcome` string
  (`success`/`blocker`/else) → DONE/BLOCKED/FAILED.
- `_mark_generator` (292-305), `_mark_evaluator` (307-325) both call `_write_submission_status`
  passing `outcome=submission.outcome, summary=submission.summary, payload=submission.payload`.
- Handoff: `_build_handoff_rollup` (215-240) + the `set_task_status_if_current` summary dict
  at 196-209 with `payload.handoff_rollup` — **Cross-ref WS7** (closure/handoff lifecycle).
- `_persist_generator_tasks` (265-290) passes `summaries=[]` + `spawn_reason=…` +
  `task_center_attempt_id=…` to `upsert_task` (284-287). **Cross-ref WS6/MN3** (store signature).

**Target shape (WS4-owned part = the submit write):**
- `_write_submission_status` must write **`outcomes` (list[Outcome] projected) + `terminal_tool_result` (raw payload)** instead of appending to `summaries`. This requires the
  **store seam** `set_task_status` to take `outcomes`/`terminal_tool_result` instead of `summary`
  (see store change #6). Concretely: build one `Outcome` from the submission
  (`local_id_of(task_id)`, `present_status(status.value)`, `text=submission.text`) and persist
  `outcomes=[to_record(o)]`, `terminal_tool_result=submission.terminal_tool_result`.
- `submission.summary`→`submission.text`; `submission.outcome`→`submission.status`;
  `submission.payload`→`submission.terminal_tool_result` (WS2 owns the
  `GeneratorSubmission`/`ReducerSubmission` DTO field renames — cross-ref).
- `_mark_evaluator`→`_mark_reducer` (WS1/WS2 own the rename; the *write* is shared via
  `_write_submission_status`).
- Import: `generator_summaries`→`outcomes`; `to_record` stays; `child_outcomes_for_workflow`
  + `_build_handoff_rollup` + `attempt_failure_line` usage in handoff is **WS7**.

**Risk:** The submit-write reshape is the single most-tested path (every pipeline scenario).
It is co-owned: WS4 owns "write `outcomes`+`terminal_tool_result`"; WS2 owns the submission
DTO field names; WS1 owns reducer rename. Sequence: WS2 DTO → WS4 write reshape.

---

### 3. `backend/src/task_center/iteration/attempt_coordinator.py` (`_achieved_record_for`→`_iteration_outcomes_for` — CORE)
**Current shape (verified):**
- Import (16): `from task_center._core.generator_summaries import generator_outcomes, to_record`.
- `_achieved_record_for(attempt)` (215-223): `outcomes = generator_outcomes(attempt, task_store=self._task_store); return json.dumps([to_record(o) for o in outcomes])`.
- Called from `_close_iteration_passed` (194-213) →
  `iteration_store.close_succeeded(…, plan_spec=…, task_summary=self._achieved_record_for(attempt), …)`.
- `_close_iteration_failed` (252-258): currently `set_status(FAILED)` with **no outcomes write** — must become failure-aware (write the last failed attempt's failed-task outcomes + `fail_reason`).
- Emits `IterationClosureReport` with `TerminalSuccess`/`SuccessDeferred`/`AttemptPlanFailed`
  (270-298) — **Cross-ref WS7** (closure DTO removal).

**Target shape (WS4-owned):**
- `_achieved_record_for`→`_iteration_outcomes_for(attempt)`: project the passing attempt's
  **reducer** outcomes, not generator: iterate `attempt.reducer_task_ids`
  (a new helper `reducer_outcomes(attempt, task_store=…)` in `outcomes.py`, mirroring
  `generator_outcomes` but over `reducer_task_ids` — **add it**), `json.dumps([to_record(o) …])`.
  Cross-ref WS1/WS2 for `attempt.reducer_task_ids`.
- Pass result to `close_succeeded(..., outcomes=…)` (the column rename `task_summary`→`outcomes`,
  store change #5).
- **Failure-aware (`_close_iteration_failed`):** §1 "a failed iteration carries its last failed
  attempt's failed-task outcomes + `fail_reason`". So on failed close, write
  `iteration.outcomes` = the failed-task outcomes of the last attempt (the
  `_failed_task_lines`/failed-task `Outcome`s from `outcomes.py`) + the attempt's `fail_reason`.
  This is NEW behavior — today failed close writes nothing. **DECISION (open #4):** the exact
  serialized shape of the failure-aware `iteration.outcomes` (a list of failed-task `Outcome`
  records, with `fail_reason` carried where? as a top-level field on each, or as a synthetic
  `Outcome.failure`?). Plan says "failed-task outcomes + `fail_reason`". Recommend: list of
  failed-task `Outcome` records (`status="failure"`, `failure=<fail_reason line>`), matching the
  retry/feedback projection WS5 reads. Flag — this contract is shared with WS5 retry.
- Imports: `generator_summaries`→`outcomes`; add `reducer_outcomes`.
- `IterationClosureReport` emission is **WS7**; the coordinator's outcome-write is WS4. These two
  edits collide in `_close_iteration_passed`/`_close_iteration_failed` — coordinate (WS7 after WS4
  per §12).

**Risk:** Reducer-outcome projection depends on `reducer_task_ids` (WS1/WS2). The failure-aware
write contract is shared with WS5 (retry feedback reads it). Pin the serialized shape once.

---

### 4. `backend/src/db/models/iteration.py` (`task_summary`→`outcomes` column — CORE)
**Current shape (verified):**
- Line 30: `goal: Mapped[str]` → **Cross-ref WS6** (`goal`→`iteration_goal`, vocab §4; not WS4).
- Line 50-52: `plan_spec: Mapped[str|None]` → **Cross-ref WS2/§3** (plan_spec removed).
- Line 53: `task_summary: Mapped[str|None] = mapped_column(Text, nullable=True)` → **WS4: rename to `outcomes`**.

**Target shape (WS4-owned):**
- `task_summary`→`outcomes: Mapped[str|None] = mapped_column(Text, nullable=True)` (stays Text —
  it stores a JSON string of the outcomes list, matching today's `task_summary` JSON-in-Text).
  **DECISION (open #5):** §2 says `Iteration.outcomes (json list[Outcome])`. Today it's a JSON
  *string* in a `Text` column. Keep `Text` (JSON-encoded string, matches `json.dumps` in the
  coordinator) OR switch to a `JSON` column. Recommend **keep `Text`** to minimize the migration
  + match the `parse_achieved_record(json.loads(...))` round-trip. Flag.
- Update the docstring (47-49). The `plan_spec` removal is WS2.

**Risk:** Low (mechanical column rename), but the column-rename must be paired with the
`_RENAMED_COLUMNS` migration entry (change #7) or existing dev DBs lose the data.

---

### 5. `backend/src/task_center/iteration/state.py` — `Iteration` DTO field `task_summary`→`outcomes` (CORE)
**Current shape (verified):** `Iteration` frozen dataclass (state.py:23-61); line **42**
`task_summary: str | None = None`; line **41** `plan_spec: str | None = None`.
(Also holds `TerminalSuccess`/`SuccessDeferred`/`AttemptPlanFailed`/`ClosureOutcome`/
`IterationClosureReport` (64-88) — **Cross-ref WS7** removes those.)

**Target shape (WS4-owned — this is the hole that makes #4/#10 compile):**
- `task_summary: str | None = None`→`outcomes: str | None = None` (JSON-string of the outcomes
  list; the field rename is WS4's *semantic* change even though WS6 *relocates* the dataclass to
  `_core/state.py`). If WS6 lands first, make this rename at `_core/state.py`; if WS4 lands first,
  at `iteration/state.py:42`. Either way it is a WS4 core edit — without it `iteration_store._to_dto`
  (#10, `outcomes=record.outcomes`) and `close_succeeded` reference a nonexistent field.
- `plan_spec` removal = WS2. `goal`→`iteration_goal` ripple = WS6/§4.

**Risk:** This DTO field is the link between the model column (#4) and the store DTO (#10); the
model→store→DTO chain has a hole exactly here if it is left to WS6 prose.

### (read-only) `backend/src/db/models/workflow.py` — NOT a WS4 edit; see drift
WS4 hand-edits nothing in `WorkflowRecord`. `workflow.outcomes` is **derived, not stored** (§1) —
the last iteration's `outcomes` — so there is **no `outcomes` column** on the workflow model. The
column changes (`drop final_outcome/origin_kind/requested_by_task_id`, `add parent_task_id`) are
**WS7/WS8**. WS4's only workflow-outcomes artifact is the `workflow_outcomes(workflow,
iteration_store)` helper in `outcomes.py` (open #10) + its use in `runner.py` (#11). This file was
in scope to *read*, not edit. Listed here so the implementer does not collide with WS7.

---

### 6. `backend/src/db/models/task_center.py` (`Task` reshape — CORE, D5)
**Current shape (verified):** `TaskCenterTaskRecord` (73-113):
`id`(76) · `task_center_run_id`(77) · `role`(82) · `agent_name`(83) · `context_message`(84) ·
`status`(85) · `summaries: Mapped[list[dict]]`(86) · `needs`(87) ·
`task_center_attempt_id`(88) · `context_packet_id`(91) · `fix_target_id`(94) ·
`spawn_reason`(95) · timestamps · `run`/`agent_run` relationships.

**Target shape (§2 Task):**
- `id`→`task_id` (FLAG-2; primary_key, `String(96)`).
- `summaries`→`outcomes: Mapped[list[dict]] = mapped_column(JSON, default=list)` (JSON list of
  `Outcome` records) **+ add** `terminal_tool_result: Mapped[dict|None] = mapped_column(JSON, nullable=True)`.
- **Add** `child_workflow_id: Mapped[str|None] = mapped_column(String(36), nullable=True)`
  (forward link, §1/D8 — **Cross-ref WS7/WS8** for the bidirectional link, but the *column*
  is needed here; coordinate who adds it).
- **Drop** `task_center_attempt_id`, `context_packet_id`, `fix_target_id`, `spawn_reason`
  (D5; task_id prefix encodes the attempt).
- `agent_run_id` — §2 lists `agent_run_id: str|None`; today it's the `agent_run` relationship
  (back_populated). Confirm whether D5 wants an explicit column or keeps the relationship —
  **DECISION (open #6):** keep the existing `agent_run` relationship (the `agent_run_id` in §2
  is the logical field exposed on the `Task` DTO, not necessarily a new column). Recommend no
  schema change for `agent_run_id`; flag.

**Risk:** `id`→`task_id` rename of a primary key is a real migration (and ripples through the
store serializer + every `record.id`/`"id"` reader). The `summaries`→`outcomes` JSON column +
new `terminal_tool_result`/`child_workflow_id` columns need `_RENAMED_COLUMNS`/`_DROPPED_COLUMNS`
entries (change #7). High blast radius via the store serializer (change #6).

---

### 7. `backend/src/db/engine.py` (migration maps — CORE, MN3-adjacent)
**Current shape (verified):**
- `_DROPPED_COLUMNS` (40-69): `task_center_tasks` already drops a legacy `summary` (61) — distinct
  from the live `summaries`. No `iterations`/`attempts`/`workflows` drop entries today.
- `_RENAMED_COLUMNS` (71-78): `iterations:{task_specification→plan_spec}`,
  `attempts:{task_specification→plan_spec}`.
- Note from MEMORY: startup DDL auto-migrates table/column *renames* and *drops* but NOT enum
  string-value changes; real rows live only in disposable runner DBs. So most of this is for dev
  DB cleanliness, not data preservation.

**Target shape (WS4-owned entries):**
- `_RENAMED_COLUMNS["iterations"]` += `"task_summary": "outcomes"`.
- `_RENAMED_COLUMNS["task_center_tasks"]` = `{"id": "task_id", "summaries": "outcomes"}`
  (the `id` PK rename — confirm the migration helper handles PK rename; if not, this is a
  `_rebuild_sqlite_table` path).
- `_DROPPED_COLUMNS["task_center_tasks"]` += `{"task_center_attempt_id", "context_packet_id", "fix_target_id", "spawn_reason"}`.
- (WS7 adds `_DROPPED_COLUMNS["workflows"]` += `{final_outcome, origin_kind, requested_by_task_id}`
  and `_DROPPED_COLUMNS["attempts"]` += `{evaluation_criteria, evaluator_task_id}` — not WS4.)
- New columns (`terminal_tool_result`, `child_workflow_id`, `workflows.parent_task_id`,
  `attempts.reducer_task_ids`) are added by `create_all` automatically on a fresh DB; the SQLite
  rebuild path covers dev DBs. No explicit ADD-COLUMN code needed per MEMORY note.

**Risk:** The `id`→`task_id` PK rename is the dangerous one. Verify `_RENAMED_COLUMNS` +
`_rebuild_sqlite_table` (107+) actually handle a primary-key rename; if not, the rename of the
`Task.id` column may need the rebuild path or a manual ALTER. **DECISION (open #7):** confirm PK
rename support before relying on `_RENAMED_COLUMNS`. Flag.

---

### 8. `backend/src/db/stores/task_center_store.py` (serializer + `upsert_task`/`set_task_status` — CORE, MN3)
**Current shape (verified):**
- `_serialize_task` (44-60): emits `"id"`, `"summaries": record.summaries or []`,
  `"task_center_attempt_id"`, `"context_packet_id"`, `"fix_target_id"`, `"spawn_reason"`.
- `upsert_task` (126-175): params include `summaries`, `task_center_attempt_id`,
  `context_packet_id`, `fix_target_id`, `spawn_reason`; sets them on the record (146-174).
- `set_task_status` (220-237): param `summary: SerializedRow|None`; appends to
  `record.summaries`.
- `set_task_status_if_current` (255-280): same `summary`-append pattern (key idempotency primitive
  for handoff — **Cross-ref WS7**).
- `set_task_context_packet_id` (239-253): **drop entirely** (context_packet_id gone, D5).
- `list_tasks_for_attempt` (191-203) + `list_generator_tasks_for_attempt` (205-218): filter on
  `task_center_attempt_id` — **Cross-ref WS6** (D5 removes the column; these queries lose their
  filter column; they must derive the attempt from `task_id` prefix or be removed). The mock
  `_graph_summary` calls `list_tasks_for_attempt` (runner.py:95) — needs a replacement.

**Target shape (WS4 + MN3):**
- `_serialize_task`: `"id"`→`"task_id"`; `"summaries"`→`"outcomes": record.outcomes or []`;
  add `"terminal_tool_result": record.terminal_tool_result`; add `"child_workflow_id"`; drop
  `task_center_attempt_id`/`context_packet_id`/`fix_target_id`/`spawn_reason`.
- `upsert_task`: signature per MN3 — drop `summaries`, `task_center_attempt_id`,
  `context_packet_id`, `fix_target_id`, `spawn_reason`; add `outcomes: list[SerializedRow]`,
  `terminal_tool_result: dict|None=None`, `child_workflow_id: str|None=None`. Body matches.
  **Plan WS6 explicitly says** "update `upsert_task` (`task_center_store.py:126`) and `set_status`
  … **before** the run controller and the submit path call them" — so this store change must land
  early.
- `set_task_status`/`set_task_status_if_current`: param `summary`→a `(outcomes, terminal_tool_result)`
  write. **DECISION (open #8):** today these *append* one summary dict; under outcomes the submit
  writes the task's `outcomes` list (typically a singleton) + `terminal_tool_result`. Change the
  signature to `outcomes: list[SerializedRow]|None`, `terminal_tool_result: dict|None`, replacing
  (not appending) — a terminal task has exactly one terminal result. The handoff CAS
  (`set_task_status_if_current`) similarly writes the child-workflow-derived `outcomes` (WS7).
  Recommend replace-semantics; flag the append→replace behavior change.
- Drop `set_task_context_packet_id`.
- `list_tasks_for_attempt`/`list_generator_tasks_for_attempt`: **Cross-ref WS6** — the
  `task_center_attempt_id` filter column is gone. Either re-derive via `task_id LIKE '<attempt>:%'`
  prefix, or have callers (mock `_graph_summary`, attempt close) list by run and filter on the
  task-id prefix. Pin with WS6.

**Risk:** Highest-fanout file in the cluster: the serializer keys (`"id"`, `"summaries"`) are
read by `task_outcome_from_row`, `_graph_summary`, audit recorder, and every test that asserts on
graph_summary. The append→replace semantics change on `set_task_status_if_current` is the handoff
idempotency primitive — coordinate carefully with WS7.

---

### 9. `backend/src/task_center/_core/persistence.py` (Protocols — CORE, MN3)
**Current shape (verified):**
- Imports (23-34): `from task_center.attempt.state import (Attempt, AttemptFailReason, AttemptStage, AttemptStatus)`; `from task_center.iteration.state import (Iteration, IterationCreationReason, IterationStatus)`; `from task_center.workflow.state import Workflow, WorkflowOrigin, WorkflowStatus` — **Cross-ref WS6** (all repoint to `_core.state`).
- `WorkflowStoreProtocol.set_status` (59-66): kwarg `final_outcome` — **WS7 drops it**.
- `WorkflowStoreProtocol.insert` (46-53): `origin`/`requested_by_task_id` — WS7 → `parent_task_id`.
- `IterationStoreProtocol.set_status` (90-96): no outcomes; `close_succeeded` (102-109): params
  `plan_spec`, `task_summary` — **WS4: `task_summary`→`outcomes`**; `plan_spec` removed (WS2).
- `AttemptStoreProtocol.set_evaluator_task_id` (129), `set_plan_contract` (131-138) with
  `evaluation_criteria` — **Cross-ref WS2** (→ `set_reducer_task_ids`, drop `evaluation_criteria`).
- `TaskStoreProtocol.upsert_task` (172-187): mirrors the store sig — **MN3 update** (same as
  change #8); `set_task_status`/`set_task_status_if_current` (193-202) param `summary`→
  outcomes/terminal_tool_result; `set_task_context_packet_id` (204) — drop.

**Target shape (WS4-owned):**
- `IterationStoreProtocol.close_succeeded`: `task_summary: str`→`outcomes: str` (or drop
  `plan_spec` per WS2; keep the `outcomes` param). Add an `outcomes`-write path for failed close
  if `set_status` needs an `outcomes` kwarg (failure-aware iteration close, change #3) —
  **DECISION (open #9):** does failure-aware close reuse `set_status` (add `outcomes` kwarg) or a
  new `close_failed(..., outcomes=…)`? Recommend extend `set_status` with an optional
  `outcomes: str|None=None` since it already takes `status`+`closed_at`. Flag — shared with WS7
  iteration-close.
- `TaskStoreProtocol.upsert_task`/`set_task_status*`: mirror change #8 exactly. Drop
  `set_task_context_packet_id`.
- `WorkflowStoreProtocol.set_status` drop `final_outcome` — **WS7**.

**Risk:** Protocol must stay byte-compatible with the concrete store (change #8) or the type
checker fails. These two files (8 and 9) must change together.

---

### 10. `backend/src/db/stores/iteration_store.py` (`close_succeeded`/`_to_dto` — CORE)
**Current shape (verified):**
- `_to_dto` (154-170): maps `plan_spec=record.plan_spec`, `task_summary=record.task_summary`.
- `close_succeeded` (126-152): params `plan_spec`, `task_summary`; sets `record.plan_spec`,
  `record.task_summary` atomically.
- `set_status` (78-94): no outcomes.
- Import (10-14): `from task_center.iteration.state import (...)` — **Cross-ref WS6**.

**Target shape (WS4-owned):**
- `close_succeeded`: `task_summary`→`outcomes`; sets `record.outcomes`. (`plan_spec` dropped by WS2.)
- `_to_dto`: `task_summary=record.task_summary`→`outcomes=record.outcomes`. (`plan_spec` → WS2.)
- `set_status`: add optional `outcomes: str|None=None` write for the failure-aware close
  (change #3 / open #9), OR a `close_failed`. Pin with change #9.
- `insert`: `goal`→`iteration_goal` (WS6/§4 vocab); `IterationStatus`/`IterationCreationReason`
  import repoint (WS6).

**Risk:** Must match the column rename (#4) and the DTO field (#3/WS6) exactly.

---

### 11. `backend/src/task_center_runner/core/runner.py` (run report `_graph_summary` — CORE)
**Current shape (verified):** `_graph_summary` (85-137). The `workflows.append({...})` dict
(127-136) currently emits `"status"`(129), `"origin_kind": workflow.origin_kind.value`(131),
`"requested_by_task_id"`(132), `"final_outcome": workflow.final_outcome`(133). Attempt dict
(96-114) emits `"task_ids": list(attempt.generator_task_ids)`(111) + `"tasks": task_rows`(112)
where `task_rows = bundle.task_store.list_tasks_for_attempt(attempt.id)`(95).

**Target shape (WS4-owned):**
- Replace `"origin_kind"`/`"requested_by_task_id"`/`"final_outcome"` with `"parent_task_id":
  workflow.parent_task_id` (WS7/WS8) + **`"outcomes": <derived workflow.outcomes>`** (the
  last-iteration outcomes; §1 "run report surfaces `workflow.status` + derived
  `workflow.outcomes`"). The derivation reads the last iteration's `outcomes`.
  **DECISION (open #10):** compute `workflow.outcomes` inline here (last iteration's `outcomes`
  field) or via a shared `outcomes.py` helper `workflow_outcomes(workflow, iteration_store)`?
  Recommend a shared helper in `outcomes.py` so the run-controller root path (WS8) and this report
  use one derivation. Flag.
- `list_tasks_for_attempt`(95) → Cross-ref WS6 (the attempt-id filter column is gone; re-derive by
  prefix). `attempt.generator_task_ids`(111) stays; consider also surfacing
  `reducer_task_ids` for scenario asserts (WS2/WS9 — not strictly WS4).
- Attempt `task_rows` now carry `"outcomes"`/`"terminal_tool_result"` (serializer #8) — scenario
  tests reading `tasks[i]["summaries"]` must repoint (propagation, WS9).

**Risk:** This is the assertion surface for ~all mock scenarios (`graph_summary["workflows"][…]`).
Field renames here break many scenario tests (WS9 propagation). Pin the exact key names with WS9.

---

### 12. `backend/tests/unit_test/test_task_center/test_lifecycle/test_iteration_attempt_coordinator.py` (CORE — semantic rewrite, NOT mechanical)
**Current shape (verified):** `test_close_iteration_passed_writes_structured_achieved_record`
(145+) seeds **generator** task fixtures `summaries=[{"summary": …}]` (162-163), then asserts
`seg.task_summary` (172-173) parses to `[{"local_id":"gen_a","status":"success","summary":…}, …]`
(175-176) over the *generator* local ids.

**Target shape:** WS4 flips the projection from generator→**reducer** outcomes (#3). The fixture
must define **reducer** tasks (reducer ids, on `attempt.reducer_task_ids`), assert `seg.outcomes`
(not `task_summary`), and use the `text` key (not `summary`). This is a semantic rewrite gated on
the #3 contract + WS1/WS2 `reducer_task_ids` — a string-match-only pass would produce a test that
asserts the wrong (generator) projection. **Classified core.**

---

**Representation asymmetry (pin once):** `Task.outcomes` is a **JSON column** (#6, list of Outcome
records); `Iteration.outcomes` is **Text holding a JSON string** (#4/#5/open #5, `json.dumps`'d by
the coordinator and `json.loads`'d by `parse_outcomes_record`). Do not assume a uniform
representation across the two.

**Deliberate deviation note:** open #1's private `_latest_text(rows)` helper is a *deliberate*
deviation from the plan's literal "readers project `Outcome`s directly" (§7 WS4) — kept only to
avoid duplicating the `summaries[-1]` dict-walk in 3 callers. The plan does not mandate the helper;
the implementer may instead inline `Outcome`-projection at each reader.

---

## PROPAGATION FILES (mechanical vocab/string-match only)

These need the `summary`→`text` / `TaskOutcome`→`Outcome` / `summaries`→`outcomes` /
`task_summary`→`outcomes` / `generator_summaries`→`outcomes` import rename, but no new logic
*from WS4* (their logic edits, where any, belong to WS5/WS7/context cluster — noted).

- `backend/src/task_center/context_engine/recipes/_task_xml.py` — imports
  `from …generator_summaries import (EMPTY_SUMMARY_PLACEHOLDERS, TaskOutcome, …)` (18-20);
  reads `outcome.summary` (73, 74, 89, 94). Rename import + `.summary`→`.text`. **Logic =
  WS5/context cluster** (the `<task>`/`<evaluator_summary>` blocks); WS4 only carries the field
  rename so it compiles.
- `backend/src/task_center/context_engine/recipes/iterations.py` — imports
  `parse_achieved_record` (29); reads `prior.task_summary` (133, 136, 143). Repoint to the renamed
  fn + `prior.outcomes`. **Iteration DTO field = WS6/this manifest #3.**
- `backend/src/task_center/context_engine/recipes/attempts.py` — imports `attempt_failure_line,
  generator_outcomes, latest_task_summary` (33-36); uses `latest_task_summary` (202),
  `_evaluator_summary_if_ran` (193-202). `latest_task_summary` is REMOVED — this file must inline
  or read `.text`. **This is WS5 + R1a fold** (attempts.py→planner.py); flagged so the removal
  doesn't strand it.
- `backend/src/task_center/context_engine/recipes/generator.py` — imports `task_outcome_from_row`
  (33). Import rename only.
- `backend/tests/unit_test/test_task_center/test_context_engine/test_role_context_matches_diagram.py`
  — constructs `TaskOutcome(local_id=…, status=…, summary=…)` (136-137, 369-428); imports
  `from …generator_summaries import TaskOutcome, to_record` (18); uses `task_summary=` (91).
  `TaskOutcome`→`Outcome`, `summary=`→`text=`, import path, `task_summary`→`outcomes`.
- `backend/tests/unit_test/test_task_center/test_context_engine/test_recipes_planner_closes_or_defers.py`
  — constructs `TaskOutcome` (2 occurrences). Same renames.
- `backend/tests/unit_test/test_task_center/test_context_engine/test_attempts.py` — imports/asserts
  on generator_summaries projections; `summary`→`text`, import path.
- `backend/tests/unit_test/test_task_center/test_context_engine/test_recipes_other.py` — same.
- `backend/tests/unit_test/test_task_center/test_persistence/test_close_succeeded.py` — asserts
  `closed.task_summary` (37, 42). Rename to `outcomes`.
- `backend/tests/unit_test/test_task_center/test_context_engine/test_iteration_no_invariant.py`,
  `test_recipes_planner_closes_or_defers.py` — `task_summary`/projection asserts; rename.
- `backend/src/task_center_runner/audit/recorder.py` — emits `"task_summary": record.task_summary`
  (130), `"summaries": list(record.summaries or [])` (165), `"final_outcome"`/`"origin_kind"`/
  `"requested_by_task_id"` (106,107,111). The `task_summary`→`outcomes` + `summaries`→`outcomes`
  key renames are WS4 propagation; `final_outcome`/`origin_kind`/`requested_by_task_id` are WS7.

---

## OPEN DECISIONS (plan leaves unpinned)

1. **`latest_task_summary` removal mechanics** — readers "project `Outcome`s directly" (§7) vs a
   private `_latest_text(rows)` helper. Recommend a private helper in `outcomes.py` (3 callers).
2. **`parse_achieved_record` fn name** — keep vs rename to `parse_outcomes_record`. Recommend
   rename for vocab coherence.
3. **`attempt_failure_line` generalization location** — it becomes the shared "failed-task lines"
   helper for both iteration failure-aware close (#3) and WS5 retry. Pin it lives in `outcomes.py`,
   over the union of `generator_task_ids`+`reducer_task_ids`.
4. **Failure-aware `iteration.outcomes` serialized shape** — list of failed-task `Outcome` records
   (`status="failure"`, `failure=<fail_reason line>`) vs a separate `fail_reason` field. Recommend
   the former (shared contract with WS5 retry feed).
5. **`Iteration.outcomes` column type** — keep `Text` (JSON string, matches `json.dumps`) vs `JSON`
   column. Recommend `Text`.
6. **`Task.agent_run_id`** — new column vs keep the existing `agent_run` relationship. Recommend no
   schema change (the §2 `agent_run_id` is the logical DTO field).
7. **`Task.id`→`task_id` PK rename migration** — confirm `_RENAMED_COLUMNS` + `_rebuild_sqlite_table`
   handle a primary-key rename; if not, rebuild-path or manual ALTER.
8. **`set_task_status*` append→replace semantics** — switch from appending a summary dict to writing
   the task's `outcomes` list + `terminal_tool_result` (replace). Confirm no reader relies on the
   multi-entry `summaries` history.
9. **Failure-aware iteration close API** — extend `IterationStore.set_status`/protocol with optional
   `outcomes` vs a new `close_failed`. Recommend extend `set_status`.
10. **`workflow.outcomes` derivation** — inline in `_graph_summary`/run-controller vs a shared
    `workflow_outcomes(workflow, iteration_store)` helper in `outcomes.py`. Recommend the shared
    helper (used by WS8 root path too).
11. **`new Outcome` reducer projection helper** — add `reducer_outcomes(attempt, *, task_store)`
    mirroring `generator_outcomes` over `attempt.reducer_task_ids`. Pin this is added in `outcomes.py`
    (depends on WS1/WS2 `reducer_task_ids`).

---

## EXECUTION ORDERING (within WS4, given §12)
1. (Precond) WS1 reducer role + WS2 `reducer_task_ids` on `Attempt`/store/protocol exist.
2. `_core/outcomes.py` type rename (`Outcome`/`text`/`from_record` legacy fallback) + add
   `reducer_outcomes` + fold `attempt_failure_line` (after WS2 `TASK_FAILED`).
3. Store + protocol (#8, #9) `upsert_task`/`set_task_status*`/`close_succeeded` signatures —
   **before** orchestrator submit + coordinator call them (plan WS6 explicit).
4. DB models (#4, #6) + migration maps (#7).
5. Orchestrator submit write (#2) + coordinator `_iteration_outcomes_for` (#3).
6. Run-report surface (#11) + iteration_store (#10).
7. Propagation (recipes import/field renames + tests) — last, once the type is stable.
8. WS7 reshapes the handoff/closure algebra (`child_outcomes_for_workflow`, `_build_handoff_rollup`,
   `final_outcome`) on top of the stable `Outcome` type.
