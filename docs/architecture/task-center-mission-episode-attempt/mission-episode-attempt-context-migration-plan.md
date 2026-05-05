# TaskCenter Mission / Episode / Attempt Context and Naming Migration Plan

**Status:** In progress - prompt semantics and auxiliary block cleanup are
implemented; mission / episode / attempt path and symbol cleanup is in flight.

**Goal:** migrate the LLM-facing context language and naming surface from
legacy `complex-task request -> segment -> harness graph -> task` language to
the clearer semantic frame `Mission -> Episode -> Attempt -> Task`.

This plan is about context semantics and naming conventions: headings,
summaries, recipe inputs, renderer ordering, documentation paths, Python
package/file paths, classes, functions, variables, and tests. Database table and
column names may lag behind only where a storage migration requires it; new
runtime code and tests should not expose legacy request / segment / harness
graph names as domain vocabulary.

## 1. Decisions

Use these terms in rendered prompts and runtime-facing names:

| Legacy source | Canonical term | Meaning |
| ------------- | -------------- | ------- |
| `ComplexTaskRequest`, request-layer domain nouns | Mission | The full goal and close boundary for one delegated mission. |
| `TaskSegment`, segment-layer domain nouns | Episode | One self-contained continuation slice of the mission. |
| `HarnessGraph`, graph-layer domain nouns | Attempt | One planner -> generator DAG -> evaluator try within an episode. |
| `TaskCenterTaskRecord` | Task | One atomic planner, generator, evaluator, or helper task. |

`Mission` is the lifecycle noun. Parent executor or original user context may
appear as background evidence, but it is not the Mission contract for this
Mission.

`Episode` is preferred over `segment` because segment implies the smallest unit.
That is wrong here: the smallest executable unit is the task. `Episode` also
fits the partial-planning lifecycle: an episode may complete the mission or
produce a continuation goal for the next episode.

Do not use `Current Episode Task`. `Task` already names the bottom layer, so
putting it into the episode heading blurs the hierarchy. Use `Current Episode`.

Filesystem names should follow the same semantic frame where they describe these
domain layers:

| Legacy filesystem name | Target filesystem name | Notes |
| ---------------------- | ---------------------- | ----- |
| `complex_task` | `mission` | Delegated mission lifecycle boundary. |
| `segment` | `episode` | Vertical continuation slice layer. |
| `harness_graph` | `attempt` | One planner -> generator DAG -> evaluator try. |
| `task` | `task` | Already matches the bottom-layer term. |

Do not rename service-oriented folders merely because they touch these layers.
`context_engine` stays `context_engine`: it composes context packets, not a
Mission/Episode/Attempt lifecycle object.

### 1.1 Naming Convention Matrix

Use one noun per lifecycle layer. Avoid mixed names such as
`request_segment_graph`, `segment_graph`, `graph_store`, or
`delegated_request` when the object is a Mission, Episode, or Attempt.

