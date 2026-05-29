# Role-Scoped Context Design — Planner / Generator / Evaluator / Handoff

Status: REVISED v6 (unify generator summaries on `<task>` everywhere; status vocab = success|failure|pending;
handoff renders nested `<task>`; structured achieved-record storage).
Scope: TaskCenter context engine + iteration lifecycle + attempt orchestrator (handoff closure).

---

## 0. Rendering conventions (apply everywhere)

> The XML examples below are written compactly; the renderer always emits **block form**
> (`<tag>\n{content}\n</tag>` — `renderer.py:_render_block`/`_render_group`), never inline. Empty
> `<task>` bodies use a placeholder (presence-defensive) rather than self-closing; the handoff's
> nested `<task>` is pre-rendered (`pre_rendered_xml="true"`) since the group path nests one level.
> The whole rendered body is wrapped in a `<context>…</context>` envelope by the composer
> (`agent_launch/composer.py:_wrap_context`), not the renderer — it's one of the launch rows
> (system + `<context>` + `<Task Guidance>` + `Load skill:`).


- **One element for a generator/task summary: `<task id="<local_id>" status="<status>">summary</task>`.**
  Used in the evaluator, the generator's dependencies, the planner's prior-iteration and
  failed-attempt blocks, and (nested) the handoff result. No `<summary>`/`<accepted_plan>` blob, no
  `<achieved>`, no flat `- local_id: summary` text.
- **Status vocabulary is `success | failure | pending`** (presentation), mapped from the internal
  enum: `DONE→success`, `FAILED|BLOCKED→failure`, `PENDING→pending` (`RUNNING`/`WAITING_GOAL` are
  transient and not shown in these terminal contexts).
- **Un-started generators are excluded** in the planner's failed-attempt blocks ("pretend they never
  existed") — i.e. only render statuses derived from `TERMINAL_GENERATOR_STATUSES {DONE, FAILED,
  BLOCKED}`.

Because we render per-task `<task id status>`, the denormalized achieved records (prior-iteration and
handoff roll-up) must be stored **structurally** — a list of `{local_id, status, summary}` — not a
flat string, so the recipe can emit `<task>` cleanly without parsing.

---

## 1. Per-role target structure

### Planner — `recipe: planner` · scope `goal_id, iteration_id, attempt_id`
Both the prior iterations and the current iteration are `<iteration>` groups (same
`current_iteration_group_id` / prior group_id mechanism). The current group holds the
`<iteration_goal>` **first**, then any failed attempts.
```xml
<goal>
…overall goal…
</goal>

<!-- prior CLOSED iterations (only when N>1; immediate prior=HIGH, older=MEDIUM) -->
<iteration iteration_no="1" position="prior">
<task id="storage" status="success">
…summary…
</task>
<task id="cli_add" status="success">
…summary…
</task>
</iteration>

<!-- CURRENT iteration: <iteration_goal> first, then failed attempts (only while retrying) -->
<!-- failed-attempt <task>s are TERMINAL only; un-started excluded -->
<iteration iteration_no="2" position="current">
<iteration_goal>
…current iteration goal (= deferred goal)…
</iteration_goal>
<attempt attempt_no="1">
<task id="cli_list" status="success">
…summary…
</task>
<task id="cli_done" status="failure">
…summary…
</task>
<evaluator_summary>
…submit_evaluation_* result; only if the evaluator ran…
</evaluator_summary>
<failure>
generator cli_done: …why this attempt failed…
</failure>
</attempt>
</iteration>
```
Already true in code: the current-iteration grouping and `<iteration_goal>`-first
ordering (insertion order: `goal_iteration_blocks` appends `<iteration_goal>` before
`failed_attempt_blocks` is extended; renderer preserves order within a contiguous same-group run).
Not present: prior `<accepted_plan>`/plan_spec, prior evaluator narrative, `<evaluation_criteria>`,
`<status_summary>`, `<deferred_goal_for_next_iteration>`, un-started generators.

### Generator / Executor — `recipe: generator` · scope `…, task_id`
```xml
<plan_spec>
…full attempt plan / DAG…
</plan_spec>

