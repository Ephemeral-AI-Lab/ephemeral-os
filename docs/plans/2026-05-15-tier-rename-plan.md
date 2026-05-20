# Plan v2: Semantic Refactor of `task_center` & `context_engine` — Mission/Episode/Attempt → Goal/Iteration/Trial

## 0. RALPLAN-DR Summary

### Principles (5)
1. **Atomic single-PR rename, no shims.** SQLAlchemy `create_all` + active dev branch + no external consumers → phased deprecation is overhead with no payoff. Reject any "compat alias" suggestion in review.
2. **Symbols rename; tier-agnostic values stay.** Enum *class names* rename (`MissionStatus → GoalStatus`), but their string *values* stay (`"open"`, `"succeeded"`, `"partial_continuation"`) because they describe relationships, not tier identity. **Explicit carve-out: `SpawnReason` values (§2.5.1).** Its value strings textually embed the renamed tier noun (`"attempt_planner"` → `"trial_planner"`) and are treated as part of the renamed surface.
3. **LLM-facing artifacts coordinate or break inference.** Recipe headings, `metadata["group_heading"]`/`metadata["subheading"]` strings, and the 8 agent prompts in `backend/src/agents/profile/main/` must land in the same PR. Any drift between prompt prose and emitted headings degrades agent quality silently.
4. **Reading A only; defer Reading B.** Tighten the planner-recipe "totality" framing (one `# Goal` H1 + H2 sub-sections + a separate `# Current Iteration` H1) using the renderer's existing `group_heading` mechanism — **no renderer change required**. The Reading-A acceptance lock is a *structural* assertion (block-kind order + heading-line presence), not a brittle full-text snapshot, so Reading B can replace it without rewriting the test.
5. **Verbs stay verbs.** Class `Attempt` → `Trial`, but the lowercase prose verb "attempt" (in agent prompts, docstrings, tool descriptions) is **not** renamed. Tool names `submit_full_plan`/`submit_partial_plan` describe the verb-action and stay.

### Decision Drivers (top 3)
1. **Blast-radius management.** 117 backend files × ~1240 hits demands a *symbol-table* approach: a canonical mapping in §2 that an Executor can drive `ast-grep`/`sed`/`ruff --fix` from without judgment calls.
2. **LLM prompt coherence.** Eight agent profiles + four recipe heading constants + ~30 prose mentions of "Mission/Episode/Attempt" must land coherent in one commit or planners emit broken JSON.
3. **Test-suite stability.** ~30 unit tests already encode the old strings (e.g. `test_recipes_planner.py` snapshots `"# Mission / Current Episode"`). Their string-only updates must be enumerated in §2 so they're not surprises in §4.

### Viable Options (with bounded pros/cons)

**Option A — Pure rename + Reading-A totality reframing via existing `group_heading` (CHOSEN).**
- Pros: Smallest diff that delivers the user's stated scope; preserves the existing `mission_episode_blocks → goal_iteration_blocks` API shape; planner-recipe contract surface is unchanged; **renderer.py untouched** (Reading A reuses the existing group-heading grouping).
- Cons: Doesn't introduce a structural way to enforce "one totality section per packet" — still relies on string heading conventions. Acceptable for now.

**Option B — Rename + new `ContextBlockKind.TOTALITY_FRAME` block + restructured `ContextRefs.totality_ref`.**
- Pros: Lets the renderer enforce one totality section structurally; future evaluator/generator recipes can reuse the kind.
- Cons: New kind value, new renderer code-path, new packet schema, new ~6 tests. Scope creep for a rename PR. **Invalidated:** belongs in a follow-up PR with its own design discussion.

**Option C — Rename + restructured `PlannerSubmission` API (rename `submit_partial_plan` → `submit_partial_iteration` + tool-name verb churn).**
- Pros: Maximally consistent vocabulary across tier nouns and tool verbs.
- Cons: Tool names are stable contract surface for any external evaluator harness; renames invalidate every agent prompt's tool-call examples and every audit/observability dashboard that filters by tool name; doubles the agent-prompt rewrite. **Invalidated:** contradicts Principle 5.

### Pre-mortem (3 failure scenarios)

1. **Stale agent-prompt drift → planner emits malformed JSON.** Executor renames recipe heading constants but misses the corresponding "you will see `# Mission`" sentence in `planner.md`. Planner LLM looks for a heading that no longer exists, hallucinates structure, produces invalid `submit_full_plan` payload. **Mitigation:** Phase 7 ships in the same commit as Phase 5; Phase 7's verification step explicitly greps for the old heading literals across `agents/profile/`; acceptance criterion #1 catches stragglers.
2. **Test-fixture string assert tail miss.** `test_recipes_planner.py` and `test_recipes_other.py` snapshot heading strings (e.g. `"# Mission / Current Episode"`). Executor renames in `_shared.py` but misses 5-10 test fixture strings → red phase. **Mitigation:** §2 enumerates each test file by path with the exact string churn; Phase 9 verification re-runs `pytest backend/tests/unit_test/test_task_center/test_context_engine/`. The new Reading-A acceptance test uses structural (not full-text) assertions so it survives later Reading-B rewrites.
3. **task_center_runner audit-shape integration miss.** `task_center_runner/audit/recorder.py` emits payload fields `mission_id`/`episode_id`/`attempt_id` and `test_emission_shape.py` asserts them. Executor renames DB columns + DTOs but forgets the audit payload JSON keys → integration smoke fails at scenario replay. **Mitigation:** §3 includes audit/recorder.py + scenarios in Phase 8 with named verification; §2's audit-payload row makes the JSON keys explicit. The `SpawnReason` carve-out (§2.5.1) is called out as an *observable contract change* so external dashboards know to update.

### Expanded test plan (deliberate mode) — see §4 for full detail.

---

## 1. Scope & Non-Goals

### IN scope (explicit)
- **Symbols renamed:** every class, dataclass, enum, function, file, directory, table, column, JSON key, enum *value where tier-specific* (e.g. `attempt_sequence_no` → `trial_sequence_no`, `SpawnReason` values per §2.5.1), context-block `kind` string value, recipe `metadata["group_heading"]` and `metadata["subheading"]` strings, agent-prompt prose, audit-event payload field names, and `TaskCenterInvariantViolation` message-string tier nouns.
- **Reading-A prompt reframing in the planner recipe:** restructured headings for iteration ≥ 2 so the totality is one coherent `# Goal` H1 with H2 sub-sections (`## Goal`, `## Iteration N accepted plan`, `## Iteration N summary`), separated from `# Current Iteration`. Implemented via the renderer's existing `group_heading` mechanism — **no renderer change**.
- **DB-safety gate:** a startup check that raises if legacy tables (`missions`, `episodes`, `attempts`) are present after rename, plus a one-shot drop script (§Phase 10).

### OUT of scope (explicit — must not creep)
- New `ContextBlockKind.TOTALITY_FRAME` or `ContextRefs.totality_ref` (Reading B → follow-up).
- Renaming the tools `submit_full_plan` / `submit_partial_plan` or any `submit_*` verb-action tool.
- Renaming the lowercase prose verb "attempt" in agent prompts, docstrings, error strings ("attempt to …", "first attempt at …").
- Renaming tier-agnostic enum *values* — `"open"`, `"succeeded"`, `"failed"`, `"cancelled"`, `"initial"`, `"partial_continuation"`, `"plan"`, `"generate"`, `"evaluate"`, `"closed"`, `"running"`, `"passed"`, `"planner_failed"`, `"generator_failed"`, `"evaluator_failed"`, `"startup_failed"` all retain their string values; only their enum *class names* rename. (`SpawnReason` is an explicit carve-out, §2.5.1.)
- Renaming `deferred_goal` (field, column, JSON key) — "continuation" is tier-agnostic.
- Renaming `requested_by_task_id`, `planner_task_id`, `task_center_run_id`, `evaluator_task_id`, `generator_task_ids` — these reference task IDs and the entry-task layer, not the renamed tier.
- Renaming the *prose verbs* "plan", "execute", "evaluate", "iterate".
- Backwards-compat aliases, deprecation shims, or phased rollout.
- Any logic change beyond the Reading-A reframing in `_shared.py` and the Phase-10 DB-safety gate.

---

## 2. Naming Inventory (canonical mapping)

### 2.1 Tier class & enum class names

| Old | New | Source file |
|---|---|---|
| `Mission` | `Goal` | `task_center/mission/state.py` |
| `MissionStatus` | `GoalStatus` | `task_center/mission/state.py` |
| `MissionClosureReport` | `GoalClosureReport` | `task_center/mission/state.py` |
| `CloseReportDeliveryStatus` | `CloseReportDeliveryStatus` (unchanged — tier-agnostic) | `task_center/mission/state.py` |
| `CloseReportDeliveryResult` | `CloseReportDeliveryResult` (unchanged) | `task_center/mission/state.py` |
| `MissionStarter` | `GoalStarter` | `task_center/mission/starter.py` |
| `StartedMission` | `StartedGoal` | `task_center/mission/starter.py` |
| `MissionRecord` | `GoalRecord` | `db/models/mission.py` |
| `Episode` | `Iteration` | `task_center/episode/state.py` |
| `EpisodeStatus` | `IterationStatus` | `task_center/episode/state.py` |
| `EpisodeCreationReason` | `IterationCreationReason` | `task_center/episode/state.py` |
| `EpisodeClosureReport` | `IterationClosureReport` | `task_center/episode/state.py` |
| `EpisodeManager` | `IterationManager` | `task_center/episode/manager.py` |
| `EpisodeClosureRouter` | `IterationClosureRouter` | `task_center/mission/close_report_router.py` |
| `EpisodeRecord` | `IterationRecord` | `db/models/episode.py` |
| `Attempt` | `Trial` | `task_center/attempt/state.py` |
| `AttemptStage` | `TrialStage` | `task_center/attempt/state.py` |
| `AttemptStatus` | `TrialStatus` | `task_center/attempt/state.py` |
| `AttemptFailReason` | `TrialFailReason` | `task_center/attempt/state.py` |
| `AttemptedPlanEntry` | `PriorTrialEntry` | `task_center/episode/state.py` |
| `AttemptOrchestrator` | `TrialOrchestrator` | `task_center/attempt/orchestrator.py` |
| `AttemptDeps` | `TrialDeps` | `task_center/attempt/runtime.py` |
| `AttemptRecord` | `TrialRecord` | `db/models/attempt.py` |
| `AttemptPlanFailed` (ClosureOutcome) | `TrialPlanFailed` | `task_center/episode/state.py` |