| Surface | Legacy name shape | Canonical name shape |
| ------- | ----------------- | -------------------- |
| Folder/package | `complex_task/`, `segment/`, `harness_graph/` | `mission/`, `episode/`, `attempt/` |
| Documentation path | `task-center-harness-migration/`, `context-semantics-migration-plan.md` | `task-center-mission-episode-attempt/`, `mission-episode-attempt-context-migration-plan.md` |
| DTO/model class | `ComplexTaskRequest`, `TaskSegment`, `HarnessGraph` | `Mission`, `Episode`, `Attempt` |
| Store class | `ComplexTaskRequestStore`, `TaskSegmentStore`, `HarnessGraphStore` | `MissionStore`, `EpisodeStore`, `AttemptStore` |
| DB model class | `ComplexTaskRequestRecord`, `TaskSegmentRecord`, `HarnessGraphRecord` | `MissionRecord`, `EpisodeRecord`, `AttemptRecord` |
| Lifecycle service | `ComplexTaskRequestHandler`, `TaskSegmentManager`, `HarnessGraphOrchestrator` | `MissionHandler`, `EpisodeManager`, `AttemptOrchestrator` |
| Start boundary | `ComplexTaskRequestStarter`, `StartedComplexTaskRequest`, `ComplexTaskHandoffCoordinator` | `MissionStarter`, `StartedMission` |
| Submission context | `HarnessSubmissionContext`, `resolve_harness_submission_context`, `_resolve_graph_context` | `AttemptSubmissionContext`, `resolve_attempt_submission_context`, `_resolve_attempt_context` |
| Close callback | `on_graph_closed`, `handle_segment_closed`, `close_complex_task_request` | `on_attempt_closed`, `handle_episode_closed`, `close_mission` |
| Variables/parameters | `request_id`, `segment_id`, `graph_id` when naming lifecycle rows | `mission_id`, `episode_id`, `attempt_id` |
| Store variables | `request_store`, `segment_store`, `graph_store` | `mission_store`, `episode_store`, `attempt_store` |
| Context scope/refs | `ContextScope.request_id`, `ContextRefs.request_id` | `ContextScope.mission_id`, `ContextRefs.mission_id` |
| Context block constants | `COMPLEX_TASK_GOAL`, `SEGMENT_GOAL`, `PRIOR_SEGMENT_*` | `MISSION_GOAL`, `EPISODE_GOAL`, `PRIOR_EPISODE_*` |
| Failed-attempt evidence | `graph_landscape`, `failed_graph_landscape` | `attempt_landscape`, `failed_attempt_landscape` |
| Tests and fixtures | `_seed_request`, `_seed_segment`, `_seed_graph`, `*_request_segment_graph_*` | `_seed_mission`, `_seed_episode`, `_seed_attempt`, `*_mission_episode_attempt_*` |

`request` remains valid only when it is a verb or a transport concept, such as
`request_mission_solution(...)`, HTTP request handling, provider requests, or
the generic non-TaskCenter `TaskCenterRequestRecord` API. It should not name the
TaskCenter lifecycle object.

## 2. Episode 1 Special Case

For episode 1, the mission and current episode are equivalent:

```text
mission_goal == episode_goal
```

No previous episode has narrowed or advanced the mission yet, so the prompt
must not render duplicate mission and episode sections. Render one merged
section:

```md
# Mission / Current Episode

<full mission goal>
```

For episode 2+, render the layers separately:

```md
# Mission

<full mission goal>

# Previous Episode Results

<accepted prior episode projection>

# Current Episode

<current continuation goal>
```

The correct claim is narrow: "episode 1 is equivalent to the mission." Do not
generalize that to "an episode is the mission."

## 3. Prompt Sections

The storage block kinds can remain mechanical, but renderer output should use
these headings:

| Current block kind | Rendered section | Notes |
| ------------------ | ---------------- | ----- |
| `mission_goal` | `# Mission` | Omitted as a separate section for episode 1. |
| `episode_goal` | `# Mission / Current Episode` or `# Current Episode` | Merged for episode 1; separate for episode 2+. |
| `prior_episode_specification`, `prior_episode_summary` | `# Previous Episode Results` | Accepted prior episode projection in sequence order. |
| `failed_attempt_landscape` | `# Failed Attempts` | Failed attempts inside the current episode. |
| `task_specification` | `# Attempt Plan` | Planner-emitted plan for the current attempt. |
| `dependency_summary`, `completed_task_summary` | `# Dependency Results` | Grouped heading with task subsections. |
| `planned_task_spec` | `# Assigned Task` | Generator's local assignment; always last for generator. |
| `evaluation_criteria` | `# Evaluation Criteria` | Authoritative pass/fail criteria for the current attempt; always last for evaluator. |

Use `Evaluation Criteria` because it mirrors the `submit_full_plan` and
`submit_partial_plan` field name. Do not use `Evaluation Goal`: `goal` is already
the Mission/Episode/continuation vocabulary, and evaluator inputs must be
falsifiable criteria rather than an aspirational target.

Dependency results must render as one grouped section:

```md
# Dependency Results

## <task name or id>

<summary/result content>

## <task name or id>

<summary/result content>
```