<!-- wrapper around upstream task summaries -->
<dependency>
<task id="storage" status="success">
…summary…
</task>
<task id="cli_add" status="success">
…summary…
</task>
</dependency>

<assigned_task task_id="cli_done">
…this generator's task contract…
</assigned_task>
```
Change vs today: dependencies move from flat `<dependency id="…">` siblings to a `<dependency>`
wrapper with `<task>` children; status vocab applies.

### Evaluator — `recipe: evaluator` · scope `…, attempt_id`
```xml
<plan_spec>
…full attempt plan / DAG…
</plan_spec>

<!-- one per generator; all success at eval time -->
<task id="storage" status="success">
…summary…
</task>
<task id="cli_add" status="success">
…summary…
</task>
<task id="cli_list" status="success">
…summary…
</task>
<task id="cli_done" status="success">
…summary…
</task>

<evaluation_criteria>
…planner-defined acceptance criteria…
</evaluation_criteria>
```
Change vs today: status vocab only (`done→success`). Structure otherwise unchanged.

### Handoff task (a generator that called `submit_execution_handoff`)
Its summary is the roll-up of its **child goal's** generators, rendered as **nested `<task>`**:
```xml
<!-- SUCCESS: all child generators across all succeeded child iterations -->
<task id="implement_auth" status="success">
<task id="schema" status="success">
…summary…
</task>
<task id="login_api" status="success">
…summary…
</task>
<task id="session_mw" status="success">
…summary…
</task>
</task>

