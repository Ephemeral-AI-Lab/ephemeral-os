# Role-Scoped Context Design — Planner / Generator / Evaluator / Handoff

Status: REVISED v5 (full per-role design after mapping all four current recipes)
Scope: TaskCenter context engine + iteration lifecycle + attempt orchestrator (handoff closure).

> **Headline:** the **generator** and **evaluator** contexts **already match** the target design —
> no change. All real work is in the **planner** (two distinct blocks) and the **handoff** result.

---

## 0. Current state per role (code-grounded)

### Generator (recipe `generator`) — `recipes/generator.py` — ALREADY MATCHES TARGET
Scope: `goal_id, iteration_id, attempt_id, task_id`. Ordered blocks:
1. `<plan_spec>` (HIGH) — full attempt plan/DAG (`generator.py:59-69`). **Present.**
2. `<dependency id="...">` (MEDIUM) — one per upstream `needs` task, body = that dep's latest
   summary (`_dependency_blocks`, `generator.py:101-132`). **Present.**
3. `<assigned_task task_id="...">` (REQUIRED) — this generator's task contract (`generator.py:73-85`).
   **Present.**
→ Target (plan spec + dependency summaries + assigned task) = **exactly today**. No change.

### Evaluator (recipe `evaluator`) — `recipes/evaluator.py` → `current_attempt_flat_blocks` — ALREADY MATCHES
Scope: `goal_id, iteration_id, attempt_id`. Ordered blocks (`attempts.py:127-174`):
1. `<plan_spec>` (HIGH). **Present.**
2. `<task id="..." status="...">summary</task>` per generator in `attempt.generator_task_ids`
   (HIGH). **Present (all of them).**
3. `<evaluation_criteria>` (REQUIRED). **Present.**
→ Target (plan spec + all generator summaries + criteria) = **exactly today**. No change.
   *Note:* the evaluator only runs once **all** generators are `DONE` (stage advance requires
   `all_done`), so at eval time there are no un-started generators — "all summaries" is naturally
   all-done. No filtering question here.

### Planner (recipe `planner`) — `recipes/planner.py` → `iterations.py` + `attempts.py`
Scope: `goal_id, iteration_id, attempt_id`. Two temporally-distinct contributions:
- **Cross-iteration** (`_prior_iteration_blocks`, `iterations.py:106-158`): per prior closed
  iteration, `<accepted_plan>`=`prior.plan_spec` + `<summary>`=`prior.task_summary` (today the
  evaluator pass-summary + `Passed criteria:`). Immediate=HIGH, older=MEDIUM.
- **Within-iteration replanning** (`failed_attempt_blocks` → `_render_failed_attempt_body`,
  `attempts.py`): per **failed** attempt of the current iteration — `<plan_spec>`,
  `<deferred_goal_for_next_iteration>` (if any), `<status_summary>`, one `<task id status>` per
  generator (**all**, incl. un-started), `<evaluation_criteria>`, `<evaluator_summary>`,
  `<passed_criteria>`/`<failed_criteria>`.

### Handoff result — `attempt/orchestrator.py:160-206` (`apply_goal_closure_report`)
`submit_execution_handoff` starts a delegated goal and returns immediately. On child-goal closure,
the **parent generator task's** summary is set (today: one line `"Delegated goal X succeeded/failed."`).
The executor continuation reads it via its `<dependency>` block. `AttemptDeps` exposes
`goal_store/iteration_store/attempt_store/task_store` here, so all child state is reachable.

### Generator status enum (`_core/task_state.py:27-42`)
`PENDING, RUNNING, WAITING_GOAL, DONE, FAILED, BLOCKED` (+ synthetic "missing task row").
`TERMINAL_GENERATOR_STATUSES = {DONE, FAILED, BLOCKED}`. **`BLOCKED` means the generator ran and
reported a `blocker` outcome — NOT "never started".** Un-started/never-ran = `PENDING`,
unreachable-pending, `RUNNING`, `WAITING_GOAL`, missing. Today's failed-attempt + evaluator blocks
render **all** statuses with **no filtering**.

---

## 1. Target design (from user) + verdict

