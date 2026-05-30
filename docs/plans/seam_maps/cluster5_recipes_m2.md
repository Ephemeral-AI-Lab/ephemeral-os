# Cluster 5 — WS5 recipes + prompts + M2 — Edit Manifest

Scope: the three context recipes (planner/generator/reducer), their shared helpers,
the context-engine support surface (scope/tag_dictionary/renderer/outline/directives/
task_guidance), the `core.py`→`engine.py` module rename, and the M2/D3 plan_spec decision
in the three `main/*.md` prompt profiles.

Verified against current code (HEAD `fabce1b70`). Line numbers below are ACTUAL, not the
plan's. Baseline: 428 unit tests pass.

---

## 0. Cross-cluster dependency contract (read first)

My files import symbols that OTHER clusters rename. The implementer must apply MY edits
*after* the producing cluster lands its rename, or in the same coherent pass. The
producing renames I consume:

- `task_center._core.generator_summaries` → `_core/outcomes.py` (WS4): `TaskOutcome`→`Outcome`,
  `.summary`→`.text`, `task_outcome_from_row`, `parse_achieved_record`, `generator_outcomes`,
  `attempt_failure_line`, `latest_task_summary`, `EMPTY_SUMMARY_PLACEHOLDERS`. Confirmed
  present today at `_core/generator_summaries.py` (lines 31/50/72/106/126/138/193).
- `task_center.iteration.state.Iteration`, `task_center.workflow.state.Workflow`,
  `task_center.attempt.state.{Attempt,AttemptStatus}` → all → `_core/state.py` (WS6/D11).
- `Attempt.plan_spec`, `Attempt.evaluation_criteria`, `Attempt.evaluator_task_id`,
  `Attempt.generator_task_ids` field changes (WS2): `plan_spec`+`evaluation_criteria` DELETED;
  `evaluator_task_id`→`reducer_task_ids` (tuple). My recipes READ these fields and MUST stop.
- `Iteration.task_summary` → `Iteration.outcomes` (WS4). Read in `iterations.py` (lines 133/143).
- `Iteration.goal`/`Workflow.goal` → `iteration_goal`/`workflow_goal` (WS-vocab D2). Read in
  `iterations.py` (lines 80/96).

These appear in `drift`/`open_decisions` where ownership overlaps.

---

## CORE FILES (hand-edited logic)

### C1. `context_engine/recipes/evaluator.py` → `recipes/reducer.py` (RENAME + REWRITE)

Current (57 lines): `EVALUATOR_ID="evaluator"`, `_REQUIRED_FIELDS=frozenset({"attempt_id"})`,
`build_evaluator_context` calls `current_attempt_flat_blocks(attempt, task_store)` (the
bespoke evaluator assembly), targets `target_role="evaluator"`, emits `EVALUATOR_RECIPE`.

Target shape (mirror generator.py; reducer + generator share `_needs.py`):
- File renamed to `reducer.py`. `REDUCER_ID="reducer"`.
- `_REQUIRED_FIELDS = frozenset({"workflow_id", "attempt_id", "task_id"})` (now per-task,
  like generator — a reducer is a plan task with a `task_id`, not an attempt-level singleton).