<!-- FAILURE: what's been done + the failing step of the last iteration -->
<task id="implement_auth" status="failure">
<task id="schema" status="success">
…summary…   (what's been done)
</task>
<task id="login_api" status="failure">
…summary…   (last iteration's failing step)
</task>
<failure>
generator login_api: …
</failure>
</task>
```
This nested `<task>` then appears wherever that generator appears (a `<dependency>` child downstream,
a `<task>` in the evaluator, or the planner's prior-iteration / failed-attempt blocks). Nesting is
one level per task — a child that itself did a handoff already encapsulates its own subtree.

---

## 2. Changes by surface

### 2.1 Planner cross-iteration record
- Denorm at `attempt_coordinator._close_iteration_passed`: store the passing attempt's generators
  **structurally** (`[{local_id, status:"success", summary}]`) — replaces the evaluator pass-summary
  in `Iteration.task_summary` (store JSON in the existing text column, or a new nullable column;
  either avoids backfill on the long-lived no-Alembic DB and degrades gracefully for legacy rows).
  Stop surfacing `plan_spec`.
- `iterations.py:_prior_iteration_blocks`: drop `<accepted_plan>`; render the structured record as
  `<task id status>` children inside `<iteration position="prior">` (no `<summary>` wrapper). Retarget
  the chain-integrity guard to the achieved-record field.
- `_evaluator_pass_summary_for` / `_evaluator_passed_criteria` become unused here → remove if no
  other caller.

### 2.2 Planner within-iteration failed-attempt blocks (`attempts.py:failed_attempt_blocks` / `_render_failed_attempt_body`)
- Already grouped under `<iteration position="current">` (shares `current_iteration_group_id`) after the
  `<iteration_goal>` — no grouping/ordering change needed.
- **Rename the iteration group attribute `status` → `position`** (values `prior`/`current`) — it
  collides with the domain `IterationStatus` and with `<task status>`. Change in
  `iterations.py:current_iteration_group_attrs` and the prior-iteration `group_attrs` (e.g.
  `f'iteration_no="{n}" position="current"'`); update recipe tests. `<task>`/`<attempt>` keep `status`.
- `<attempt attempt_no="k">` — drop the `status` / `verdict="fail"` from the block's `attrs`
  (keep `attempt_no`); the attempt is a prior attempt *of the current iteration*, so those attrs were
  misleading anyway.
- `<task id status>` per generator, **TERMINAL only** (exclude un-started), status vocab.
- `<evaluator_summary>` = `submit_evaluation_*` result (with passed/failed detail) — only if the
  evaluator executed (drop the missing-row fallback).
- `<failure>` (see §2.4). Drop `<plan_spec>`, `<evaluation_criteria>`, `<status_summary>`,
  `<deferred_goal_for_next_iteration>`.

### 2.3 Generator + Evaluator
- Evaluator: status vocab only.
- Generator: wrap dependencies in `<dependency>` with `<task>` children; status vocab.

### 2.4 Failure component — `attempt_failure_line(attempt, task_store)` (shared)
From `attempt.fail_reason` + the failing task's latest summary:
- `PLANNER_FAILED` → `planner: <summary>`; `GENERATOR_FAILED` → `generator <local_id>: <summary>`
  (per failed/blocked generator); `EVALUATOR_FAILED` → `evaluator: <summary>`;
  `STARTUP_FAILED` → `agent_launch_failed` (terse `"<role> agent launch failed."` when present).
- Tag `(terminated)` when the task's `payload.fail_reason == "run_exhausted"`.
- Presence-defensive (`(no detail recorded)`).

### 2.5 Handoff result (`attempt/orchestrator.py:apply_goal_closure_report`)
Store the result **structurally** on the parent generator task so it renders nested `<task>`:
- **Success:** child generators across all SUCCEEDED child iterations
  (`iteration_store.list_for_goal` → succeeded iterations' structured achieved records, reused from §2.1).
- **Failure:** what's been done (succeeded child iterations' generators) **+** the last failed
  attempt's `attempt_failure_line` (§2.4), as a `<failure>` child.
- `GoalClosureReport` carries only `outcome`/`final_iteration_id`/`final_attempt_id`; read the "why"
  from `attempt_store.get(report.final_attempt_id).fail_reason` + failing task summary (reachable via
  `AttemptDeps`).

---

## 3. Failure representation — findings (why the odd cases are safe)
The launcher normalizes abnormal terminations into clean failure submissions with human-readable text:
- Clean `submit_*_failure` → agent's reason text (+ payload, e.g. `failed_criteria`).
- Terminated (1.5× tool-call hard ceiling `TERMINAL_NOT_SUBMITTED`, crash, run-failed) →
  `attempt/launch.py` synthesizes a role submission with `payload.fail_reason="run_exhausted"` and a
  diagnostic summary (`"Agent run crashed: <exc>"` / `"Agent run ended without a terminal submission."`).
- `STARTUP_FAILED` (`_mark_startup_failed`/unstarted) → only `agent_launch_failed` (the one terse case).
So `attempt_failure_line` always has at least the stage + a message (or the terse launch-failed label).

---

## 4. Shared helpers (`_core`) + cleanup
- `task_center/_core/generator_summaries.py`:
  - `latest_task_summary(summaries)` — moved projection (placeholders `(no summary recorded)`/`(empty)`).
  - `generator_outcomes(attempt, task_store)` — `[{local_id, status(success|failure|pending), summary}]`
    over `attempt.generator_task_ids` (DAG order, presence-defensive, status mapped); the structured
    record reused by §2.1 denorm, §2.5 handoff, and rendered to `<task>` by recipes.
  - `attempt_failure_line(attempt, task_store)` — §2.4.
- Delete `recipes/summaries.py`; repoint its 3 callers to `_core.latest_task_summary`.
- A small status-mapping helper (internal enum → `success|failure|pending`) shared by all renderers.

---

## 5. Tests / verification
- Status vocab: `<task>` renders `success|failure|pending` everywhere (evaluator, generator deps,
  planner blocks, handoff).
- Generator: dependencies wrapped in `<dependency>` with `<task>` children.
- Planner prior-iteration: `<task>` children, no `<summary>`/`<accepted_plan>`; chain-guard retarget.
- Planner failed-attempt: `<attempt seq>` (no status/verdict attrs); un-started excluded;
  `<evaluator_summary>` only if evaluator ran; `<failure>` present; no plan_spec/criteria/status_summary/deferred.
- Handoff: success → nested `<task>` of all succeeded child generators; failure → what's-been-done +
  `<failure>`; robust across terminated/exhausted/agent_launch_failed.
- Structured achieved record round-trips; legacy (pre-migration) iterations render gracefully.
- `recipes/summaries.py` deleted; 3 callers repointed.
- `.venv/bin/pytest` green (never global pytest).
```