Internally, generator inputs can still use `dependency_summary` and evaluator
inputs can still use `completed_task_summary`. The LLM-facing vocabulary should
be unified as `Dependency Results`.

## 4. Role Context Contracts

### Planner

Episode 1:

```text
Mission / Current Episode -> Failed Attempts (if any)
```

Episode 2+:

```text
Mission -> Previous Episode Results -> Current Episode -> Failed Attempts (if any)
```

Planner context defines the next attempt. Failed attempts are retry evidence
inside the same episode, not previous episode results.

### Generator

```text
Attempt Plan -> Dependency Results -> Assigned Task
```

The generator should not receive the full mission or episode history by
default. If the planner wants a generator to account for wider mission context,
that requirement belongs in the assigned task.

`Assigned Task` must be the final section so the generator ends on its concrete
local obligation.

### Evaluator

Episode 1:

```text
Mission / Current Episode -> Attempt Plan -> Dependency Results -> Evaluation Criteria
```

Episode 2+:

```text
Mission -> Previous Episode Results -> Current Episode -> Attempt Plan -> Dependency Results -> Evaluation Criteria
```

The evaluator receives the mission and episode frame as orientation. It still
judges the current attempt against the current attempt plan and evaluation
criteria. Mission, previous episode, and current episode frames are
non-authoritative for pass/fail; they must not broaden what the evaluator accepts
or rejects.

`Evaluation Criteria` must be the final section so the evaluator ends on the
pass/fail contract.

## 5. Summary Semantics

The target LLM-facing summary vocabulary is:

| Heading | Content source today | Target meaning |
| ------- | -------------------- | -------------- |
| `Previous Episode Results` | `Episode.task_specification` and `Episode.task_summary` from closed prior segments | Accepted prior episode projection available today: the accepted attempt plan plus the closed episode summary. |
| `Dependency Results` | Upstream task summaries for generator; completed generator/verifier summaries for evaluator | Results produced by prerequisite tasks inside the current attempt. |
| `Failed Attempts` | Failed graph landscape blocks | Retry evidence from failed attempts inside the current episode. |

Current storage only has a partial episode-result projection:
`Episode.task_summary` comes from the passing evaluator summary and
`Episode.task_specification` comes from the passing graph. That is enough
for the prompt-heading migration, but it is not a full episode-result model.

A later summary phase should introduce richer episode results with:

- completed work,
- continuation goal,
- artifact references,
- residual risks,
- attempted-plan history.

Until that richer model exists, do not claim `Previous Episode Results` contains
artifacts, residual risks, or a continuation handoff beyond what the current
closed episode summary actually records.

Do not block the prompt semantics migration on that richer model.

## 6. Migration Phases

Implementation state:

- Phases 1-7 are implemented for the live context-engine, helper-agent, planner
  variant, and main-agent prompt surfaces.
- Phases 8-10 finish the mission / episode / attempt naming cleanup across
  documentation paths, runtime paths, and runtime symbols.

### Phase 1 - Renderer Contract

- Recipes must emit blocks in role-specific semantic presentation order, either
  directly or through generic block metadata such as `heading`, `group`, or
  `section_order`.
- The markdown renderer must stay role-agnostic and preserve packet order after
  compression.
- Keep priority for compression only.
- Preserve required blocks under token pressure, but do not let priority sorting
  override semantic order.
- Add grouped rendering for `Dependency Results`.
- Support per-block headings through metadata where that keeps recipes simple.
- Remove `parent_question` and `capability_note` from the renderer's first-class
  heading templates. They are not Mission / Episode / Attempt / Task concepts.

Verification:

- A mixed-priority packet renders in semantic packet order, not priority order.
- Compression preserves the order of blocks that remain after truncation.
- Dependency summaries render as `# Dependency Results` with `## ...`
  subsections.
- Rendered prompts contain no `# Parent question` or `# Capability note`
  sections.

### Phase 2 - Planner Recipe

- For episode 1, emit/render one `Mission / Current Episode` frame.
- For episode 2+, render separate `Mission`, `Previous Episode Results`, and
  `Current Episode` frames.