### 2.2 Closure-outcome variants
| Old | New |
|---|---|
| `TerminalSuccess` | `TerminalSuccess` (unchanged) |
| `SuccessContinue` | `SuccessContinue` (unchanged) |
| `AttemptPlanFailed` | `TrialPlanFailed` |
| `ClosureOutcome` (union) | `ClosureOutcome` (unchanged; alias union narrows to renamed variant) |

### 2.3 Files & directories

| Old path | New path |
|---|---|
| `backend/src/task_center/mission/` | `backend/src/task_center/goal/` |
| `backend/src/task_center/mission/state.py` | `backend/src/task_center/goal/state.py` |
| `backend/src/task_center/mission/starter.py` | `backend/src/task_center/goal/starter.py` |
| `backend/src/task_center/mission/handler.py` | `backend/src/task_center/goal/handler.py` |
| `backend/src/task_center/mission/close_report_router.py` | `backend/src/task_center/goal/close_report_router.py` |
| `backend/src/task_center/episode/` | `backend/src/task_center/iteration/` |
| `backend/src/task_center/episode/state.py` | `backend/src/task_center/iteration/state.py` |
| `backend/src/task_center/episode/manager.py` | `backend/src/task_center/iteration/manager.py` |
| `backend/src/task_center/attempt/` | `backend/src/task_center/trial/` |
| `backend/src/task_center/attempt/state.py` | `backend/src/task_center/trial/state.py` |
| `backend/src/task_center/attempt/orchestrator.py` | `backend/src/task_center/trial/orchestrator.py` |
| `backend/src/task_center/attempt/orchestrator_registry.py` | `backend/src/task_center/trial/orchestrator_registry.py` |
| `backend/src/task_center/attempt/runtime.py` | `backend/src/task_center/trial/runtime.py` |
| `backend/src/task_center/attempt/launch.py` | `backend/src/task_center/trial/launch.py` |
| `backend/src/task_center/attempt/dispatcher.py` | `backend/src/task_center/trial/dispatcher.py` |
| `backend/src/task_center/attempt/contexts.py` | `backend/src/task_center/trial/contexts.py` |
| `backend/src/task_center/attempt/generator_dag.py` | `backend/src/task_center/trial/generator_dag.py` |
| `backend/src/db/models/mission.py` | `backend/src/db/models/goal.py` |
| `backend/src/db/models/episode.py` | `backend/src/db/models/iteration.py` |
| `backend/src/db/models/attempt.py` | `backend/src/db/models/trial.py` |
| `backend/src/db/stores/mission_store.py` | `backend/src/db/stores/goal_store.py` |
| `backend/src/db/stores/episode_store.py` | `backend/src/db/stores/iteration_store.py` |
| `backend/src/db/stores/attempt_store.py` | `backend/src/db/stores/trial_store.py` |
| `backend/src/task_center/context_engine/recipes/attempt_landscape.py` | `backend/src/task_center/context_engine/recipes/trial_landscape.py` |
| `backend/tests/unit_test/test_task_center/test_domain/test_mission_dto.py` | `…/test_goal_dto.py` |
| `backend/tests/unit_test/test_task_center/test_domain/test_episode_dto.py` | `…/test_iteration_dto.py` |
| `backend/tests/unit_test/test_task_center/test_domain/test_episode_closure_report.py` | `…/test_iteration_closure_report.py` |
| `backend/tests/unit_test/test_task_center/test_domain/test_episode_facade_imports.py` | `…/test_iteration_facade_imports.py` |
| `backend/tests/unit_test/test_task_center/test_domain/test_attempt_dto.py` | `…/test_trial_dto.py` |
| `backend/tests/unit_test/test_task_center/test_persistence/test_mission_store.py` | `…/test_goal_store.py` |
| `backend/tests/unit_test/test_task_center/test_persistence/test_episode_store.py` | `…/test_iteration_store.py` |
| `backend/tests/unit_test/test_task_center/test_persistence/test_attempt_store.py` | `…/test_trial_store.py` |
| `backend/tests/unit_test/test_task_center/test_context_engine/test_attempt_landscape.py` | `…/test_trial_landscape.py` |
| `backend/src/task_center_runner/scenarios/initial_mission.py` | `…/initial_goal.py` |
| `backend/src/task_center_runner/scenarios/nested_mission.py` | `…/nested_goal.py` |
| `backend/src/task_center_runner/scenarios/attempt_budget_exhausted.py` | `…/trial_budget_exhausted.py` |
| `backend/src/task_center_runner/scenarios/attempt_retry_evaluator_failure.py` | `…/trial_retry_evaluator_failure.py` |
| `backend/src/task_center_runner/scenarios/attempt_retry_generator_failure.py` | `…/trial_retry_generator_failure.py` |
| `backend/src/task_center_runner/scenarios/attempt_retry_planner_failure.py` | `…/trial_retry_planner_failure.py` |
| `backend/src/task_center_runner/scenarios/episodic_continuation.py` | `…/iterative_continuation.py` |
| `backend/src/task_center_runner/scenarios/_utils/mission_helpers.py` | `…/_utils/goal_helpers.py` |

### 2.4 Database tables, columns, and constraints

| Old (DB) | New (DB) |
|---|---|
| Table `missions` | `goals` |
| Table `episodes` | `iterations` |
| Table `attempts` | `trials` |
| Column `mission_id` (in `episodes`, FK) | `goal_id` |
| Column `episode_id` (in `attempts`, FK) | `iteration_id` |
| Column `episode_ids` (JSON in `missions`) | `iteration_ids` |
| Column `attempt_ids` (JSON in `episodes`) | `trial_ids` |
| Column `attempt_sequence_no` (in `attempts`) | `trial_sequence_no` |
| Column `episode_id` final-pointer (in `missions.final_outcome.final_episode_id` JSON key) | `final_iteration_id` |
| Column `final_attempt_id` (in `missions.final_outcome` JSON key) | `final_trial_id` |
| Column `goal` (in `missions`, `episodes`) | **unchanged** (`goal` is the noun for the renamed Goal tier; describes contents not tier) |
| Column `sequence_no` (in `episodes`) | **unchanged** (kept generic; renaming to `iteration_sequence_no` is gratuitous) |
| Column `deferred_goal` | **unchanged** (Principle 2; "continuation" is a relationship) |
| Column `attempt_budget` (in `episodes`) | `trial_budget` |
| Column `task_specification`, `task_summary`, `evaluation_criteria`, `planner_task_id`, `evaluator_task_id`, `generator_task_ids`, `fail_reason`, `stage`, `status`, `created_at`, `updated_at`, `closed_at`, `final_outcome` | **unchanged** |
| FK target string `"missions.id"` | `"goals.id"` |
| FK target string `"episodes.id"` | `"iterations.id"` |
| FK target string `"task_center_runs.id"` (Goal → TaskCenterRun) | **unchanged** |
| UniqueConstraint name `uq_episode_request_sequence` | `uq_iteration_goal_sequence` |
| UniqueConstraint name `uq_attempt_segment_sequence` | `uq_trial_iteration_sequence` |
| `task_center_run_id` FK column (in `goals`) | **unchanged** |

### 2.5 Enum *values* (string contents) — rename audit

| Enum class | Old value(s) | New value(s) | Decision |
|---|---|---|---|
| `MissionStatus`/`GoalStatus` | `"open"`, `"succeeded"`, `"failed"`, `"cancelled"` | **unchanged** | Tier-agnostic |
| `EpisodeStatus`/`IterationStatus` | `"open"`, `"succeeded"`, `"failed"`, `"cancelled"` | **unchanged** | Tier-agnostic |
| `EpisodeCreationReason`/`IterationCreationReason` | `"initial"`, `"partial_continuation"` | **unchanged** | Tier-agnostic |
| `AttemptStage`/`TrialStage` | `"plan"`, `"generate"`, `"evaluate"`, `"closed"` | **unchanged** | Pipeline-stage verbs, not tier |
| `AttemptStatus`/`TrialStatus` | `"running"`, `"passed"`, `"failed"` | **unchanged** | Tier-agnostic |
| `AttemptFailReason`/`TrialFailReason` | `"planner_failed"`, `"generator_failed"`, `"evaluator_failed"`, `"startup_failed"` | **unchanged** | Refers to inner-pipeline roles, not "attempt" |
| `TaskCenterTaskRole` | `"planner"`, `"generator"`, `"evaluator"`, `"entry_executor"` | **unchanged** | Role names, not tier |
| `SpawnReason` | `"attempt_planner"`, `"attempt_generator"`, `"attempt_evaluator"`, `"entry_executor"` | `"trial_planner"`, `"trial_generator"`, `"trial_evaluator"`, `"entry_executor"` | These textually embed the renamed tier noun — see §2.5.1 carve-out |

### 2.5.1 Principle-2 carve-out: `SpawnReason` values (deliberate)

> Principle 2 generally preserves enum *value strings*. `SpawnReason`'s values (`"attempt_planner"`, `"attempt_generator"`, `"attempt_evaluator"`) textually embed the tier noun being renamed, so we treat them as part of the renamed *surface*, not as tier-agnostic identifiers. Renaming them to `"trial_planner"` / `"trial_generator"` / `"trial_evaluator"` is consistent with the rename's intent. **This is an observable contract change for any external audit/observability consumer that filters spawn-reason payloads**; see §2.14 and the Phase-10 migration note.

**Confirmed string-literal hit sites (must update in Phase 4 + Phase 9):**
- `backend/src/task_center/task_state.py:17-20` — `SpawnReason` enum definition.
- `backend/src/task_center/attempt/orchestrator.py:101, 263` — emit-site usages of the values.
- `backend/src/task_center/attempt/dispatcher.py:267` — dispatcher tag.
- `backend/src/task_center/entry/coordinator.py:275` — entry-coordinator dispatch.
- `backend/tests/unit_test/test_task_center/test_agent_launch/test_launcher.py:69` — literal-string assertion (must change to `"trial_planner"`).