- `build_reducer_context(scope, deps)`:
  - resolve `attempt_id`, `task_id`, `workflow_id`; `task = deps.task_store.get_task(task_id)`.
  - `needs = tuple(str(d) for d in task.get("needs") or ())`.
  - `blocks = needs_outcome_blocks(needs=needs, task_store=deps.task_store)` (shared helper, C3).
  - append the assigned-prompt block: the reducer's `prompt` lives in the task's
    `context_message` (same column the generator reads for `task_spec`). Block kind =
    `PLANNED_TASK_SPEC` (or a new kind — see open_decisions #5), `priority=REQUIRED`,
    `metadata={"tag": "assigned_prompt", "attrs": f'task_id="{task_id}"'}`.
  - `ContextPacket(target_role="reducer", target_id=task_id, canonical_refs=…(task_id=task_id), …)`.
- `REDUCER_RECIPE = ContextRecipe(id=REDUCER_ID, required_scope_fields=_REQUIRED_FIELDS, build=build_reducer_context)`.
- DELETE the import of `current_attempt_flat_blocks` and the whole flat-blocks code path.
- Update import `from task_center.context_engine.core import …` → `.engine import …` (C7).

Risk: this is a SHAPE CHANGE, not a rename. The evaluator was attempt-scoped + read
`plan_spec`/`evaluation_criteria`/all-generator-outcomes; the reducer is task-scoped + reads
ONLY its `needs` outcomes + its own prompt (plan §5, ADR §11 "a reducer sees only its
needs"). A convergent reducer that `needs` every generator recovers the global view. This
removes ALL reads of `attempt.plan_spec` and `attempt.evaluation_criteria` from this recipe.

### C2. `context_engine/recipes/generator.py` (CORE — remove plan_spec, swap to `_needs.py`)

Current shape (151 lines):
- L33: `from task_center._core.generator_summaries import task_outcome_from_row`.
- L34: `from …recipes._task_xml import block_task_body, task_attrs`.
- L60-70: emits a `<plan_spec>` block from `attempt.plan_spec` (kind `TASK_SPECIFICATION`).
- L72: `needs = tuple(str(dep) for dep in task.get("needs") or ())`.
- L73: `blocks.extend(_dependency_blocks(needs=needs, task_store=deps.task_store))`.
- L74-86: `<assigned_task task_id="…">` block (kind `PLANNED_TASK_SPEC`).
- L102-143: private `_dependency_blocks` — group_tag `"dependency"`, child_tag `"task"`,
  group_id `_DEPENDENCY_GROUP_ID="dependencies"`, builds `task_outcome_from_row`+`block_task_body`.

Target:
- DELETE the `attempt.plan_spec` block (L60-70) entirely (field is gone — WS2; ADR M2).
  After deletion, `attempt` is still fetched for `iteration_id` fallback (L51-54) — keep that.
- MOVE `_dependency_blocks` (L102-143) OUT to `_needs.py` as the shared `needs_outcome_blocks`
  (C3). In generator.py, replace L73 with
  `blocks.extend(needs_outcome_blocks(needs=needs, task_store=deps.task_store))` and import it.
- Drop now-unused imports: `task_outcome_from_row`, `block_task_body`, `task_attrs`,
  `_DEPENDENCY_GROUP_ID` (they move to `_needs.py`).
- Update module docstring (L1-18): drop the `<plan_spec>` bullet; `<dependency>` group →
  `<needs>` group; note symmetry with reducer. The "planner / evaluator concern" phrasing
  (L13) → "planner / reducer concern".
- Update `core` import (L25) → `engine`.

Risk: `attempt.plan_spec` read MUST go or the recipe references a dead field at runtime.
The `<assigned_task>` block stays (kind `PLANNED_TASK_SPEC`, tag `assigned_task`).

### C3. NEW `context_engine/recipes/_needs.py` (shared generator+reducer helper)

Extract from generator.py `_dependency_blocks`. Proposed signature + body:
```
def needs_outcome_blocks(*, needs: tuple[str, ...], task_store) -> list[ContextBlock]:
    if not needs: return []
    out = []
    for dep_id in needs:
        dep = task_store.get_task(dep_id)
        if dep is None: raise ContextEngineError(…)  # same invariant message, "needs"
        outcome = outcome_from_row(dep_id, dep)        # renamed task_outcome_from_row (WS4)
        text, pre_rendered = block_task_body(outcome)
        metadata = {"group_id": "needs", "group_tag": "needs", "child_tag": "task",
                    "attrs": task_attrs(outcome)}
        if pre_rendered: metadata["pre_rendered_xml"] = "true"
        out.append(ContextBlock(kind=ContextBlockKind.DEPENDENCY_SUMMARY, priority=MEDIUM, …))
    return out
```
Changes vs current `_dependency_blocks`: `group_tag` `"dependency"`→`"needs"`; `group_id`
const `"dependencies"`→`"needs"` (plan §4: wrapper `<dependency>`→`<needs>`, child stays
`<task>`). `child_tag` stays `"task"`. `kind` stays `DEPENDENCY_SUMMARY` (renderer default tag
for that kind is `"dependency"` — but every grouped block sets `child_tag` explicitly so the
default is never hit here; still, update renderer `_DEFAULT_TAGS["dependency_summary"]` →
`"needs"` for correctness — see C8). Imports: `ContextBlock/Kind/Priority` from packet,
`ContextEngineError` from exceptions, `block_task_body`+`task_attrs` from `_task_xml`,
`outcome_from_row` (renamed) from `_core/outcomes`.

Both generator.py and reducer.py import `needs_outcome_blocks` from here.

### C4. `context_engine/recipes/planner.py` (CORE — R1a fold of iterations.py + attempts.py)

Current (80 lines): imports `goal_iteration_blocks` from `recipes.iterations`,
`failed_attempt_blocks` from `recipes.attempts`; `build_planner_context` calls both and
concatenates. After R1a, `iterations.py` + `attempts.py` are FOLDED into planner.py (they
have no other consumer once evaluator.py stops using `current_attempt_flat_blocks` — C1).

Target:
- Inline the two block-builders that survive: `goal_iteration_blocks` (+ its privates
  `_goal_statement_block`, `_current_iteration_goal_child`, `_prior_iteration_blocks`,
  `current_iteration_group_id`, `current_iteration_group_attrs`) from `iterations.py`, and
  `failed_attempt_blocks` (+ `_render_failed_attempt_body`, `_evaluator_summary_if_ran`) from
  `attempts.py`. DROP `current_attempt_flat_blocks` + `_task_outcome_block` + the two
  `_*_KIND` consts (evaluator-only; C1 deletes their consumer).
- DELETE files `recipes/iterations.py` and `recipes/attempts.py`.
- Field reads to update (WS4/WS6/D2 — see §0): `workflow.goal`→`workflow.workflow_goal`;
  `iteration.goal`→`iteration.iteration_goal`; `iteration.task_summary`→`iteration.outcomes`
  (now a json list[Outcome], not a string — `parse_achieved_record` may collapse to a direct
  `Outcome` list read; coordinate with WS4 `from_record`).
- `_evaluator_summary_if_ran` reads `attempt.evaluator_task_id` (L197) + `task.get("summaries")`
  (L200). Under WS2 `evaluator_task_id`→`reducer_task_ids` (tuple) and WS4
  `summaries`→`outcomes`. The retry/feedback block (`failed_attempt_blocks`) is WS5's
  feedback path: plan §5 says retry renders failed-TASK outcomes (any role) + `fail_reason`,
  NOT an `<evaluator_summary>`. So `<evaluator_summary>` is DROPPED; rework
  `_render_failed_attempt_body` to render every terminal failed task's outcome (generators +
  the reducer if it ran) via `render_task_element`, then the `<failure>` line from
  `attempt_failure_line`. See open_decisions #3.
- Update `core` import → `engine`.
- Update planner docstring: drop the "evaluator" mention (L1-13 region of iterations.py note).

Risk: largest core edit in the cluster. The fold is structural; the field renames are
WS4/WS6-coupled. `_render_failed_attempt_body` rework (drop `<evaluator_summary>`, render
failed-task outcomes) is the WS5 retry-projection requirement — verify both
failed-reducer AND failed-generator-before-reducer cases (plan §10).

### C5. `context_engine/recipes/_task_xml.py` (CORE — field renames to compile)

Current (112 lines). Uses `TaskOutcome` (L20), `.summary` (L73, L94),
`EMPTY_SUMMARY_PLACEHOLDERS` (L19), `outcome.children`/`outcome.failure`/`outcome.local_id`/
`outcome.status` (L58-101). `STRUCTURAL_CLOSERS` (L29-41) lists `</evaluator_summary>`,
`</evaluation_criteria>`, `</plan_spec>`, `</dependency>`.

Target (WS4 field renames + vocab):
- `TaskOutcome`→`Outcome`; `.summary`→`.text` (L73, L94); import from `_core/outcomes`.
- `STRUCTURAL_CLOSERS`: DROP `</evaluator_summary>`, `</evaluation_criteria>`, `</plan_spec>`
  (those tags no longer emitted — C1/C2/C9). REPLACE `</dependency>`→`</needs>`. Keep
  `</task>`, `</failure>`, `</assigned_task>`, `</attempt>`, `</iteration>`, `</goal>`,
  `</iteration_goal>`. ADD `</assigned_prompt>` (new reducer tag) and `</needs>`.
- Docstring (L4-14): "evaluator outcomes"→"reducer outcomes"; "`task_center._core.
  generator_summaries`"→"`_core.outcomes`".

OWNERSHIP NOTE (drift): `TaskOutcome`→`Outcome`/`.summary`→`.text` is WS4's field rename
landing across the codebase. `_task_xml.py` is in MY read-list but the rename is WS4-driven.
Decision: WS5 (this cluster) OWNS the `_task_xml.py` body edits (it's a recipe-layer file);
WS4 owns the `_core/outcomes.py` definition. They must land together. Flagged in drift.

### C6. `agents/profile/main/planner.md` (CORE — M2 + full vocab rework)

This is the M2/D3 decision file. See §M2 below for the full justified spec. Summary of edits:
- L29: drop "an evaluator judges it against your rubric" → "a reducer task gates it".
- L44-46: `<iteration … status="prior/current">` → `position="prior/current"`; rewrite the
  `<attempt>` retry-evidence bullet to drop `<plan_spec>`/`<evaluation_criteria>`/
  `<evaluator_summary>`/`<status_summary>`/`<failed_criteria>` and describe failed-task
  outcomes + `<failure>`.
- L60/64: terminal signatures `submit_plan_closes_goal(plan_spec, evaluation_criteria, tasks,
  task_specs)` → `(tasks, reducers)` (NO plan_spec, NO evaluation_criteria). See open_decisions
  #4 for inline-vs-dict task_spec shape (gated on WS2 `_schemas.py`).
- L82-85: DELETE the `plan_spec` field bullet and the `evaluation_criteria` bullet; ADD a
  `reducers` field bullet (each `{id, needs, prompt}`, `prompt` is the gate authority).
- L86-93: `tasks` items `{id, agent_name, deps}` → `{id, agent_name, needs}`; `deps`→`needs`;
  drop `verifier` from agent_name choices (WS3); `task_specs`→per-task `task_spec` (or keep
  dict — open_decisions #4).
- L104/110/113/114/122: `evaluator`→`reducer`, `evaluation_criteria`→`reducers`,
  `plan_spec` references removed/reworded.
- M2 narrative: the global-narrative framing that `plan_spec` carried is DISTRIBUTED, not
  inlined verbatim — reinforce L111 "write each task_spec so the agent can act without
  re-reading the contract" now that the crutch is gone, and make each reducer `prompt` the
  per-gate authority. Do NOT instruct pasting a shared narrative into every task_spec
  (contradicts L114 "don't paste content").

Risk: largest prose file. Every removed field must vanish from signatures, field lists,
hard-validity rules, AND output-discipline prose, or the prompt describes a schema the
tool no longer accepts.

### C7. `context_engine/core.py` → `context_engine/engine.py` (RENAME)

Current `core.py` (78 lines): defines `ContextEngine`, `ContextEngineDeps`,
`ContextPacketStoreProtocol`, re-exports exceptions. Plan §3 / §4: `core.py`→`engine.py`
(D-rename, "engine" is the public name). Importers to repoint (verified — 6 src + ~12 test):

src: `task_center/__init__.py:41,72`, `_core/terminal_tool_routing.py:15`,
`entry/bootstrap.py:31`, `recipes_registry.py:19`, `recipes/{planner,generator,reducer}.py`,
`agent_launch/composer.py:24,47`.
Doc-string-only string refs (`core.py:build_skill_message`) in
`tools/skills/_factory.py:5`, `agents/skills/loader.py:3`, and planner.md frontmatter comment
(planner.md:21,24) — these are PROPAGATION (string fix), see P-list.

Risk: low (pure module rename), but `task_center/__init__.py` lazy `_EXPORTS` map (the
`"task_center.context_engine.core"` tuple strings) must update or the lazy import breaks.

---

## PROPAGATION FILES (mechanical vocab / string-match only)

### P1. `context_engine/scope.py` — `for_evaluator` → `for_reducer(task_id)`

`for_evaluator` (L88-101) takes `workflow_id, iteration_id, attempt_id`. Target `for_reducer`
adds `task_id` (the reducer is now per-task). Proposed:
```
@classmethod
def for_reducer(cls, *, workflow_id, iteration_id, attempt_id, task_id) -> ContextScope:
    return cls(workflow_id=…, iteration_id=…, attempt_id=…, task_id=task_id)
```
(Same shape as `for_generator`.) Borderline core/propagation — it's a mechanical
factory-mirror of `for_generator`, so PROPAGATION, but flagged: WS1 callers move with it
(`attempt/launch.py:354,359`, `attempt/stage_advancer.py:209`, and the signature test
`test_agent_launch_factory_for_role.py:11,30` expecting `for_evaluator`). Those callers are
WS1/WS2-owned, not mine; I only own the scope.py method body.

### P2. `context_engine/tag_dictionary.py` — descriptor edits

- DELETE descriptors: `plan_spec` (L64), `evaluation_criteria` (L71-75), `evaluator_summary`
  (L76-80) — verified nothing emits those tags after C1/C2/C9.
- RENAME `dependency` descriptor (L86-90) `tag="dependency"`→`tag="needs"`, label
  "upstream task output" (unchanged or "upstream needs output").
- ADD `assigned_prompt` descriptor (label e.g. "your reducer prompt") for the new reducer tag.
- The `iteration` `position` filters (L48-53), `attempt`, `task`, `assigned_task`,
  `iteration_goal`, `goal` descriptors stay.

### P3. `context_engine/renderer.py` — `_DEFAULT_TAGS` cleanup

`_DEFAULT_TAGS` (L38-47): `"task_specification": "plan_spec"` (L44) becomes DEAD once C1/C2
remove all `TASK_SPECIFICATION`-kind blocks — DELETE it. `"dependency_summary": "dependency"`
(L46) → `"needs"`. `"prior_iteration_summary": "summary"` unchanged. The docstring example
`</attempt_plan>` (L17) is stale (no such tag) — leave unless touched. Also DELETE the
`TASK_SPECIFICATION` member from `packet.py:38` ContextBlockKind enum once unused (verify no
remaining ref — currently only generator.py:63 + attempts.py:125, both removed). NOTE:
`packet.py` ContextBlockKind enum is shared infra — coordinate, but the member removal is
mechanical.

### P4. `context_engine/agent_directives.py` — `AGENT_DIRECTIVES` table

`AGENT_DIRECTIVES` (L17-23): DROP `"verifier"` (WS3); change `"evaluator": "Verify the
current attempt against <evaluation_criteria>."` → `"reducer": "Digest your <needs> and gate
against <assigned_prompt>."` (or similar; the directive is one imperative line). `"executor"`
stays "Complete <assigned_task>."; `"planner"` stays.

### P5. `agents/profile/main/executor.md` — frontmatter + prose vocab

- L26: terminal `submit_execution_handoff`→`submit_workflow_handoff` (WS-rename; FLAG-5).
- L37,39,52: `submit_execution_handoff` references → `submit_workflow_handoff`.
- L29: `terminal_routing: executor_routing.py` unchanged.
- L51: "the attempt's evaluator reads" → "the attempt's reducer reads".
- `context_recipe: generator` (L32) unchanged.
- M2: executor.md body has NO `plan_spec`/global-narrative dependence (verified) — body needs
  only the evaluator→reducer wording fix. The plan_spec dependence lives in the SKILL file
  (see open_decisions #2), not this profile.

### P6. `agents/profile/main/evaluator.md` → `reducer.md` (RENAME)

Owned by WS1 (role/profile), but listed here for completeness of MY cluster's prompt set.
The reducer.md body must be rewritten: drop `<plan_spec>`/`<evaluation_criteria>`/per-task
`<task>`-against-criteria framing → reducer sees `<needs>` outcomes + `<assigned_prompt>`,
terminals `submit_reduction_success/failure`. `context_recipe: evaluator`→`reducer`;
`role: evaluator`→`reducer`. Cross-listed; primary owner WS1.

---

## M2 / D3 DECISION (open_decisions item #1) — JUSTIFIED

**Question:** does removing `plan_spec` (D3) require (a) reworking planner.md prose to drop
the global narrative, AND/OR (b) inlining the narrative slice into each task_spec?

**Evidence gathered:**
- planner.md:82 — `plan_spec` is "the contract for this graph in plain prose … The evaluator
  sees this as framing." This is a GLOBAL NARRATIVE.
- planner.md:60,64 — `plan_spec` is a planner SUBMISSION FIELD (`submit_plan_closes_goal(
  plan_spec, evaluation_criteria, tasks, task_specs)`). WS2's `_schemas.py` removes it.
- executor SKILL.md:9,17 — "`<plan_spec>` is the surrounding contract"; task specs "were
  chosen to fit the surrounding `<plan_spec>`". Generator DOES lean on the narrative today.
- evaluator SKILL.md:9 — "The attempt's `<plan_spec>` frames the scope; the criteria are the
  authority." Evaluator leans on it too.
- ADR §11 — "a reducer sees only its `needs`"; §5 — generator is "symmetric with reducer".
  The end-state model has NO global narrative.

**VERDICT: BOTH (a) and (b), with precise shapes.**

**(a) is MANDATORY, independent of the framing argument.** Because `plan_spec` is a
submission field that WS2 deletes from the schema, planner.md's terminal signatures (L60,64),
field list (L82), hard-validity rule (L104), and output-discipline prose (L114,122) MUST drop
it regardless. (a) is not contingent on anything — it is forced by the schema change.

**(b) is "DISTRIBUTE," NOT "duplicate."** The coherent end-state (ADR §11, §5) is that there
is no shared narrative; each task is self-contained. So the framing `plan_spec` carried is
decomposed into two destinations:
1. Each **`task_spec`** — planner.md:111 / planner SKILL.md:46 already say "write each
   task_spec so the executor can act without re-reading the plan contract." Reinforce this
   now that the crutch is gone: the task_spec must carry the inputs/outputs/success-conditions
   that previously relied on `plan_spec` for context.
2. Each **reducer `prompt`** — replaces `evaluation_criteria` as the per-gate authority
   (plan §1: "`prompt` required + nonblank"). The reducer's `prompt` carries the acceptance
   framing for the slice it gates.

**Do NOT instruct pasting a shared narrative into every task_spec** — that directly
contradicts planner.md:114 "Do not inline them into `plan_spec` or `task_specs`" and the
"don't paste content" principle. The decomposition is per-task self-containment, not
copy-paste of one paragraph N times.

**Concrete planner.md edits for M2 (subset of C6):**
- L60,64: signatures → `(tasks, reducers)` (no plan_spec, no evaluation_criteria).
- L82: DELETE plan_spec field bullet. L83-85: DELETE evaluation_criteria; ADD `reducers` field.
- L111: keep + strengthen "generator independence" — explicitly state each `task_spec` is
  now the generator's ONLY framing (no shared plan_spec to lean on).
- Add one sentence: "Each reducer's `prompt` is the acceptance authority for the slice it
  gates; scope it to what its `needs` produce."
- L114,122: drop plan_spec from the "durable inputs" lists.

**executor.md / executor SKILL.md (M2 fallout):** executor.md BODY has no plan_spec
dependence (verified) — no M2 edit there beyond P5's evaluator→reducer wording. BUT executor
SKILL.md:9,17 DOES depend on `<plan_spec>` as "the surrounding contract." That file is
WS9-owned (skills), OUTSIDE my read-list, but load-bearing for M2: it must drop the
`<plan_spec>` references and reframe task_spec + `<needs>` as the self-contained inputs.
FLAGGED for WS9 (open_decisions #2). A generator skill that still says "fit the surrounding
plan_spec" contradicts the new model.

---

## DRIFT (plan claims vs current code)

1. **Plan §3/§5 says `core.py` imports recipes; the dependency is the reverse + via `_core`.**
   `recipes/{planner,generator,evaluator}.py` import `from …context_engine.core import
   ContextEngineDeps` (L18/25/15). The rename `core.py`→`engine.py` repoints these. No issue,
   just confirming direction.
2. **`_core/state.py` does NOT exist yet.** My recipes import `iteration.state.Iteration`,
   `workflow.state.Workflow`, `attempt.state.{Attempt,AttemptStatus}` (iterations.py:37-38,
   attempts.py:32,53). Plan §2/D11 consolidates to `_core/state.py` (WS6). My edits must
   repoint AFTER WS6 lands, or import-error. Flagged.
3. **Plan §5 "deleted: current_attempt_flat_blocks" — but it lives in `attempts.py`, which
   R1a folds into planner.py.** So the deletion happens AS PART OF the fold (C4), not as a
   standalone op. attempts.py also still has the evaluator-only `_TASK_OUTCOME_KIND`/
   `_EVALUATION_CRITERIA_KIND` consts (L63-64) — both die with the flat-blocks deletion.
4. **Plan §5/§7 retry path: WS5 says "generalizes `attempt_failure_line`."** Current
   `attempt_failure_line` lives in `_core/generator_summaries.py:138` (WS4 → `outcomes.py`),
   NOT in the recipe layer. The recipe (`_render_failed_attempt_body`, attempts.py:169) CALLS
   it. So WS5's retry rework is in planner.py (after fold), calling the WS4-renamed helper.
5. **`TaskOutcome.summary` rename ownership.** `_task_xml.py` does the field access
   (`.summary` L73,94) but the type is defined in WS4's `outcomes.py`. WS5 owns the recipe-
   layer body; WS4 owns the dataclass. Must land together (flagged in C5).
6. **planner.md L44 uses `status="prior/current"` for `<iteration>`** but the CODE emits
   `position="prior/current"` (iterations.py:47,142). The prompt is ALREADY STALE vs current
   code (pre-existing drift). Fix to `position=` during C6.
7. **planner.md L46 lists `<status_summary>`, `<failed_criteria>`, `<passed_criteria>`** as
   `<attempt>` children — the CODE never emits those (attempts.py `_render_failed_attempt_body`
   emits only `<task>`, optional `<evaluator_summary>`, `<failure>`). Pre-existing prompt drift;
   the C6 rewrite removes the whole stale list.

---

## OPEN DECISIONS (concrete proposals)

1. **M2/D3 (the assigned decision)** — RESOLVED above: BOTH (a) mandatory schema/prose drop +
   (b) distribute (not duplicate) framing into per-task `task_spec` + reducer `prompt`.
2. **executor + evaluator SKILL.md plan_spec dependence (WS9-owned, flagged).** These two
   files (`backend/config/skills/executor/SKILL.md:9,17`,
   `backend/config/skills/evaluator/SKILL.md:9`) reference `<plan_spec>`/`<evaluation_criteria>`
   and contradict the new model. Proposal: WS9 drops those refs; executor skill reframes
   `<needs>` + `<assigned_task>` as self-contained; the evaluator→reducer skill (renamed)
   reframes `<assigned_prompt>` as the authority. NOT my cluster to edit, but a blocker for a
   coherent M2 — must be in WS9's list.
3. **`<evaluator_summary>` in the retry/failed-attempt body.** Proposal: DROP it. Plan §5 says
   retry renders failed-TASK outcomes (any role) + `fail_reason`, not an evaluator commentary
   block. `_render_failed_attempt_body` renders `render_task_element` per terminal failed task
   (generators + reducer if it ran), then the `<failure>` line. Removes
   `_evaluator_summary_if_ran` (attempts.py:193) and the `</evaluator_summary>` closer (C5).
4. **planner submission `task_spec` shape: inline-per-task vs `task_specs` dict.** Plan §2
   shows generator `{local_id, agent_name, needs, task_spec}` (inline). Current planner.md uses
   a separate `task_specs: dict[id,str]`. Proposal: follow §2 (inline per-task `task_spec`) to
   match the DTO, but this is GATED on WS2 `tools/submission/planner/_schemas.py` — the prompt
   must mirror whatever shape the schema lands. Flag: coordinate with WS2 before finalizing
   planner.md L86-93/101.
5. **Reducer assigned-prompt block kind.** Proposal: reuse `PLANNED_TASK_SPEC` kind (renderer
   default tag `assigned_task`) but OVERRIDE `metadata["tag"]="assigned_prompt"`. Alternative:
   add a `ContextBlockKind.ASSIGNED_PROMPT`. Recommend the metadata-override (no enum churn;
   `kind` is provenance-only per renderer docstring). Add `assigned_prompt` to tag_dictionary
   (P2) + `_DEFAULT_TAGS` only if a default is desired (not needed if always overridden).
6. **`for_reducer` core vs propagation.** Classified PROPAGATION (mechanical mirror of
   `for_generator`), but its WS1 callers (`launch.py`, `stage_advancer.py`, the signature test)
   move with the WS1 rename — confirm WS1 owns those, not WS5.

---

## File partition summary

CORE (hand-edited logic):
- `context_engine/recipes/evaluator.py`→`reducer.py` (rewrite, C1)
- `context_engine/recipes/generator.py` (remove plan_spec, swap helper, C2)
- `context_engine/recipes/_needs.py` (NEW shared helper, C3)
- `context_engine/recipes/planner.py` (R1a fold + retry rework, C4)
- `context_engine/recipes/_task_xml.py` (field renames + closer list, C5)
- `agents/profile/main/planner.md` (M2 + full vocab, C6)
- `context_engine/core.py`→`engine.py` (module rename + importers, C7)
- DELETED by fold: `context_engine/recipes/iterations.py`, `recipes/attempts.py`

PROPAGATION (mechanical):
- `context_engine/scope.py` (for_reducer mirror, P1)
- `context_engine/tag_dictionary.py` (descriptor add/del/rename, P2)
- `context_engine/renderer.py` (_DEFAULT_TAGS cleanup, P3)
- `context_engine/packet.py` (drop TASK_SPECIFICATION enum member, P3)
- `context_engine/agent_directives.py` (table edit, P4)
- `agents/profile/main/executor.md` (evaluator→reducer wording + handoff rename, P5)
- `agents/profile/main/evaluator.md`→`reducer.md` (cross-listed, WS1-primary, P6)

UNCHANGED in this cluster (verified no edit needed): `recipes_registry.py` (generic),
`recipes/__init__.py` (auto-discovers `*_RECIPE`; reducer.py's `REDUCER_RECIPE` auto-picks
up — NO edit), `context_outline.py` (reads metadata generically; works once tag_dictionary +
recipes are correct — no code edit), `task_guidance.py` (keyed by agent name via
AGENT_DIRECTIVES; no edit beyond P4's table).