- Continue using failed attempt landscape only for attempts inside the current
  episode.

Verification:

- Episode 1 planner context has no duplicate mission/current-episode content.
- Episode 2+ planner context includes prior accepted episode results before the
  current episode.
- Failed attempts remain separate from previous episode results.

### Phase 3 - Generator Recipe

- Render the current attempt plan first.
- Render dependency task results under grouped `Dependency Results`.
- Render the assigned local task last.
- Do not add mission or episode history by default.

Verification:

- Dependency results include all ready upstream dependency summaries.
- `Assigned Task` is the last top-level section.
- Mission/episode headings are absent unless explicitly added through a task
  assignment.

### Phase 4 - Evaluator Recipe

- Add mission and episode framing to evaluator context.
- Use the episode 1 merged-frame special case.
- Render current attempt plan before dependency results.
- Render completed generator/verifier summaries as `Dependency Results`.
- Render evaluation criteria last.

Verification:

- Episode 1 evaluator context order is:
  `Mission / Current Episode -> Attempt Plan -> Dependency Results -> Evaluation Criteria`.
- Episode 2+ evaluator context order is:
  `Mission -> Previous Episode Results -> Current Episode -> Attempt Plan -> Dependency Results -> Evaluation Criteria`.
- Evaluator tests assert that mission/episode context is framing only, while
  pass/fail authority still comes from current attempt evaluation criteria.

### Phase 5 - Tests and Fixtures

Add focused tests near the context engine:

- planner episode 1 merged heading,
- planner episode 2+ split headings,
- generator dependency grouping and assigned-task-last order,
- evaluator episode 1 order,
- evaluator episode 2+ order,
- compression preserves required blocks while retaining semantic order,
- `dependency_summary` and `completed_task_summary` both render as dependency
  result subsections,
- no recipe or renderer test depends on `parent_question`,
- no launch/composer test injects `capability_note` as a context block.

Use a representative benchmark prompt, such as the first PR description from
`backend/config/benchmarks/sweevo_gpt5_2025_08_07_pr_descriptions.csv`, as a
manual demonstration fixture for Mission -> Episode -> Attempt -> Task
semantics. The CSV must be parsed with a real CSV parser because the
`pr_description` field is multiline.

### Phase 6 - Later Summary Model

After prompt semantics are stable, design a first-class episode-result summary.
That phase can replace the current `Episode.task_summary` projection with a
shape that explicitly records continuation goal, artifacts, residual risks, and
attempted-plan history.

### Phase 7 - Legacy Auxiliary Block Cleanup

Delete auxiliary context-block concepts that do not belong to the new semantic
frame:

- Remove `ContextBlockKind.PARENT_QUESTION`.
- Remove `ContextBlockKind.CAPABILITY_NOTE`.
- Remove renderer headings for `parent_question` and `capability_note`.
- Replace helper-agent parent-task framing with existing parent-context
  inheritance or a role-local assigned-task section; do not introduce a new
  top-level context kind for it.
- Remove planner variant `required_context_blocks` that only emit
  `capability_note`. The selected variant's terminal-tool surface is the hard
  gate; do not duplicate it as a prompt block.
- Update tests and agent markdown that reference `parent_question` or
  `capability_note`.

Verification:

- `rg "parent_question|capability_note" backend/src backend/tests` returns no
  live code or test references.
- Planner full-only selection still hides `submit_partial_plan` through the
  selected variant's terminals.
- Helper recipes still preserve parent context without a `parent_question`
  block kind.

### Phase 8 - Documentation Filesystem Rename

Rename the architecture-doc package and old-object filenames so durable docs
match the Mission / Episode / Attempt / Task vocabulary.

Recommended doc path map:

| Legacy path | Target path |
| ----------- | ----------- |
| `docs/architecture/task-center-harness-migration.md` | `docs/architecture/task-center-mission-episode-attempt.md` |
| `docs/architecture/task-center-harness-migration/` | `docs/architecture/task-center-mission-episode-attempt/` |
| `context-semantics-migration-plan.md` | `mission-episode-attempt-context-migration-plan.md` |
| `complex-task-workflow-overview.md` | `mission-episode-attempt-workflow-overview.md` |
| `phase-01-graph-and-attempt-model.md` | `phase-01-mission-episode-attempt-model.md` |
| `phase-02-harness-graph-orchestrator-lifecycle.md` | `phase-02-attempt-orchestrator-lifecycle.md` |
| `phase-04-complex-task-spawning.md` | `phase-04-mission-spawning.md` |

Leave phase implementation-plan and implementation-report filenames in place
unless their basename contains an old domain term. Their phase number already
gives the durable navigation key.

Verification:

- The phase index links resolve after the folder rename.
- `rg "task-center-harness-migration|context-semantics-migration-plan|complex-task-workflow-overview|phase-01-graph-and-attempt-model|phase-02-harness-graph-orchestrator-lifecycle|phase-04-complex-task-spawning" docs README.md`
  returns no stale links except historical notes that intentionally describe
  legacy paths.
- `git diff --name-status` shows the doc moves as renames rather than delete/add
  churn where possible.

### Phase 9 - Runtime Package/File Rename

Rename Python package and file paths that encode the old domain nouns. Keep
database table/column compatibility separate from runtime path naming.

Recommended runtime path map:

| Legacy path | Target path |
| ----------- | ----------- |
| `backend/src/task_center/complex_task/` | `backend/src/task_center/mission/` |
| `backend/src/task_center/complex_task/request.py` | `backend/src/task_center/mission/mission.py` |
| `backend/src/task_center/complex_task/handler.py` | `backend/src/task_center/mission/handler.py` |
| `backend/src/task_center/complex_task/close_report_delivery.py` | `backend/src/task_center/mission/close_report_delivery.py` |
| `backend/src/task_center/complex_task/ancestry.py` | `backend/src/task_center/mission/ancestry.py` |
| `backend/src/task_center/complex_task/handoff.py` | `backend/src/task_center/mission/starter.py` |
| `backend/src/task_center/segment/` | `backend/src/task_center/episode/` |
| `backend/src/task_center/segment/segment.py` | `backend/src/task_center/episode/episode.py` |
| `backend/src/task_center/segment/manager.py` | `backend/src/task_center/episode/manager.py` |
| `backend/src/task_center/segment/registry.py` | `backend/src/task_center/episode/registry.py` |
| `backend/src/task_center/segment/closure_report.py` | `backend/src/task_center/episode/closure_report.py` |
| `backend/src/task_center/harness_graph/` | `backend/src/task_center/attempt/` |
| `backend/src/task_center/harness_graph/graph.py` | `backend/src/task_center/attempt/state.py` |
| `backend/src/task_center/harness_graph/orchestrator.py` | `backend/src/task_center/attempt/orchestrator.py` |
| `backend/src/task_center/harness_graph/orchestrator_registry.py` | `backend/src/task_center/attempt/orchestrator_registry.py` |
| `backend/src/task_center/harness_graph/runtime.py` | `backend/src/task_center/attempt/runtime.py` |
| `backend/src/task_center/harness_graph/generator_dag.py` | `backend/src/task_center/attempt/generator_dag.py` |
| `backend/src/db/models/complex_task_request.py` | `backend/src/db/models/mission.py` |
| `backend/src/db/models/task_segment.py` | `backend/src/db/models/episode.py` |
| `backend/src/db/models/harness_graph.py` | `backend/src/db/models/attempt.py` |
| `backend/src/db/stores/complex_task_request_store.py` | `backend/src/db/stores/mission_store.py` |
| `backend/src/db/stores/task_segment_store.py` | `backend/src/db/stores/episode_store.py` |
| `backend/src/db/stores/harness_graph_store.py` | `backend/src/db/stores/attempt_store.py` |
| `backend/src/task_center/context_engine/recipes/graph_landscape.py` | `backend/src/task_center/context_engine/recipes/attempt_landscape.py` |
| `backend/src/tools/submission/main_agent/generator/request_complex_task_solution.py` | `backend/src/tools/submission/main_agent/generator/request_mission_solution.py` |