### 2.6 `ContextBlockKind` string values

| Old enum name (value) | New enum name (value) | Notes |
|---|---|---|
| `MISSION_GOAL` (`"mission_goal"`) | `GOAL_STATEMENT` (`"goal_statement"`) | Renamed to "statement" to distinguish "the Goal tier's text" from the field name `goal` on tiers |
| `EPISODE_GOAL` (`"episode_goal"`) | `ITERATION_STATEMENT` (`"iteration_statement"`) | Parallel rename |
| `PRIOR_EPISODE_SPECIFICATION` (`"prior_episode_specification"`) | `PRIOR_ITERATION_SPECIFICATION` (`"prior_iteration_specification"`) | |
| `PRIOR_EPISODE_SUMMARY` (`"prior_episode_summary"`) | `PRIOR_ITERATION_SUMMARY` (`"prior_iteration_summary"`) | |
| `FAILED_ATTEMPT_LANDSCAPE` (`"failed_attempt_landscape"`) | `FAILED_TRIAL_LANDSCAPE` (`"failed_trial_landscape"`) | |
| `PARTIAL_PLAN_BOUNDARY` (`"partial_plan_boundary"`) | **unchanged** | Tier-agnostic |
| `PLANNED_TASK_SPEC`, `TASK_SPECIFICATION`, `EVALUATION_CRITERIA`, `DEPENDENCY_SUMMARY`, `COMPLETED_TASK_SUMMARY`, `ARTIFACT_REFERENCE`, `ENTRY_REQUEST` | **unchanged** | None embed the tier nouns |

### 2.7 Recipe heading constants (in `recipes/_shared.py`) — Reading A redesign (rewritten, renderer-aligned)

The renderer (verified at `backend/src/task_center/context_engine/renderer.py:106-122`) groups blocks **only** when consecutive blocks share `metadata["group_heading"]`. There is no H1 de-dup and no "blocks without `heading` attach to previous H1" path. Reading A is implemented entirely via the existing `group_heading` mechanism — **no renderer change**.

**Old constants:**
```python
MISSION_EPISODE_HEADING = "# Mission / Current Episode"
MISSION_HEADING = "# Mission"
CURRENT_EPISODE_HEADING = "# Current Episode"
PREVIOUS_EPISODE_RESULTS_HEADING = "# Previous Episode Results"
```

**New constants (Reading A):**
```python
GOAL_ITERATION_HEADING = "# Goal / Current Iteration"   # iteration 1: combined H1 (no group)
GOAL_HEADING = "# Goal"                                  # iteration ≥ 2: group_heading for goal + prior iterations
CURRENT_ITERATION_HEADING = "# Current Iteration"        # iteration ≥ 2: separate H1
# (No "previous results" H1 — the prior-iteration H2 sub-sections live under `# Goal`)
```

**Per-block metadata (Reading A, the contract Phase 5 must produce):**

For `current_iteration.sequence_no >= 2`, the iteration ≥ 2 packet emits the following blocks **in order**, all inside one `group_heading == GOAL_HEADING` group followed by the iteration-statement block in its own (no-group) section:

| # | Block kind | `metadata["group_heading"]` | `metadata["subheading"]` | Body |
|---|---|---|---|---|
| 1 | `GOAL_STATEMENT` (`"goal_statement"`) | `GOAL_HEADING` (`"# Goal"`) | `"Goal"` | `goal.text` |
| 2 | `PRIOR_ITERATION_SPECIFICATION` | `GOAL_HEADING` | `"Iteration 1 accepted plan"` | prior-iteration-1 task_specification |
| 3 | `PRIOR_ITERATION_SUMMARY` | `GOAL_HEADING` | `"Iteration 1 summary"` | prior-iteration-1 task_summary |
| 4 | `PRIOR_ITERATION_SPECIFICATION` | `GOAL_HEADING` | `"Iteration 2 accepted plan"` | prior-iteration-2 task_specification |
| 5 | `PRIOR_ITERATION_SUMMARY` | `GOAL_HEADING` | `"Iteration 2 summary"` | prior-iteration-2 task_summary |
| … | (repeat for each prior iteration) | … | … | … |
| N | `ITERATION_STATEMENT` | *(none)* | *(none — uses default `# Current Iteration` heading via `metadata["heading"] = CURRENT_ITERATION_HEADING`)* | `current_iteration.goal` |

**Renderer output (verified by walking `_render_group` at `renderer.py:132-138`):**
```
# Goal

## Goal

{goal.text}

## Iteration 1 accepted plan

{prior_iteration_1.task_specification}

## Iteration 1 summary

{prior_iteration_1.task_summary}

## Iteration 2 accepted plan

{prior_iteration_2.task_specification}

## Iteration 2 summary

{prior_iteration_2.task_summary}

# Current Iteration

{current_iteration.goal}
```

**Design choice: option (a) — `## Goal` H2 wrapper.** We pick the path that requires zero renderer change. The goal text is rendered under an explicit `## Goal` H2 inside the `# Goal` H1 group. Rationale: (1) zero renderer churn; (2) fully predictable rendering driven only by `group_heading`/`subheading` metadata; (3) a future Reading B can collapse the `## Goal` H2 into the `# Goal` H1 cleanly when the renderer is upgraded. The alternative (extend `_render_group` so the first block in a group with no `subheading` renders inline) is explicitly **not taken** in this PR.

**Iteration-1 structure (unchanged shape, renamed heading):**
The iteration-1 path emits a single `ITERATION_STATEMENT` block with `metadata["heading"] = GOAL_ITERATION_HEADING` (no group). Renderer output:
```
# Goal / Current Iteration

{iteration.goal}
```

### 2.8 Function & symbol renames inside `recipes/_shared.py`

| Old | New |
|---|---|
| `mission_episode_blocks` | `goal_iteration_blocks` |
| `_episode_goal_block` | `_iteration_statement_block` |
| `_mission_goal_block` | `_goal_statement_block` |
| `_previous_episode_result_blocks` | `_prior_iteration_blocks` |
| `MISSION_EPISODE_HEADING` | `GOAL_ITERATION_HEADING` |
| `MISSION_HEADING` | `GOAL_HEADING` |
| `CURRENT_EPISODE_HEADING` | `CURRENT_ITERATION_HEADING` |
| `PREVIOUS_EPISODE_RESULTS_HEADING` | **removed** (Reading A folds prior iterations under `GOAL_HEADING`) |

### 2.9 `ContextScope` factories & `ContextRefs` fields

| Old | New |
|---|---|
| `ContextScope.mission_id` | `ContextScope.goal_id` |
| `ContextScope.episode_id` | `ContextScope.iteration_id` |
| `ContextScope.attempt_id` | `ContextScope.trial_id` |
| `ContextRefs.mission_id` | `ContextRefs.goal_id` |
| `ContextRefs.episode_id` | `ContextRefs.iteration_id` |
| `ContextRefs.attempt_id` | `ContextRefs.trial_id` |
| `for_planner(mission_id, episode_id, attempt_id)` | `for_planner(goal_id, iteration_id, trial_id)` |
| `for_generator(mission_id, episode_id, attempt_id, task_id)` | `for_generator(goal_id, iteration_id, trial_id, task_id)` |
| `for_evaluator(mission_id, episode_id, attempt_id)` | `for_evaluator(goal_id, iteration_id, trial_id)` |
| `for_entry_executor(task_id)` | **unchanged** |
| Recipe `_REQUIRED_FIELDS = frozenset({"mission_id", "episode_id", "attempt_id"})` | `frozenset({"goal_id", "iteration_id", "trial_id"})` |

### 2.10 `task_center/__init__.py` public surface

`_EXPORTS` map rewrites:
| Old key (and target) | New key (and target) |
|---|---|
| `"Mission"` → `("task_center.mission.state", "Mission")` | `"Goal"` → `("task_center.goal.state", "Goal")` |
| `"MissionStatus"` | `"GoalStatus"` |
| `"MissionStarter"` | `"GoalStarter"` |
| `"StartedMission"` | `"StartedGoal"` |
| `"Episode"` → `("task_center.episode.state", "Episode")` | `"Iteration"` → `("task_center.iteration.state", "Iteration")` |
| `"EpisodeStatus"` | `"IterationStatus"` |
| `"EpisodeCreationReason"` | `"IterationCreationReason"` |
| `"Attempt"` → `("task_center.attempt.state", "Attempt")` | `"Trial"` → `("task_center.trial.state", "Trial")` |
| `"AttemptStage"` | `"TrialStage"` |
| `"AttemptStatus"` | `"TrialStatus"` |
| `"AttemptFailReason"` | `"TrialFailReason"` |
| `"AttemptOrchestrator"` | `"TrialOrchestrator"` |
| `"AttemptDeps"` | `"TrialDeps"` |
| `"ordered_generator_tasks"` → `("task_center.attempt.generator_dag", …)` | retarget to `task_center.trial.generator_dag` |
| `"PlannerSubmission"`, `"GeneratorSubmission"`, `"EvaluatorSubmission"`, `"PlannedGeneratorTask"`, `"ContextComposer"`, `"ContextPacket"`, `"ContextScope"`, `"RecipeRegistry"`, `"LaunchBundle"`, `"PredicateRegistry"`, `"AgentDefinitionValidationError"`, `"TaskCenterInvariantViolation"`, `"TaskCenterSandboxBridge"`, `"EntryTaskController"`, `"start_task_center_entry_run"` | **unchanged** |

### 2.11 Agent-prompt prose mapping + `TaskCenterInvariantViolation` message strings

#### 2.11.a Agent-prompt prose (LLM-facing)

These are the *exact* prose substitutions in `backend/src/agents/profile/main/*.md`. The Executor applies them as case-sensitive, whole-noun replacements only; **lowercase verb usages are exempt** (Principle 5).