| Role | Target | Verdict |
|---|---|---|
| **Generator** | plan spec · dependency summaries · assigned task | ✅ already exactly this — **no change** |
| **Evaluator** | plan spec · all generator summaries · evaluation criteria | ✅ already exactly this — **no change** |
| **Planner** | goal · iteration goal · what's-been-done (prior iterations) · generator summaries (passed+failed, **not un-started**) · evaluator summary (if executed) · **not** prior plan spec / eval criteria | sound; **real changes** (§2) |
| **Handoff gen** | its summary = list of **all** child generator summaries (+ optional failure) | ✅ sound; reuse denorm (§3) |

**Overall:** coherent, and the fact that 2 of 3 roles already implement the target is strong evidence
the design is consistent with the system's existing intent. The planner changes are the substance.

### Why the planner changes are sound
- **Drop prior plan_spec + eval criteria (within-iteration replanning blocks):** the planner is
  *about to write its own* plan + criteria. Showing the failed attempt's plan/criteria anchors it to
  the decomposition that just failed. Keeping **what was tried** (generator summaries) + **why it
  failed** (evaluator summary) is the useful retry fuel; the plan/criteria are not.
- **Exclude un-started generators ("pretend they never exist"):** an un-started task carries zero
  signal about what happened. Precise rule = **keep only `TERMINAL_GENERATOR_STATUSES`
  {DONE, FAILED, BLOCKED}**; drop `PENDING`/unreachable-pending/`RUNNING`/`WAITING_GOAL`/missing.
  (`BLOCKED` is kept — it ran and reported a blocker, which is exactly the kind of "why stuck"
  signal replanning wants.)
- **Evaluator summary only if executed:** omit the block entirely when the attempt failed before the
  evaluator ran (no `(missing evaluator task row)` placeholder).
- **Cross-iteration "what's been done":** the passing attempt's generator summaries (all `DONE`),
  denormalized — no evaluator text, no plan_spec, no criteria.

### Decisions to confirm (the genuine ambiguities)
- **D1 — "drop evaluation criteria":** I read this as dropping the `<evaluation_criteria>` list **and**
  the `<passed_criteria>`/`<failed_criteria>` breakdown, keeping only the free-text
  `<evaluator_summary>`. Confirm (or keep passed/failed?).
- **D2 — failed-attempt extras:** `<status_summary>` becomes largely redundant once tasks are
  filtered + shown per-task → recommend drop; `<deferred_goal_for_next_iteration>` on a *failed*
  attempt is normally absent → leave out. Confirm.
- **D3 — asymmetry (intended?):** the planner sees the evaluator summary in the *within-iteration*
  failed-attempt blocks but **not** in the *cross-iteration* record. Justified: replanning needs
  "why it failed"; cross-iteration needs "what's done." OK?
- **D4 — handoff failure body:** aggregate succeeded child iterations' generator summaries **plus**
  the final failed attempt's terminal-generator summaries + `Handoff failed: <reason>`; or simpler
  (final attempt + reason only)? Recommend the former.

---

## 2. Planner changes

### 2.1 Cross-iteration record (`iterations.py` + denorm at close)
- At `attempt_coordinator._close_iteration_passed`: set `task_summary = generator_summary_lines(
  passing_attempt, task_store)` (the `- <local_id>: <summary>` list; passing attempt ⇒ all `DONE`).
  Keep `plan_spec` stored (audit) but stop surfacing it.
- `iterations.py:_prior_iteration_blocks`: **remove** the `<accepted_plan>` block; keep `<summary>`
  (content now the generator list). Retarget the chain-integrity guard to `task_summary` only.
- `_evaluator_pass_summary_for` / `_evaluator_passed_criteria` become unused here → remove if no
  other caller.

### 2.2 Within-iteration replanning blocks (`attempts.py:_render_failed_attempt_body`)
- **Filter generators to `TERMINAL_GENERATOR_STATUSES`** (exclude un-started) — applied in
  `_generator_outcomes` or at render.
- **Remove** `<plan_spec>` and `<evaluation_criteria>` (+ `<passed_criteria>`/`<failed_criteria>` per D1).
- **Keep `<evaluator_summary>` only if the evaluator executed** (drop the missing-row fallback path).
- Per D2: drop `<status_summary>`; leave out `<deferred_goal_for_next_iteration>`.