Keep `backend/src/task_center/task/` unchanged because `Task` remains the
bottom-layer concept. Keep database model files and persisted column/table names
unchanged in this pass; compatibility is more important than cosmetic schema
alignment.

Update imports directly and avoid long-lived compatibility wrappers. A temporary
old-path wrapper is acceptable only when it keeps the rename reviewable, and the
same migration must include a deletion step for those wrappers.

Verification:

- `rg "task_center\\.complex_task|task_center\\.segment|task_center\\.harness_graph" backend/src backend/tests`
  returns no live imports after the move.
- `rg "complex_task/|segment/|harness_graph/|request_complex_task_solution|graph_landscape" backend/src backend/tests docs`
  returns no stale path references except historical notes.
- Focused TaskCenter tests and static checks pass after import rewrites:
  `uv run pytest backend/tests/task_center -q`,
  `uv run pytest backend/tests/test_tools -q`,
  `uv run ruff check backend/src/task_center backend/tests/task_center`.

### Phase 10 - Runtime Symbol Rename

After files and folders move, rename classes, functions, variables, context
scope fields, and tests so the code reads with the same mission / episode /
attempt vocabulary as the paths.

Recommended symbol map:

| Legacy symbol | Target symbol |
| ------------- | ------------- |
| `ComplexTaskRequest` | `Mission` |
| `ComplexTaskRequestRecord` | `MissionRecord` |
| `ComplexTaskRequestStore` | `MissionStore` |
| `ComplexTaskRequestHandler` | `MissionHandler` |
| `ComplexTaskRequestStarter`, `StartedComplexTaskRequest` | `MissionStarter`, `StartedMission` |
| `TaskSegment`, `TaskSegmentRecord`, `TaskSegmentStore` | `Episode`, `EpisodeRecord`, `EpisodeStore` |
| `TaskSegmentManager`, `TaskSegmentManagerRegistry` | `EpisodeManager`, `EpisodeManagerRegistry` |
| `TaskSegmentClosureReport` | `EpisodeClosureReport` |
| `HarnessGraph`, `HarnessGraphRecord`, `HarnessGraphStore` | `Attempt`, `AttemptRecord`, `AttemptStore` |
| `HarnessGraphOrchestrator`, `HarnessGraphRuntime` | `AttemptOrchestrator`, `AttemptRuntime` |
| `HarnessSubmissionContext` | `AttemptSubmissionContext` |
| `HarnessSubmissionContextError` | `AttemptSubmissionContextError` |
| `resolve_harness_submission_context` | `resolve_attempt_submission_context` |
| `_resolve_graph_context` | `_resolve_attempt_context` |
| `_assert_submission_graph` | `_assert_submission_attempt` |
| `ContextScope.request_id` | `ContextScope.mission_id` |
| `ContextRefs.request_id` | `ContextRefs.mission_id` |
| `ContextBlockKind.COMPLEX_TASK_GOAL` | `ContextBlockKind.MISSION_GOAL` |
| `ContextBlockKind.SEGMENT_GOAL` | `ContextBlockKind.EPISODE_GOAL` |
| `ContextBlockKind.PRIOR_SEGMENT_SPECIFICATION` | `ContextBlockKind.PRIOR_EPISODE_SPECIFICATION` |
| `ContextBlockKind.PRIOR_SEGMENT_SUMMARY` | `ContextBlockKind.PRIOR_EPISODE_SUMMARY` |
| `request_id`, `segment_id`, `graph_id` lifecycle variables | `mission_id`, `episode_id`, `attempt_id` |
| `request_store`, `segment_store`, `graph_store` lifecycle variables | `mission_store`, `episode_store`, `attempt_store` |
| `delegated_request` lifecycle variables | `delegated_mission` |
| `_seed_request`, `_seed_segment`, `_seed_graph` tests | `_seed_mission`, `_seed_episode`, `_seed_attempt` |

Verification:

- `rg "ComplexTask|TaskSegment|HarnessGraph|complex_task|task_segment|harness_graph" backend/src backend/tests`
  returns no live runtime/test symbols.