| Old prose noun | New prose noun |
|---|---|
| `Mission` (capital M, noun) | `Goal` |
| `Episode` (capital E, noun) | `Iteration` |
| `Attempt` (capital A, noun) — when referring to the tier (e.g. "one Attempt", "this attempt") | `Trial` (only when capitalized noun-form; **prose "attempt" verb stays**) |
| `Current Episode` | `Current Iteration` |
| `Previous Episode Results` | (deleted — Reading A subsumes into `# Goal`) |
| `Mission / Current Episode` | `Goal / Current Iteration` |
| `Failed Attempts` | `Failed Trials` |
| `Attempt Plan` | `Trial Plan` |
| `episode chain` / `episode lifecycle` (lowercase, refers to the tier) | `iteration chain` / `iteration lifecycle` |
| `mission's ancestry` | `goal's ancestry` |
| `nested mission depth` | `nested goal depth` (only when referring to the tier; predicate names — §2.13 — are a separate decision) |

**Files where these apply (confirmed by grep):**
- `agents/profile/main/planner.md`
- `agents/profile/main/planner_full_only.md`
- `agents/profile/main/evaluator.md`
- `agents/profile/main/generator_verifier.md`
- `agents/profile/main/executor.md`
- `agents/profile/main/executor_success_handoff.md`
- `agents/profile/main/executor_success_failure.md`
- `agents/profile/main/entry_executor.md`

#### 2.11.b `TaskCenterInvariantViolation` message strings (in `task_center/_core/infra.py`)

> Note: `_core/infra.py:15-23` imports `Mission`/`Episode`/`Attempt`-related types from `task_center.{mission,episode,attempt}.state`; these imports flip to `task_center.{goal,iteration,trial}.state` as part of the Phase-4 directory renames. Listed here so the Executor's grep over `_core/infra.py` covers both message-string and import-line renames in a single pass.

The `infra.py` invariant-assertion helpers embed tier nouns directly in their error messages. Each must be rewritten so the message text matches the renamed type. Exact line-level enumeration (verified against `backend/src/task_center/_core/infra.py`):

| File:line | Old message substring | New message substring |
|---|---|---|
| `_core/infra.py:153-157` (`assert_mission_open`) | `f"Mission {mission.id!r} is not open (status={mission.status})"` | `f"Goal {goal.id!r} is not open (status={goal.status})"` |
| `_core/infra.py:160-164` (`assert_episode_id_unique_in_mission`) | `f"Episode {episode_id!r} already present in Mission {mission.id!r} episode list"` | `f"Iteration {iteration_id!r} already present in Goal {goal.id!r} iteration list"` |
| `_core/infra.py:167-172` (`assert_episode_sequence_contiguous`) | `f"Episode sequence_no must be contiguous: expected {expected}, got {new_sequence_no}"` | `f"Iteration sequence_no must be contiguous: expected {expected}, got {new_sequence_no}"` |
| `_core/infra.py:175-185` (`assert_continuation_episode_predecessor`) | `f"Continuation requires predecessor episode {previous.id!r} to be SUCCEEDED, not {previous.status}"` / `f"Continuation requires predecessor episode {previous.id!r} to have a deferred_goal; none was recorded"` | `f"Continuation requires predecessor iteration {previous.id!r} to be SUCCEEDED, not {previous.status}"` / `f"Continuation requires predecessor iteration {previous.id!r} to have a deferred_goal; none was recorded"` |
| `_core/infra.py:188-192` (`assert_episode_open`) | `f"Episode {episode.id!r} is not open (status={episode.status})"` | `f"Iteration {iteration.id!r} is not open (status={iteration.status})"` |
| `_core/infra.py:195-200` (`assert_episode_has_budget`) | `f"Episode {episode.id!r} attempt budget exhausted ({episode.attempt_count}/{episode.attempt_budget})"` | `f"Iteration {iteration.id!r} trial budget exhausted ({iteration.trial_count}/{iteration.trial_budget})"` |
| `_core/infra.py:203-208` (`assert_attempt_belongs_to_episode`) | `f"Attempt {attempt.id!r} (episode {attempt.episode_id!r}) does not belong to Episode {episode.id!r}"` | `f"Trial {trial.id!r} (iteration {trial.iteration_id!r}) does not belong to Iteration {iteration.id!r}"` |
| `_core/infra.py:211-217` (`assert_attempt_sequence_contiguous`) | `f"Attempt attempt_sequence_no must be contiguous: expected {expected}, got {new_sequence_no}"` | `f"Trial trial_sequence_no must be contiguous: expected {expected}, got {new_sequence_no}"` |
| `_core/infra.py:220-224` (`assert_fail_reason_present_on_failure`) | `f"Attempt {attempt.id!r} closed FAILED with no fail_reason"` | `f"Trial {trial.id!r} closed FAILED with no fail_reason"` |
| `_core/infra.py:227-232` (`assert_attempt_stage`) | `f"Attempt {attempt.id!r} expected stage {expected.value!r}, got {attempt.stage.value!r}"` | `f"Trial {trial.id!r} expected stage {expected.value!r}, got {trial.stage.value!r}"` |
| `_core/infra.py:235-237` (`assert_attempt_not_closed`) | `f"Attempt {attempt.id!r} is already closed"` | `f"Trial {trial.id!r} is already closed"` |
| `_core/infra.py:240-248` (`assert_valid_attempt_close`) | `"Failed attempt close requires fail_reason"` / `"Passed attempt close cannot have fail_reason"` / `"Cannot close attempt with running status"` | `"Failed trial close requires fail_reason"` / `"Passed trial close cannot have fail_reason"` / `"Cannot close trial with running status"` |
| `_core/infra.py:251-255` (`assert_task_belongs_to_attempt`) | `f"Task {task.get('id')!r} does not belong to Attempt {attempt.id!r}"` | `f"Task {task.get('id')!r} does not belong to Trial {trial.id!r}"` |
| `_core/infra.py:258-261` (`assert_generator_task_for_submission`) | (string `"is not a generator task"` body unchanged; function signature/parameter renames to `trial`) | unchanged body; signature renamed |
| `_core/infra.py:264-267` (`assert_evaluator_task_for_submission`) | (string `"is not an evaluator task"` body unchanged; function signature renames to `trial`) | unchanged body; signature renamed |

**Function-name renames in the same file** (the `assert_*` helpers and `__all__` entries):
- `assert_mission_open` → `assert_goal_open`
- `assert_episode_id_unique_in_mission` → `assert_iteration_id_unique_in_goal`
- `assert_episode_sequence_contiguous` → `assert_iteration_sequence_contiguous`
- `assert_continuation_episode_predecessor` → `assert_continuation_iteration_predecessor`
- `assert_episode_open` → `assert_iteration_open`
- `assert_episode_has_budget` → `assert_iteration_has_budget`
- `assert_attempt_belongs_to_episode` → `assert_trial_belongs_to_iteration`
- `assert_attempt_sequence_contiguous` → `assert_trial_sequence_contiguous`
- `assert_attempt_stage` → `assert_trial_stage`
- `assert_attempt_not_closed` → `assert_trial_not_closed`
- `assert_valid_attempt_close` → `assert_valid_trial_close`
- `assert_task_belongs_to_attempt` → `assert_task_belongs_to_trial`

### 2.12 Tool-layer naming

| File | Mapping |
|---|---|
| `tools/submission/planner/submit_full_plan.py` | **filename unchanged**; rename internal symbol refs (`Attempt` → `Trial`, etc.) and prose docstrings noun-only |
| `tools/submission/planner/submit_partial_plan.py` | same |
| `tools/submission/planner/_schemas.py` | same |
| `tools/submission/planner/__init__.py` | same |
| `tools/submission/context/attempt.py` | **rename to `tools/submission/context/trial.py`** |
| `tools/submission/context/executor.py` | rename internal symbols only; filename unchanged |
| `tools/submission/_factory.py` | symbol renames only |
| `tools/submission/evaluator/*.py` | symbol renames only (filename `submit_evaluation_success.py`, `submit_evaluation_failure.py` unchanged) |
| `tools/submission/{advisor,verifier,resolver,explorer,executor,notification_triggers,helper}/*.py` (if they reference renamed symbols/strings) | symbol renames only |
| `tools/submission/resolver_history.py` | symbol renames only |

### 2.13 Predicate / soft-reminder names (`task_center/_core/agent_routing.py`)

Predicates whose names embed the tier-noun (verified hits): `nested_mission_depth_gt_1`, `nested_mission_depth_above_handoff_range`, `request_mission_after_edit`.

| Old predicate | New predicate |
|---|---|
| `nested_mission_depth_gt_1` | `nested_goal_depth_gt_1` |
| `nested_mission_depth_above_handoff_range` | `nested_goal_depth_above_handoff_range` |
| `request_mission_after_edit` | `request_goal_after_edit` |

These appear in **both** locations: agent-prompt front-matter `when:` clauses (in `planner.md`, `executor.md`, `executor_success_handoff.md`, `entry_executor.md`) **and** Python source under `task_center/_core/` (registry registration + any internal callers). Phase 7 covers the front-matter; Phase 4/3 covers the Python; **acceptance criterion §4.5 #3 verifies both surfaces in one grep**.

### 2.14 Audit / event payload field names

In `task_center_runner/audit/recorder.py`, `audit/legacy.py`, `audit/node_id.py`, and `hooks/builtins.py`:

| Old JSON key | New JSON key |
|---|---|
| `"mission_id"` | `"goal_id"` |
| `"episode_id"` | `"iteration_id"` |
| `"attempt_id"` | `"trial_id"` |
| `"attempt_sequence_no"` | `"trial_sequence_no"` |
| `"episode_ids"` | `"iteration_ids"` |
| `"attempt_ids"` | `"trial_ids"` |
| `SpawnReason` values (in `spawn_reason` payload field) — `"attempt_planner"`/`"attempt_generator"`/`"attempt_evaluator"` | `"trial_planner"`/`"trial_generator"`/`"trial_evaluator"` (§2.5.1) |

The test `backend/tests/unit_test/test_task_center/test_audit/test_emission_shape.py` asserts these keys; update in lockstep.

### 2.15 Test-file string-fixture updates (enumerated)

Each test file below contains at least one string literal asserting old terminology. Executor must update the string fixtures; full path enumeration:

- `backend/tests/unit_test/test_task_center/test_context_engine/test_recipes_planner.py` — heading strings, block-kind values, `metadata["group_heading"]`/`metadata["subheading"]` patterns; **new structural acceptance test added (see §4.5 #7 and Phase 9)**.
- `backend/tests/unit_test/test_task_center/test_context_engine/test_recipes_other.py` — heading strings for evaluator/generator.
- `backend/tests/unit_test/test_task_center/test_context_engine/test_attempt_landscape.py` → file renamed; `FAILED_ATTEMPT_LANDSCAPE` → `FAILED_TRIAL_LANDSCAPE`.
- `backend/tests/unit_test/test_task_center/test_context_engine/test_helper_recipes.py` — predicate name strings.
- `backend/tests/unit_test/test_task_center/test_context_engine/test_engine.py` — scope kwargs.
- `backend/tests/unit_test/test_task_center/test_context_engine/test_renderer.py` — heading layout snapshots.
- `backend/tests/unit_test/test_task_center/test_context_engine/test_packet.py` — block-kind enum values.
- `backend/tests/unit_test/test_task_center/test_context_engine/test_scope.py` — scope field names.
- `backend/tests/unit_test/test_task_center/test_context_engine/test_token_budget.py` — block-kind values.
- `backend/tests/unit_test/test_task_center/test_domain/test_{mission,episode,attempt}_*.py` — all renamed (§2.3) and DTO fields updated.
- `backend/tests/unit_test/test_task_center/test_domain/test_ancestry.py` — predicate names + tier nouns.
- `backend/tests/unit_test/test_task_center/test_persistence/test_{mission,episode,attempt}_store.py` — renamed (§2.3) and table/column refs.
- `backend/tests/unit_test/test_task_center/test_persistence/test_close_succeeded.py` — closure report field names + JSON keys.
- `backend/tests/unit_test/test_task_center/test_persistence/test_task_center_task_helpers.py` — spawn-reason values (§2.5.1 carve-out).
- `backend/tests/unit_test/test_task_center/test_audit/test_emission_shape.py` — audit JSON keys (§2.14).
- `backend/tests/unit_test/test_task_center/test_audit/test_emitter.py` — same.
- `backend/tests/unit_test/test_task_center/test_agent_launch/test_{composer,resolver}.py` — scope kwargs + symbol refs.
- `backend/tests/unit_test/test_task_center/test_agent_launch/test_launcher.py` — **line 69 specifically**: `SpawnReason` literal-string assertion must change to `"trial_planner"` (§2.5.1).
- `backend/tests/unit_test/test_task_center/test_lifecycle/*.py` — symbol refs + closure outcome variant rename + invariant-violation message substrings (§2.11.b).
- `backend/tests/unit_test/test_task_center/conftest.py` — fixture factories renamed (e.g. `make_mission` → `make_goal`).
- `backend/tests/unit_test/test_tools/test_submission_planner_tools.py` — symbol refs in payload assertions.
- `backend/tests/unit_test/test_tools/submission_test_utils.py` — fixture builders.
- `backend/tests/unit_test/test_tools/conftest.py` — same.

### 2.16 Non-changes (explicit "do not touch" list)

To prevent the Executor from over-renaming:

- File names: `submit_full_plan.py`, `submit_partial_plan.py`, `submit_evaluation_success.py`, `submit_evaluation_failure.py` — verb actions, not tier nouns.
- Symbol `planner_task_id`, `evaluator_task_id`, `generator_task_ids` (DB columns, DTO fields) — refer to inner-pipeline role IDs, not the renamed tier.
- Class/module `TaskCenterRunRecord`, `TaskCenterTaskRole`, `TaskCenterSandboxBridge`, `TaskCenterInvariantViolation`, `task_center_run_id`, `start_task_center_entry_run`, the package name `task_center` itself — these reference the harness wrapper, not the renamed tier.
- `EvaluatorSubmission`, `GeneratorSubmission`, `PlannerSubmission`, `PlannerFailureSubmission`, `PlannedGeneratorTask` — pipeline-role DTOs, not tier DTOs.
- `task_state.py` filename — the file itself is not tier-specific.
- All `goal` field names on `Mission`/`Goal` and `Episode`/`Iteration` (these *are* the goal-text fields; not tier-renames).
- All `submit_execution_handoff`, `submit_execution_failure`, `submit_execution_success` tool names and prose references.
- `db/models/task_center.py` and `db/stores/task_center_store.py` — represent the run-level wrapper; out of scope.
- `db/models/agent_run.py`, `db/models/context_packet.py`, `db/stores/agent_run_store.py`, `db/stores/context_packet_store.py` — orthogonal to the tier hierarchy.
- Lowercase prose verb "attempt" ("attempt to", "first attempt at", "the attempt at") in any file. The Executor's rename script must be case-sensitive on initial-capital noun form `Attempt` only.

---

## 3. Implementation Phases (ordered, each = one verifiable checkpoint)

Each phase produces one commit. ~10 commits total.

### Phase 1 — DB models + table renames (~150 LoC, 3 files)
**Files:** `db/models/mission.py` → `goal.py`, `db/models/episode.py` → `iteration.py`, `db/models/attempt.py` → `trial.py`; update `db/models/__init__.py`, `db/models/model_registration.py`, `db/base.py` imports.

**Changes:** Class renames per §2.1, `__tablename__` per §2.4, column renames per §2.4, FK target strings per §2.4, UniqueConstraint name strings per §2.4.

**Verify:**
- `python -c "from db.models.goal import GoalRecord; from db.models.iteration import IterationRecord; from db.models.trial import TrialRecord; print('ok')"` returns `ok`.
- `rg -n "MissionRecord|EpisodeRecord|AttemptRecord" backend/src/db/models/` returns empty.
- `rg -n '"missions"|"episodes"|"attempts"' backend/src/db/models/` returns empty.

### Phase 2 — DB stores + helpers (~150 LoC, 4 files)
**Files:** `db/stores/mission_store.py` → `goal_store.py`, `db/stores/episode_store.py` → `iteration_store.py`, `db/stores/attempt_store.py` → `trial_store.py`, `db/stores/__init__.py`, `db/stores/base.py` (if it references renamed types), `db/stores/task_center_store.py` (only if it imports the renamed model classes).

**Changes:** Class renames (`MissionStore` → `GoalStore`, etc.), method param renames (`mission_id` → `goal_id`, etc.), `_to_dto` factory renames, and the DTO union now points to renamed `task_center.{goal,iteration,trial}.state`.

**Verify:**
- `rg -n "MissionStore|EpisodeStore|AttemptStore|mission_store|episode_store|attempt_store" backend/src/db/` returns empty.
- `pytest backend/tests/unit_test/test_task_center/test_persistence/ -x` is green (after Phase 9; mark as deferred verification here).

### Phase 3 — `_core` types & persistence protocol (~110 LoC, 4 files)
**Files:** `task_center/_core/types.py`, `task_center/_core/persistence.py`, `task_center/_core/infra.py`, `task_center/_core/agent_routing.py`.

**Changes:**
- Update method signatures or assertion-helper signatures referencing `mission_id`/`episode_id`/`attempt_id`.
- Rename predicates per §2.13.
- **Rewrite every `TaskCenterInvariantViolation` message string and assertion function name per the line-level table in §2.11.b.**
- **Do not** rename `task_center_run_id`, `planner_task_id`, `TaskCenterTaskRole` (§2.16).

**Verify:**
- `rg -n "mission_id|episode_id|attempt_id" backend/src/task_center/_core/` returns empty (or only documented exceptions).
- `rg -n 'Mission|Episode|Attempt' backend/src/task_center/_core/infra.py` returns empty (every match in `infra.py` is a tier-noun; none are verb usages in this file).
- `rg -n 'nested_mission_depth|request_mission_after' backend/src/` returns empty.

### Phase 4 — Domain modules: goal/iteration/trial dirs (~430 LoC + dir renames, ~12 files)
**Files (directory renames):** `task_center/mission/` → `task_center/goal/`, `task_center/episode/` → `task_center/iteration/`, `task_center/attempt/` → `task_center/trial/`. Inside each, rename `state.py` symbols per §2.1–§2.2.

Update internal imports inside the renamed modules (e.g. `task_center/iteration/state.py` imports from `task_center.trial.state`; `task_center/iteration/manager.py` imports the renamed `IterationStore`, `TrialStore`, `IterationClosureRouter`, `TrialOrchestrator`). Update `_EpisodeClosureRouter._close_iteration_*` method names. Update `EpisodeManager._close_episode_passed` → `IterationManager._close_iteration_passed`. Rename `AttemptedPlanEntry` → `PriorTrialEntry`. Rename `AttemptPlanFailed` closure variant → `TrialPlanFailed`.

**SpawnReason value rename (§2.5.1):** Update the enum definition in `task_center/task_state.py:17-20` and all emit-site usages in `task_center/attempt/orchestrator.py:101,263`, `task_center/attempt/dispatcher.py:267`, `task_center/entry/coordinator.py:275`.

**Verify:**
- `rg -nw 'Mission|Episode|Attempt' backend/src/task_center/goal/ backend/src/task_center/iteration/ backend/src/task_center/trial/` returns empty for capitalized noun form.
- `python -c "from task_center.goal.state import Goal, GoalStatus; from task_center.iteration.state import Iteration, IterationStatus, IterationCreationReason, TrialPlanFailed; from task_center.trial.state import Trial, TrialStage, TrialStatus, TrialFailReason; print('ok')"` returns `ok`.
- `python -c "from task_center.task_state import SpawnReason; assert SpawnReason.TRIAL_PLANNER.value == 'trial_planner'; print('ok')"` returns `ok`.

### Phase 5 — Context engine: scope, packet, recipes, Reading-A reframing (~250 LoC, 8 files; renderer untouched)
**Files:**
- `task_center/context_engine/scope.py` — `ContextScope` field + factory renames (§2.9).
- `task_center/context_engine/packet.py` — `ContextRefs` field renames + `ContextBlockKind` value renames (§2.6, §2.9).
- `task_center/context_engine/recipes/_shared.py` — heading constants (§2.7), helper function names (§2.8), **Reading-A redesign per the §2.7 metadata table**.
- `task_center/context_engine/recipes/planner.py` — update `_REQUIRED_FIELDS`, scope field names, import of `goal_iteration_blocks`.
- `task_center/context_engine/recipes/generator.py`, `recipes/evaluator.py` — same scope/import updates.
- `task_center/context_engine/recipes/attempt_landscape.py` → renamed to `trial_landscape.py`; update `FAILED_ATTEMPT_LANDSCAPE` → `FAILED_TRIAL_LANDSCAPE`; rename function `failed_attempt_landscape_blocks` → `failed_trial_landscape_blocks`; rename `attempt_sequence_no` references; update heading metadata strings (`"Failed Attempts"` → `"Failed Trials"`).
- `task_center/context_engine/recipes_registry.py` — update import paths.
- `task_center/context_engine/core.py` — update `mission_store`/`episode_store`/`attempt_store` field names on `ContextEngineDeps` → `goal_store`/`iteration_store`/`trial_store`.
- `task_center/context_engine/renderer.py` — **untouched.** Verified at `renderer.py:106-122` and `renderer.py:132-138` that the existing `group_heading` + `subheading` mechanism is sufficient for Reading A.

**Reading-A implementation (executor-actionable, drives §2.7 contract):**
- In `goal_iteration_blocks` (new name), for `current_iteration.sequence_no >= 2`:
  1. Emit one `_goal_statement_block` with `metadata={"group_heading": GOAL_HEADING, "subheading": "Goal"}`.
  2. For each prior iteration in order, emit two blocks: a `PRIOR_ITERATION_SPECIFICATION` block with `metadata={"group_heading": GOAL_HEADING, "subheading": f"Iteration {n} accepted plan"}`, and a `PRIOR_ITERATION_SUMMARY` block with `metadata={"group_heading": GOAL_HEADING, "subheading": f"Iteration {n} summary"}`.
  3. Emit one `_iteration_statement_block` with `metadata={"heading": CURRENT_ITERATION_HEADING}` (no `group_heading`, so the renderer emits it as a separate top-level section).
- For `current_iteration.sequence_no == 1`: emit a single `_iteration_statement_block` with `metadata={"heading": GOAL_ITERATION_HEADING}` (no group).

**Verify:**
- `rg -n "mission_id|episode_id|attempt_id" backend/src/task_center/context_engine/` returns empty.
- `rg -n "MISSION_GOAL|EPISODE_GOAL|FAILED_ATTEMPT_LANDSCAPE|PRIOR_EPISODE" backend/src/task_center/context_engine/` returns empty.
- `python -c "from task_center.context_engine.recipes._shared import goal_iteration_blocks, GOAL_HEADING, CURRENT_ITERATION_HEADING, GOAL_ITERATION_HEADING; print('ok')"` returns `ok`.
- `git diff backend/src/task_center/context_engine/renderer.py` is empty (renderer untouched).

### Phase 6 — Tools layer (~180 LoC, ~15 files)
**Files (per §2.12):** `tools/submission/planner/{__init__,_schemas,submit_full_plan,submit_partial_plan}.py`, `tools/submission/context/attempt.py` → `trial.py`, `tools/submission/context/executor.py`, `tools/submission/_factory.py`, `tools/submission/evaluator/*.py`, plus any helpers in `tools/submission/{advisor,verifier,resolver,explorer,executor,notification_triggers}/*.py` that import renamed symbols.

**Changes:** Import path updates, internal symbol renames (`AttemptOrchestrator` → `TrialOrchestrator`, etc.), prose docstrings (capitalized noun-form only). **Do not** rename tool function names (`submit_full_plan`, `submit_partial_plan`, etc. — §2.16).

**Verify:**
- `rg -n "from task_center\.(mission|episode|attempt)" backend/src/tools/` returns empty.
- `rg -n "from db\.(models|stores)\.(mission|episode|attempt)" backend/src/tools/` returns empty.
- `pytest backend/tests/unit_test/test_tools/test_submission_planner_tools.py -x` is green (deferred to Phase 9 if needed).

### Phase 7 — Agent prompts (LLM-facing, 8 files coordinated edit)
**Files:** `backend/src/agents/profile/main/{planner,planner_full_only,evaluator,generator_verifier,executor,executor_success_handoff,executor_success_failure,entry_executor}.md`.

**Changes:** Apply the prose noun substitutions from §2.11.a *case-sensitively* (initial-capital only). Update the planner.md `when:` predicate names per §2.13. Update the description in the front-matter of `planner_full_only.md` (line 3: "TaskCenter attempts" — rename to "TaskCenter trials" because here it's used as the *noun* for the tier instance, not a verb).

**Update planner.md (iteration-1 + iteration-2+ heading descriptions) to:**
```
- `Goal / Current Iteration` appears for iteration 1, where both are the same goal.
- `Goal` appears for continuation iterations, containing the goal text (under `## Goal`) and per-prior-iteration sub-sections (`## Iteration N accepted plan` and `## Iteration N summary`).
- `Current Iteration` appears as a separate top-level section for continuation iterations.
- `Failed Trials` lists prior failed trials inside the current iteration.
```
(Apply equivalent rewrite to `planner_full_only.md`.)

**Update `executor*.md`** for `Attempt Plan` → `Trial Plan`.
**Update `evaluator.md`** for `Mission`, `Previous Episode Results`, `Current Episode`, `Attempt Plan`.
**Update `generator_verifier.md`** for `Attempt Plan`.
**Update `entry_executor.md`** for `mission/episode/attempt tree` → `goal/iteration/trial tree`, "delegate the work as a mission" → "delegate the work as a goal", front-matter predicate names.
**Update `executor_success_handoff.md`** front-matter predicate `request_mission_after_edit` → `request_goal_after_edit`.

**Verify:**
- `rg -nE '\bMission\b|\bEpisode\b|\bAttempt\b' backend/src/agents/profile/main/` returns only matches that are inside a code-block-fenced tool name (none should remain that are capitalized tier nouns).
- `rg -n "nested_mission_depth|request_mission_after" backend/src/agents/profile/` returns empty.
- Cross-check (combined with Phase 3/4): `rg -n 'nested_mission_depth|request_mission_after' backend/src/ backend/tests/ backend/src/agents/` returns empty.

### Phase 8 — task_center_runner consumers (~250 LoC, ~20 files)
**Files:** `task_center_runner/scenarios/pipeline/*.py` (rename per §2.3), `task_center_runner/scenarios/_utils/mission_helpers.py` → `goal_helpers.py`, `task_center_runner/scenarios/sandbox/*.py`, `task_center_runner/scenarios/planner_validation/*.py`, `task_center_runner/scenarios/base.py`, `task_center_runner/scenarios/full_case_user_input.py`, `task_center_runner/scenarios/full_stack_adversarial.py`, `task_center_runner/agent/mock/runner.py`, `task_center_runner/agent/mock/prompt_inspector.py`, `task_center_runner/audit/recorder.py`, `task_center_runner/audit/legacy.py`, `task_center_runner/audit/node_id.py`, `task_center_runner/hooks/builtins.py`, `task_center_runner/hooks/registry.py`, `task_center_runner/tests/test_runner_imports.py`, `task_center_runner/tests/test_capacity_scenario_packs.py`, `task_center_runner/tests/sweevo/test_full_case_user_input.py`.

**Changes:** Symbol renames, scenario filename renames (§2.3), audit JSON keys (§2.14), `SpawnReason` value renames (§2.5.1) in `spawn_reason` payload field, helper function renames (`make_mission_*` → `make_goal_*` in `goal_helpers.py`), `prompt_inspector.py` heading-detection regexes updated to new headings (must match `# Goal`, `# Goal / Current Iteration`, `# Current Iteration`, `## Iteration N accepted plan`, `## Iteration N summary`).

**Verify:**
- `rg -n "mission_id|episode_id|attempt_id" backend/src/task_center_runner/` returns empty (or only documented audit-legacy back-compat strings if any).
- `python -m task_center_runner.scenarios.pipeline.iterative_continuation` smoke command reports "scenario loaded" without ImportError.

### Phase 9 — Tests (~30 files string-and-symbol churn)
**Files:** Per §2.15 enumeration.

**Changes:** File renames per §2.3; symbol-import updates throughout; string-fixture updates for headings; `ContextBlockKind` value updates; audit JSON key updates; closure variant rename (`AttemptPlanFailed` → `TrialPlanFailed`); fixture factory renames (`make_mission` → `make_goal`); invariant-violation message-substring updates (§2.11.b); `SpawnReason` literal-string update at `test_launcher.py:69`.

**New Reading-A acceptance test (in `test_recipes_planner.py::test_iteration_2_plus_reading_a_structure`)** — **structural assertions, not a full-text snapshot**:

```python
def test_iteration_2_plus_reading_a_structure(...):
    # Build fixture with 2 prior iterations and a current iteration (seq_no = 3).
    packet = composer.compose(scope, recipe=planner_recipe)

    # 1. Block-kind order (the structural lock; survives Reading B):
    tier_kinds = {
        "goal_statement",
        "prior_iteration_specification",
        "prior_iteration_summary",
        "iteration_statement",
    }
    assert [b.kind for b in packet.blocks if b.kind in tier_kinds] == [
        "goal_statement",
        "prior_iteration_specification",
        "prior_iteration_summary",
        "prior_iteration_specification",
        "prior_iteration_summary",
        "iteration_statement",
    ]

    # 2. Renderer output structure (heading-line presence, not full body text):
    rendered = renderer.render(packet)
    assert rendered.count("# Goal\n") == 1
    assert rendered.count("# Current Iteration\n") == 1
    assert "## Iteration 1 accepted plan" in rendered
    assert "## Iteration 1 summary" in rendered
    assert "## Iteration 2 accepted plan" in rendered
    assert "## Iteration 2 summary" in rendered

    # 3. group_heading metadata contract (the §2.7 invariant):
    goal_group = [b for b in packet.blocks if b.metadata.get("group_heading") == "# Goal"]
    assert len(goal_group) == 5  # 1 goal_statement + 2 × (spec + summary)
```

This survives Reading B without rewrite because it asserts *kinds and structural heading presence*, not the exact prompt body text.

**Verify:**
- `pytest backend/tests/unit_test/test_task_center -x --no-header -q` is green.
- `pytest backend/tests/unit_test/test_tools -x --no-header -q` is green.

### Phase 10 — DB-safety gate + migration notes (~60 LoC + docs)
**Files:**
- `backend/src/db/engine.py` — add `init_db_with_legacy_check(engine)` (or wrap existing init).
- `backend/scripts/drop_legacy_tier_tables.py` — new one-shot drop script (idempotent).
- `backend/CHANGELOG.md` (or `backend/docs/migrations/2026-05-15-goal-iteration-trial-rename.md`) — append migration notes.

**Changes:**
- Add a startup check in `db/engine.py` that scans for legacy tables `missions`, `episodes`, `attempts` after rename and raises a clear error pointing to the drop script. The gate is a **pure precondition check** — it does not call `create_all` itself, so it composes cleanly with the existing migration chain in `initialize_db()`. Pseudocode:
  ```python
  def init_db_with_legacy_check(engine):
      from sqlalchemy import inspect
      legacy = {"missions", "episodes", "attempts"}
      present = legacy & set(inspect(engine).get_table_names())
      if present:
          raise RuntimeError(
              f"Legacy tier tables {sorted(present)} present after rename. "
              "Run: python -m backend.scripts.drop_legacy_tier_tables"
          )

  # In initialize_db, BEFORE create_all:
  init_db_with_legacy_check(_engine)
  Base.metadata.create_all(_engine)
  _rename_columns(...)
  _add_missing_columns(...)
  _drop_legacy_tables(...)
  ```
- Insert `init_db_with_legacy_check(_engine)` call **before** the existing `Base.metadata.create_all(_engine)` on `db/engine.py:287`; do not remove or alter the subsequent `_rename_columns` / `_add_missing_columns` / `_drop_legacy_tables` calls (lines 290-296), which handle other ongoing migration concerns. The gate's responsibility is *only* to detect orphaned legacy tier tables (`missions`, `episodes`, `attempts`) post-rename; it must not duplicate or short-circuit the existing migration helpers.
- Ship `backend/scripts/drop_legacy_tier_tables.py` as a one-shot that takes a `--db-url` arg, opens an engine, drops `attempts`, `episodes`, `missions` in dependency order (children first), and prints `dropped: [...]`.
- Document in the changelog:
  - Atomic rename of tables `missions`/`episodes`/`attempts` → `goals`/`iterations`/`trials`.
  - No alembic migration; SQLAlchemy `create_all` will produce the new schema only.
  - **Dev-action required:** Run `python -m backend.scripts.drop_legacy_tier_tables --db-url <url>` before next start, or the new startup gate raises.
  - Note the audit-event JSON key changes (§2.14) and the `SpawnReason` value carve-out (§2.5.1) for any external dashboards.

**Verify:**
- Doc file exists; CI lints pass.
- `python -c "from db.engine import init_db_with_legacy_check; import sqlalchemy; eng = sqlalchemy.create_engine('sqlite:///:memory:'); with eng.connect() as c: c.execute(sqlalchemy.text('CREATE TABLE missions (id text)')); c.commit(); init_db_with_legacy_check(eng)"` raises `RuntimeError` mentioning `drop_legacy_tier_tables`. (Acceptance criterion §4.5 #11.)

---

## 4. Test Plan (deliberate-mode expanded)

### 4.1 Unit
- All DTO tests in `test_domain/` updated and green: `test_goal_dto.py`, `test_iteration_dto.py`, `test_iteration_closure_report.py`, `test_iteration_facade_imports.py`, `test_trial_dto.py`, `test_ancestry.py`.
- All store tests in `test_persistence/`: `test_goal_store.py`, `test_iteration_store.py`, `test_trial_store.py`, `test_close_succeeded.py`, `test_task_center_task_helpers.py`.
- Lifecycle tests in `test_lifecycle/`: closure-variant rename + invariant-violation message substrings (§2.11.b) + orchestrator class refs.
- Recipe tests: `test_recipes_planner.py` (incl. new Reading-A **structural** acceptance test), `test_recipes_other.py`, `test_trial_landscape.py` (renamed), `test_helper_recipes.py`.
- Engine/scope/packet/renderer tests: `test_engine.py`, `test_scope.py`, `test_packet.py`, `test_renderer.py`, `test_token_budget.py`.
- Tool tests: `test_submission_planner_tools.py`, `test_submission_helper_tools.py`, `test_submission_terminal_routing.py`, `test_submission_soft_reminders.py`, `test_submission_tool_registration.py`, `test_tool_execution.py`, `test_tool_trace.py`.

### 4.2 Integration
- `backend/tests/unit_test/test_task_center/test_lifecycle/` exercises goal→iteration→trial flow and verifies the closure router path routes `TrialPlanFailed` correctly.
- `test_emission_shape.py` and `test_emitter.py` verify audit payload key changes (§2.14) and `SpawnReason` value renames (§2.5.1).
- `test_agent_launch/test_{composer,resolver,launcher}.py` verify scope kwargs, the predicate registry (§2.13), and the `SpawnReason` literal at `test_launcher.py:69`.

### 4.3 E2E / runner scenarios
- `backend/src/task_center_runner/scenarios/pipeline/iterative_continuation.py` (renamed from `episodic_continuation.py`) loads + runs to completion under the runner harness. This scenario exercises partial-trial → continuation iteration and is the natural smoke test for the Reading-A reframing.
- `task_center_runner/tests/test_runner_imports.py` confirms all renamed scenarios import successfully.
- `task_center_runner/tests/test_capacity_scenario_packs.py` confirms scenario manifests still resolve.

### 4.4 Observability
- `test_emission_shape.py` updated audit payload keys (§2.14) and `spawn_reason` values (§2.5.1).
- `task_center_runner/audit/recorder.py` emits new JSON keys; manual inspect one recorded trace file (or run a scenario and `jq '.[] | keys' trace.jsonl`) to confirm no `mission_id`/`episode_id`/`attempt_id` keys leak and no `"attempt_planner"`/`"attempt_generator"`/`"attempt_evaluator"` spawn-reason values leak.

### 4.5 Acceptance criteria (testable, copy-pasteable)

These are the literal commands the Critic will run.

1. **No leftover capitalized tier nouns (case-sensitive, whole-word)** in production source. Two independent checks (no brittle verb blacklist):
   - **1a.** Tier nouns with no English-verb collision:
     ```
     rg -nw 'Mission|Episode' backend/src/task_center backend/src/db backend/src/tools/submission backend/src/agents/profile/main backend/src/task_center_runner --type-add 'src:*.{py,md}' -t src
     ```
     Expected: empty.
   - **1b.** Positive existence check for renamed `Attempt`-derived *type/symbol* nouns (whole-word, capital-A, type-noun suffix only; allows docstring/verb prose):
     ```
     rg -n '\bAttempt(Status|Stage|FailReason|Orchestrator|Record|Deps|Plan|edPlanEntry)\b' backend/src/
     ```
     Expected: empty.

2. **No leftover renamed enum-value string literals** in production source (test files allowed):
   ```
   rg -n '"mission_goal"|"episode_goal"|"prior_episode_specification"|"prior_episode_summary"|"failed_attempt_landscape"|"attempt_planner"|"attempt_generator"|"attempt_evaluator"' backend/src/
   ```
   Expected: empty.

3. **No leftover renamed predicate names** (covers both Python and agent-prompt front-matter):
   ```
   rg -n 'nested_mission_depth|request_mission_after' backend/src/ backend/tests/ backend/src/agents/
   ```
   Expected: empty.

4. **No leftover renamed audit JSON keys** in production source:
   ```
   rg -nE '"(mission_id|episode_id|attempt_id|episode_ids|attempt_ids|attempt_sequence_no)"' backend/src/
   ```
   Expected: empty.

5. **Imports work**:
   ```
   python -c "from task_center import Goal, Iteration, Trial, GoalStatus, IterationStatus, TrialStatus, IterationCreationReason, TrialStage, TrialFailReason, TrialOrchestrator, GoalStarter, StartedGoal, ContextScope; print('ok')"
   ```
   Expected: `ok`.

6. **Test suites green**:
   ```
   .venv/bin/pytest backend/tests/unit_test/test_task_center backend/tests/unit_test/test_tools -x -q
   ```
   Expected: 0 failures.

7. **Recipe structural acceptance (Reading-A lock — survives Reading B)**:
   ```
   .venv/bin/pytest backend/tests/unit_test/test_task_center/test_context_engine/test_recipes_planner.py::test_iteration_2_plus_reading_a_structure -x
   ```
   Expected: pass. The test asserts (a) block-kind order, (b) heading-line presence (`# Goal\n`, `# Current Iteration\n`, `## Iteration N accepted plan`, `## Iteration N summary`), and (c) the §2.7 `group_heading` metadata contract. **Not a full-text snapshot.**

8. **Runner scenario smoke**:
   ```
   python -m task_center_runner.scenarios.pipeline.iterative_continuation --smoke
   ```
   Expected: scenario imports, runs one trial, exits cleanly. (If a CLI entry doesn't exist, substitute the equivalent test under `task_center_runner/tests/`.)

9. **Lint clean**:
   ```
   .venv/bin/ruff check backend/src/task_center backend/src/db backend/src/tools/submission backend/src/task_center_runner
   ```
   Expected: no diagnostics.

10. **Type-check clean (if mypy is run in CI)**:
    ```
    .venv/bin/mypy backend/src/task_center backend/src/db
    ```
    Expected: no errors related to renamed symbols.

11. **DB-safety gate fires on legacy tables**:
    ```
    python <<'PY'
    from sqlalchemy import create_engine, text
    from db.engine import init_db_with_legacy_check

    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE missions (id TEXT)"))

    try:
        init_db_with_legacy_check(eng)
    except RuntimeError as exc:
        assert "missions" in str(exc) and "drop_legacy_tier_tables" in str(exc)
        print("ok")
    else:
        raise SystemExit("gate did not fire")
    PY
    ```
    Expected: `ok`.

---

## 5. ADR (Architecture Decision Record)

**Title:** Rename TaskCenter tier hierarchy `Mission/Episode/Attempt` → `Goal/Iteration/Trial`; tighten planner-recipe totality framing (Reading A).

**Decision:** Atomic, single-PR rename of class names, file paths, table names, column names, JSON keys, recipe heading strings, context-block kind values, audit-event payload keys, agent-prompt prose (capitalized noun-form only), predicate names, and `TaskCenterInvariantViolation` message strings. Tier-agnostic enum *values* (status strings, fail-reason strings, stage strings, creation-reason strings) are preserved, **except `SpawnReason` values whose strings textually embed the renamed tier noun** (§2.5.1 carve-out). Lowercase prose verb "attempt" is preserved. Tool names (`submit_full_plan`, `submit_partial_plan`, etc.) are preserved. The `deferred_goal` column is preserved (relationship name, not tier name). Planner-recipe restructured for iteration ≥ 2 so the totality is one `# Goal` H1 group (containing `## Goal`, `## Iteration N accepted plan`, `## Iteration N summary` H2 sub-sections), followed by a separate `# Current Iteration` H1. Implemented entirely via the renderer's existing `group_heading`/`subheading` metadata — no renderer change. A DB-safety startup gate raises if legacy tables remain post-rename, with a one-shot drop script.

**Decision Drivers:**
1. Blast radius (~117 backend files, ~1240 hits) demands a canonical symbol-table approach with surgical greps for verification.
2. LLM-facing artifacts (8 agent prompts × 4 recipe heading constants × 8 ContextBlockKind values × audit JSON keys × invariant message strings) must land coherent in one commit to avoid silent inference degradation.
3. Test-suite stability: ~30 unit tests already encode old strings; their enumerated string churn (§2.15) belongs in the same PR. Acceptance tests use *structural* assertions (block-kind order, heading-line presence) rather than full-text snapshots so future Reading-B work doesn't require rewriting the lock.

**Alternatives Considered:**
- **Option B — Add a new `ContextBlockKind.TOTALITY_FRAME` and restructure `ContextRefs.totality_ref`.** Rejected for this PR (scope creep); listed as a Phase-2 follow-up.
- **Option C — Rename tool actions (`submit_partial_plan` → `submit_partial_iteration`).** Rejected; tool names are stable contract surface for external evaluators and audit dashboards; renaming them invalidates every agent-prompt tool-call example and every dashboard filter (violates Principle 5).
- **Option D — Phased deprecation with dual-naming aliases.** Rejected; SQLAlchemy `create_all` + active dev branch + no external consumers + no alembic make the atomic rename strictly simpler.
- **Option E — Rename `deferred_goal` to `next_iteration_goal`.** Rejected; "continuation" describes the relationship, not the tier.
- **Option F — Rename `Trial` to `Try`, `Round`, or keep `Attempt`.** `Try` collides with Python's `try` keyword in narrative contexts; `Round` overlaps with `task_center_run_id`; `keep Attempt` perpetuates the noun-vs-verb ambiguity. **Connotation note (RL-literature collision):** in classical RL, "trial" is sometimes used loosely as a synonym for "episode" (e.g., bandit literature). We acknowledge this collision; the Reflexion line of work (Shinn et al., NeurIPS 2023) firmly establishes "trial" as a single agent attempt at a task within a multi-trial reflection loop, which is the exact semantics we want here. The chosen vocabulary aligns with the recent agent-as-LLM literature, not the older RL textbook usage.
- **Option G — Reading-A renderer extension (extend `_render_group` so the first block in a group with no `subheading` renders inline).** Rejected for this PR; would touch `renderer.py:132-138` and require a +1-test renderer change. Option (a) — `## Goal` H2 wrapper — gives identical observable structure with zero renderer churn.

**Why Chosen (Option A):** Smallest diff that delivers the user's stated scope; preserves the existing `ContextBlock`/`ContextPacket`/`ContextRefs` schema unchanged structurally (only field names); preserves all tool-name contract surface; preserves all tier-agnostic enum string values (with the §2.5.1 carve-out explicitly called out as an observable change); preserves audit's `deferred_goal` semantics; and resolves the noun-vs-verb confusion that was Principle 5's motivation. The Reading-A reframing is contained to one file (`recipes/_shared.py`) and one structural acceptance test, and degrades gracefully if Reading B is later introduced. The vocabulary alignment with Reflexion (Shinn et al., 2023) — Goal/Iteration/Trial — connects this codebase to the recent agent-loops literature.

**Consequences:**
- Positive: Vocabulary aligns with recent agent-loops literature (Reflexion-style Trial as a single attempt at a task within a reflection loop).
- Positive: LLM-facing prompts get one less noun-vs-verb ambiguity; one consolidated `# Goal` section improves planner reasoning for iteration ≥ 2.
- Positive: All audit/observability consumers update field names once; future audit changes are decoupled from tier renames. The new DB-safety gate prevents silent data corruption from leftover legacy tables.
- Negative: Any in-flight local dev DB rows are orphaned (Phase 10 documents the manual reset; the startup gate ensures the developer cannot miss it).
- Negative: External dashboards or tooling that filter by `"mission_id"` audit keys or `"attempt_planner"` spawn-reason values break; mitigated by the changelog entry in Phase 10.
- Negative: ~10 commits land together; the PR is large but each commit is independently verifiable.
- Negative (acknowledged): RL-textbook readers may experience a minor terminology dissonance with "trial" if they expect bandit-style semantics; mitigated by docstring framing that ties `Trial` to "one shot at delivering the iteration's slice."

**Follow-ups (Phase-2, deferred):**
1. **Reading B:** Introduce `ContextBlockKind.TOTALITY_FRAME` (or restructure `ContextRefs` with a `totality_ref` group) so the renderer enforces "one totality section per packet" structurally rather than by string-heading convention. Design discussion required. The §4.5 #7 acceptance test is structural, so Reading B will not require rewriting it.
2. **Tool-verb rename (Option C):** If a future audit shows tool-name inconsistency materially hurts agent reasoning, revisit `submit_partial_plan` → `submit_partial_iteration` along with a coordinated agent-prompt rewrite.
3. **`deferred_goal` rename:** Re-evaluate `deferred_goal` → `next_iteration_goal` only if a future iteration-pattern variant (e.g. parallel iterations) makes the relational-vs-tier distinction less clear.

---

## 6. Risks & Mitigations (top 5)

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | Stale agent-prompt prose drifts from new recipe headings → planner LLM emits malformed JSON | HIGH | HIGH | Phase 5 + Phase 7 land in lockstep (one PR); Phase 7 verification step greps every agent-prompt file for old heading literals; acceptance criterion §4.5 #1 + #3 catches stragglers across Python + prompts. |
| 2 | Test fixture strings asserting old headings/enum values → 20+ red tests | HIGH | MEDIUM | §2.15 enumerates every test file with string churn; Phase 9 verification re-runs full unit suite; acceptance criterion §4.5 #6. The Reading-A acceptance test (§4.5 #7) is *structural* so it survives later prompt-text edits. |
| 3 | task_center_runner audit JSON key drift (+ `SpawnReason` value drift) → integration smoke fails | MEDIUM | MEDIUM | §2.14 enumerates every audit key; §2.5.1 enumerates `SpawnReason` value hit-sites; Phase 8 + Phase 9 update audit recorder + emission-shape test in lockstep; acceptance criteria §4.5 #2 + #4. |
| 4 | Reading-A `group_heading` contract mis-emitted → blocks render with wrong section structure | LOW | MEDIUM | §2.7 spells out the exact per-block `group_heading`/`subheading` metadata; renderer at `renderer.py:106-122,132-138` is verified to handle this; Phase 5 verification asserts `git diff renderer.py` is empty; §4.5 #7 asserts the resulting structure. |
| 5 | Executor over-renames lowercase prose verb "attempt" → 100+ unwanted edits in agent prompts and docstrings | LOW | MEDIUM | Principle 5 explicit; §1 OUT-of-scope explicit; §2.11.a specifies case-sensitive whole-noun replacements only; §2.16 lists non-changes; acceptance criterion §4.5 #1 uses *positive* checks (`\bAttempt(Status\|Stage\|…)` for type-suffix nouns) plus bare-`Mission|Episode` whole-word match — no brittle verb blacklist. |
| 6 | Legacy DB tables silently coexist after rename → corrupted local dev state | LOW | HIGH | Phase 10 introduces `init_db_with_legacy_check` startup gate + `drop_legacy_tier_tables` one-shot; acceptance criterion §4.5 #11 verifies the gate fires. |

---

## 7. Effort Estimate Summary

| Phase | Files touched | LoC ballpark | Commits |
|---|---|---|---|
| 1. DB models | 3–5 | ~150 | 1 |
| 2. DB stores | 4–5 | ~150 | 1 |
| 3. `_core` types (incl. infra.py message rewrites + assertion fn renames) | 4 | ~110 | 1 |
| 4. Domain modules (incl. `SpawnReason` value rename) | ~12 | ~430 + dir renames | 1 |
| 5. Context engine + Reading-A (renderer untouched) | ~8 | ~250 | 1 |
| 6. Tools layer | ~15 | ~180 | 1 |
| 7. Agent prompts | 8 | ~100 lines of prose edits | 1 |
| 8. task_center_runner | ~20 | ~250 | 1 |
| 9. Tests (incl. structural Reading-A acceptance test) | ~30 | ~400 (mostly mechanical) | 1 |
| 10. Migration notes + DB-safety gate + drop script | 3 | ~60 + ~30 docs | 1 |
| **Total** | **~115** | **~2110** | **~10** |

---

End of plan v2. Architect: please review §0 (RALPLAN-DR + carve-out language), §2.5.1 (SpawnReason carve-out), §2.7 (Reading-A `group_heading` contract — renderer untouched), §2.11.b (invariant-violation messages — line-level table), §4.5 (acceptance criteria — structural #7, dual-check #1, gate-check #11), and §Phase 10 (DB-safety gate) first — those are the v1 → v2 changes.