---

## 3. Handoff result (`apply_goal_closure_report`)
- Parent generator task summary = **all child generator summaries across all child iterations**.
  **Synergy:** after §2.1, each succeeded child iteration's `Iteration.task_summary` *is* the
  generator list — so aggregate by concatenating `iteration_store.list_for_goal(report.goal_id)`
  succeeded iterations' `task_summary` (cheap: 1 read/iteration) instead of walking every task.
- On failure (`report.outcome != "success"`): append the final failed attempt's terminal-generator
  summaries (walk `report.final_attempt_id`) + `Handoff failed: <fail_reason>` (per D4).

---

## 4. Shared helper + `latest_summary_text` removal
- New neutral `task_center/_core/generator_summaries.py`: `latest_task_summary(summaries)` (moved
  projection) + `generator_summary_lines(attempt, task_store)` (the `- <local_id>: <summary>` list,
  presence-defensive, DAG order, `local_id` via `_core/primitives.py`).
- Delete `recipes/summaries.py`; repoint its 3 callers (`generator.py:123`, `attempts.py:284,374`)
  to `_core.latest_task_summary`. (Consolidate, don't inline 4 copies.)

---

## 5. Context diagram (target)

```
PLANNER            scope: goal_id, iteration_id, attempt_id        recipe: planner
├─ <goal>                                            overall goal
├─ prior closed iterations  (N>1; immediate=HIGH, older=MEDIUM)
│   └─ <iteration status="prior" seq=k>
│        └─ <summary>            WHAT'S BEEN DONE = passing attempt's generator summaries
│             - <local_id>: <summary>      (no evaluator text · no plan_spec · no criteria)
├─ current iteration's FAILED attempts  (replanning fuel; only if retrying)
│   └─ <attempt status="prior" verdict="fail" seq=k>
│        ├─ <task id status>summary</task>   generators — TERMINAL only {done,failed,blocked}
│        │                                   (un-started PENDING/RUNNING/WAITING_GOAL excluded)
│        └─ <evaluator_summary>…             ONLY if the evaluator executed
│        ✗ no <plan_spec>   ✗ no <evaluation_criteria>
└─ <iteration_goal>                          current iteration goal (= deferred goal)

GENERATOR/EXECUTOR  scope: …, task_id     recipe: generator     [ALREADY MATCHES — no change]
├─ <plan_spec>                               full attempt plan / DAG
├─ <dependency id="…">                       one per upstream `needs` = its latest summary
│   …
└─ <assigned_task task_id="…">               this generator's task contract

EVALUATOR           scope: …, attempt_id    recipe: evaluator    [ALREADY MATCHES — no change]
├─ <plan_spec>                               full attempt plan / DAG
├─ <task id status>summary</task>            one per generator (all DONE at eval time)
│   …
└─ <evaluation_criteria>                     planner-defined acceptance criteria

HANDOFF result      written at apply_goal_closure_report → parent generator task summary
└─ parent generator's summary =
     - <child_local_id>: <summary>           ALL child generators, ALL succeeded child iterations
     …
     Handoff failed: <reason>                optional — only if the delegated goal failed
   (executor continuation reads this through its <dependency> block)
```

---

## 6. Tests / verification
- Generator & evaluator recipes: **regression only** (assert unchanged).
- Planner cross-iteration: `<summary>` = generator list; no `<accepted_plan>`; chain-guard retarget.
- Planner failed-attempt: un-started filtered out; no `<plan_spec>`/`<evaluation_criteria>`;
  `<evaluator_summary>` present iff evaluator ran. Tests in
  `test_recipes_planner_closes_or_defers.py`, `test_recipes_other.py`, `test_iteration_attempt_coordinator.py`.
- Handoff: success aggregates all succeeded child iterations' generator lists; failure appends
  terminal generators + reason. Tests under `test_task_center` for `apply_goal_closure_report`.
- `recipes/summaries.py` deleted; 3 callers repointed; identical output at those sites.
- `.venv/bin/pytest` green (never global pytest).
```