- `rg "request_id|segment_id|graph_id|request_store|segment_store|graph_store" backend/src/task_center backend/src/tools/submission backend/tests/task_center`
  returns only non-lifecycle transport or persistence compatibility names that
  are explicitly documented.
- `rg "HarnessSubmissionContext|resolve_harness_submission_context|_resolve_graph_context|COMPLEX_TASK_GOAL|SEGMENT_GOAL|PRIOR_SEGMENT" backend/src backend/tests`
  returns no hits.
- Focused tests and static checks pass:
  `uv run pytest backend/tests/task_center -q`,
  `uv run pytest backend/tests/test_tools -q`,
  `uv run ruff check backend/src/task_center backend/src/tools/submission backend/tests/task_center backend/tests/test_tools`.

## 7. Implementation Touchpoints

Expected code touchpoints:

- `docs/architecture/task-center-mission-episode-attempt.md`
- `docs/architecture/task-center-mission-episode-attempt/`
- `backend/src/db/models/{mission,episode,attempt}.py`
- `backend/src/db/stores/{mission_store,episode_store,attempt_store}.py`
- `backend/src/task_center/context_engine/renderer.py`
- `backend/src/task_center/context_engine/packet.py`
- `backend/src/task_center/context_engine/recipes/planner.py`
- `backend/src/task_center/context_engine/recipes/generator.py`
- `backend/src/task_center/context_engine/recipes/evaluator.py`
- `backend/src/task_center/context_engine/recipes/attempt_landscape.py`
- `backend/src/task_center/context_engine/recipes/helper.py`
- `backend/src/task_center/context_engine/scope.py`
- `backend/src/task_center/agent_launch/resolver.py`
- `backend/src/task_center/mission/`
- `backend/src/task_center/episode/`
- `backend/src/task_center/attempt/`
- `backend/src/tools/submission/context.py`
- `backend/src/tools/submission/main_agent/`
- `backend/src/tools/submission/hooks/`
- `backend/src/tools/submission/notification_triggers/`
- `backend/src/agents/main_agent/planner/agent.md`
- `backend/tests/task_center/context_engine/`
- `backend/tests/task_center/`
- `backend/tests/test_tools/`

Do not rename persisted database tables or columns unless the same phase
includes a migration and compatibility strategy. Runtime models, DTOs, classes,
functions, variables, and test names should move to Mission / Episode / Attempt
as part of this plan.

## 8. Migration Exit Criteria

- Rendered prompts use Mission / Episode / Attempt / Task semantics for planner,
  generator, and evaluator roles.
- Episode 1 renders `# Mission / Current Episode` and does not duplicate the
  same goal under two headings.
- Episode 2+ renders `# Mission`, `# Previous Episode Results`, and
  `# Current Episode` separately.
- Generator context contains `# Dependency Results` when upstream dependency
  summaries exist and always ends with `# Assigned Task`.
- Evaluator context contains `# Dependency Results` and always ends with
  `# Evaluation Criteria`.
- Priority remains a compression policy, not a presentation-order policy.
- Evaluator mission/episode framing does not change the pass/fail policy for the
  current attempt.
- `parent_question` and `capability_note` are removed from live context-engine
  code, tests, renderer headings, and planner variant context blocks.
- Architecture docs and runtime package/file names use Mission / Episode /
  Attempt / Task filesystem vocabulary where those paths name lifecycle layers.
- Runtime classes, functions, variables, context refs, and tests use Mission /
  Episode / Attempt naming for lifecycle objects.
- Old `complex_task`, `segment`, and `harness_graph` import paths are removed or
  explicitly temporary wrappers with a same-phase deletion step.
- Old `request_id`, `segment_id`, and `graph_id` lifecycle variables are removed
  from TaskCenter runtime and tests except where explicitly retained for
  transport/persistence compatibility.

## 9. Non-goals

- No persisted table or column rename unless a concrete migration is included.
- No retry-budget policy change.
- No planner partial-plan gate change.
- No new multi-episode look-ahead system.
- No full episode-result persistence model in this pass.
