# Implementation Design Notes — planner_prior_iteration_context_IMPL_PLAN

Frozen design for implementing `planner_prior_iteration_context_IMPL_PLAN.md`. Source of
truth for the rendered shape is **the IMPL_PLAN §0/§1 XML**, NOT any workflow agent's
reconstruction (one agent wrongly inserted `<plan_spec>` into the failed `<attempt>` and
wrongly kept `<accepted_plan>`/`<summary>` in prior iterations — both contradict §2.1/§2.2).

## Override decisions (would be lost on compaction)
- **Prior iterations drop `<accepted_plan>` and `<summary>`** → render `<task id status>` children only.
- **`plan_spec` is still WRITTEN** by `close_succeeded` (no signature change); the recipe just
  stops READING it. Chain-integrity guard retargets to `task_summary` (the achieved record).
- **Renderer left untouched** — all new structure is recipe-supplied via metadata + `pre_rendered_xml`.
  (`</position>` is an attribute, not a tag — ignore that agent suggestion.) Only touch renderer if a test forces it.
- **`task_summary` flip to JSON is safe**: only readers are the iterations recipe + coordinator
  (both changing) + `recorder.py:130` (opaque structured DB-row dump, not prose).

## Layering
- `_core/generator_summaries.py` = DATA only (no XML, no ContextEngineError import).
- XML render + sanitization stays in `context_engine` (new `recipes/_task_xml.py`).
- Handoff stores STRUCTURED `handoff_rollup` on the parent task (lifecycle never emits XML).

## `_core/generator_summaries.py`
- `TaskOutcome(local_id, status, summary, children=(), failure=None, raw_status=None)` + `is_terminal`
  (`raw_status in {done,failed,blocked}`).
- `latest_task_summary(summaries)` — moved verbatim from `recipes/summaries.py`.
- `present_status(raw)` — done→success, failed|blocked→failure, pending→pending, else raw (keeps "missing task row").
- `local_id_of(task_id)` — `task_id.split(":gen:",1)[1]` if `:gen:` present else full id.
- `generator_outcomes(attempt, task_store)` → list[TaskOutcome] over `attempt.generator_task_ids`,
  reading `summaries[-1].payload.handoff_rollup` → children/failure.
- `attempt_failure_line(attempt, task_store)` — §2.4: planner: / generator <lid>: / evaluator: /
  agent_launch_failed; `(terminated)` tag when failing task payload.fail_reason=="run_exhausted";
  `(no detail recorded)` fallback.
- `to_record(o)`/`from_record(d)` JSON dict ⇄ TaskOutcome (children recursive; raw_status dropped).
- `child_outcomes_for_goal(goal_id, iteration_store)` → flatten succeeded iterations' achieved records.

## `recipes/_task_xml.py`
- `render_task_children(outcome) -> str` (inner: nested `<task>`×children + optional `<failure>`).
- `render_task_element(outcome) -> str` (full `<task id status>body</task>`; body = children-string
  if children else summary; empty → placeholder "(no summary recorded)", NEVER self-closing).
- `sanitize_fragment(text)` raises ContextEngineError on structural closers (incl. `</failure>`).

## Rendering modes
- BLOCK mode (renderer wraps): evaluator flat `<task>`, deps `<dependency>` group children,
  prior-iteration `<iteration position=prior>` group children. text=summary; for handoff outcome
  text=render_task_children(...) + `pre_rendered_xml="true"`. Empty body = "" (existing evaluator tests).
- STRING mode (recipe assembles, `pre_rendered_xml="true"` on the block): failed-attempt `<attempt>` body,
  handoff parent `<task>` (when itself a block elsewhere uses block-mode body).

## Per-surface
- iterations.py: `current_iteration_group_attrs` status→position; prior group_attrs status→position;
  `_prior_iteration_blocks` parse `json.loads(prior.task_summary)` → one child block per entry
  (child_tag="task", attrs `id status`, pre_rendered when children). Guard: raise if task_summary is None.
  Legacy non-JSON → single `<task id="summary" status="success">{text}</task>` (graceful).
- attempts.py: failed_attempt_blocks attrs `attempt_no="k"` only (drop status/verdict). Body:
  TERMINAL-only `<task>` (raw status in {done,failed,blocked}) + `<evaluator_summary>` ONLY if evaluator
  ran (drop fallbacks) + `<failure>` (attempt_failure_line). Drop plan_spec/criteria/status_summary/deferred.
  current_attempt_flat_blocks (evaluator): present_status vocab; handoff→nested body+pre_rendered.
  Repoint `latest_summary_text`→`_core.latest_task_summary`. Add `</failure>` to sanitizer closers.
- generator.py: `_dependency_blocks` → `<dependency>` group (group_tag="dependency", child_tag="task"),
  attrs `id status`; handoff→pre_rendered nested body. Repoint latest_summary_text.
- attempt_coordinator.py: `_close_iteration_passed` task_summary = json.dumps([to_record(o) for o in
  generator_outcomes(attempt, task_store)]); keep plan_spec write. DELETE `_evaluator_pass_summary_for`
  + `_evaluator_passed_criteria`. (get_evaluator_pass_summary store method stays for its own tests.)
- orchestrator.py: apply_goal_closure_report builds handoff_rollup = {"children":[to_record(o)...],
  "failure": attempt_failure_line(...) or None} from succeeded child iterations
  (iteration_store.list_for_goal(report.goal_id)) + (failure) final_attempt. Stored in summary payload
  alongside existing goal_closure_report payload (idempotency/delivery tests still pass).
- DELETE recipes/summaries.py; repoint generator.py + attempts.py imports.
- tag_dictionary.py + context_outline.py SOURCE: iteration semantic attr status→position.

## Verification (the user's explicit ask)
- Durable render-and-compare test using PRODUCTION-shape ids (`attempt:gen:storage`), diffing against
  IMPL_PLAN §1 verbatim for planner / generator / evaluator.
- Handoff success+failure: unit render of a parent task carrying a `handoff_rollup` payload.
