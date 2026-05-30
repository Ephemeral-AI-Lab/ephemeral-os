# TaskCenter Outcomes And Context Engine Rewrite Plan

**Status:** Draft, implementation-ready after final review.
**Scope:** `backend/src/task_center`, `backend/src/tools/submission`, agent launch context/guidance wiring, and focused tests.
**Goal:** Replace the current recursive/generic outcome and recipe-packet machinery with a smaller model:
outcomes are task-bound evidence, aggregate outcomes are explicit projections, and each agent receives only the context it can act on.

This plan supersedes the outcome/context portions of older reducer/evaluator-era plans. The current checkout has already moved to planner/generator/reducer tasks and has partial outcome consolidation, but it still carries old concepts that this plan removes: recursive handoff children, `failure`/`fail_reason` outcome payloads, presentation status `failure`, generic `ContextPacket`/`ContextBlockKind` recipe machinery, and reducer `<assigned_prompt>`.

---

## 1. Settled Decisions

- A **task outcome is bounded to one TaskCenter task**. Agent runtime results are metadata; they are not durable outcomes unless TaskCenter converts a terminal submission into a task outcome.
- `task.outcomes` is a role-discriminated UI/rendering field. Planner tasks may have planner outcomes for future UI, but planner outcomes are not execution evidence.
- `attempt.outcomes`, `iteration.outcomes`, and `workflow.outcomes` are execution evidence only. They include generator/reducer outcomes, never planner outcomes.
- Outcome status vocabulary is `success | failed`. No `cancelled`, no `failure`, no `pending`, no `blocker` in stored outcome records.
- No `fail_reason` field on outcomes. Failure detail lives only in the outcome text submitted by the generator/reducer failure tool.
- `startup_failed` means the task produced no terminal outcome. The containing attempt may fail with `outcomes=[]`.
- `submit_workflow_handoff` is its own terminal category. It does not immediately create a task outcome.
- A generator task that hands off to a child workflow is resolved by `child_workflow_id is not None`; do not add `used_submit_workflow_handoff`.
- When the child workflow closes, the parent generator task's `outcomes` becomes exactly `child_workflow.outcomes`. This flattens child workflow evidence into the parent task without a wrapper outcome.
- Context XML should not have `<outcomes>` wrappers. Show task id, role, status, and outcome directly with `<task ...>...</task>`.
- Context body tags are multiline. Do not inline body text as `<goal>...</goal>`.
- Context and task guidance remain two separate initial user messages after the system prompt. Internally they stay separate even if a provider adapter later joins them for transport.

---

## 2. Target Outcome Model

### 2.1 Shared types

Create a small outcome model in `backend/src/task_center/_core/outcomes.py`.

```python
TaskOutcomeStatus = Literal["success", "failed"]
ExecutionRole = Literal["generator", "reducer"]

@dataclass(frozen=True, slots=True)
class PlannedTaskRef:
    task_id: str
    role: Literal["generator", "reducer"]
    assigned_task: str
    needs: tuple[str, ...]
    agent_name: str | None = None

@dataclass(frozen=True, slots=True)
class PlannerTaskOutcome:
    status: TaskOutcomeStatus
    role: Literal["planner"]
    task_id: str
    planned_tasks: tuple[PlannedTaskRef, ...]
    deferred_goal_for_next_iteration: str | None = None

@dataclass(frozen=True, slots=True)
class ExecutionTaskOutcome:
    status: TaskOutcomeStatus
    role: ExecutionRole
    task_id: str
    outcome: str

TaskOutcome = PlannerTaskOutcome | ExecutionTaskOutcome
```

Remove from the stored outcome shape:

- `local_id`
- `children`
- `failure`
- `raw_status`
- generic `Outcome` status strings such as `failure`, `pending`, or `missing task row`

If local task ids are needed for rendering, derive them at render time from `task_id`.

### 2.2 Task outcomes

```python
class TaskCenterTask:
    role: Literal["planner", "generator", "reducer", ...]
    status: TaskCenterTaskStatus
    outcomes: tuple[TaskOutcome, ...]
    terminal_tool_result: dict[str, Any] | None
    child_workflow_id: str | None
```

Task outcome rules:

- Planner success creates one `PlannerTaskOutcome`.
- Planner startup failure creates no planner outcome.
- Generator/reducer success creates one `ExecutionTaskOutcome(status="success", ...)`.
- Generator/reducer failure creates one `ExecutionTaskOutcome(status="failed", ...)`.
- Generator handoff creates no immediate outcome; it sets `child_workflow_id` and waits.
- Generator handoff resolution copies child workflow outcomes into the generator task's outcomes.

Planner outcome construction should come from the normalized `submit_plan_*` payload, not from prose like `"Accepted completes planner submission."`.

```python
def planner_outcome_from_submission(submission: PlannerSubmission) -> PlannerTaskOutcome:
    return PlannerTaskOutcome(
        status="success",
        role="planner",
        task_id=submission.planner_task_id,
        planned_tasks=tuple(
            PlannedTaskRef(
                task_id=task_id_for(submission.attempt_id, item),
                role=item.role,
                assigned_task=item.task_spec_or_prompt,
                needs=item.needs,
                agent_name=item.agent_name,
            )
            for item in submission.generators_and_reducers_in_dag_order
        ),
        deferred_goal_for_next_iteration=submission.deferred_goal_for_next_iteration,
    )
```

For `submit_plan_defers_goal`, the planner outcome is still `status="success"` and includes `deferred_goal_for_next_iteration`. It should also include the planned task refs for the bounded current slice. If a future planner tool defers without any executable slice, represent that as `planned_tasks=()` plus the deferred goal; do not synthesize prose.

### 2.3 Attempt outcomes

Add an `outcomes` field to `Attempt`.

```python
class Attempt:
    ...
    outcomes: tuple[ExecutionTaskOutcome, ...]
```

Projection:

```python
def project_attempt_outcomes(attempt: Attempt, task_store: TaskStore) -> tuple[ExecutionTaskOutcome, ...]:
    return tuple(
        outcome
        for task_id in (*attempt.generator_task_ids, *attempt.reducer_task_ids)
        for outcome in task_store.execution_outcomes(task_id)
        if outcome.status in ("success", "failed")
    )
```

Notes:

- Planner outcomes are excluded.
- Startup failures produce no task outcome, so they do not appear here.
- A handoff generator contributes the flattened child workflow outcomes after the child closes.

### 2.4 Iteration outcomes

Add/keep an `outcomes` field on `Iteration`, but change its meaning to the new projection.

```python
class Iteration:
    ...
    outcomes: tuple[ExecutionTaskOutcome, ...]
```

Projection:

```python
def project_iteration_outcomes(iteration: Iteration, attempts: Sequence[Attempt]) -> tuple[ExecutionTaskOutcome, ...]:
    successful_reducers_from_all_attempts = tuple(
        outcome
        for attempt in attempts
        for outcome in attempt.outcomes
        if outcome.role == "reducer" and outcome.status == "success"
    )

    last_attempt = attempts[-1] if attempts else None
    failed_tasks_from_last_attempt = tuple(
        outcome
        for outcome in (last_attempt.outcomes if last_attempt else ())
        if outcome.role in ("generator", "reducer") and outcome.status == "failed"
    )

    return successful_reducers_from_all_attempts + failed_tasks_from_last_attempt
```

This supports both success and failure:

- Successful iteration: usually reducer successes from the passing attempt, plus any successful reducer evidence from prior failed attempts if those reducers completed useful slices.
- Failed iteration: reducer successes from all attempts plus failed generator/reducer outcomes from the final failed attempt.
- Startup-failed final attempt: no failed task outcome exists, so iteration outcomes are just prior successful reducer outcomes, possibly empty.

### 2.5 Workflow outcomes

Expose `Workflow.outcomes` as the latest iteration projection.

```python
class Workflow:
    ...
    outcomes: tuple[ExecutionTaskOutcome, ...]
```

Projection:

```python
def project_workflow_outcomes(workflow: Workflow, iterations: Sequence[Iteration]) -> tuple[ExecutionTaskOutcome, ...]:
    return iterations[-1].outcomes if iterations else ()
```

Implementation detail: keep one source of truth. The simplest path is to load/populate `Workflow.outcomes` from the latest iteration in the store/read model instead of maintaining an independently mutable workflow outcome column. If UI query performance requires a denormalized workflow column later, write it only at workflow close and assert it equals the latest iteration outcomes.

---

## 3. Submission And Lifecycle Changes

### 3.1 Planner submissions

Files:

- `backend/src/tools/submission/planner/_schemas.py`
- `backend/src/task_center/submissions.py`
- `backend/src/task_center/attempt/orchestrator.py`

Changes:

- Remove `PlannerSubmission.outcome: str`.
- Add a builder that converts validated plan payloads into `PlannerTaskOutcome`.
- Store that planner outcome on the planner task for UI rendering.
- Do not copy planner outcomes into attempt/iteration/workflow outcomes.
- Preserve existing validation: at least one reducer, reachable generators, no cycles, no unknown generator-capable agent.

### 3.2 Generator terminal tools

Files:

- `backend/src/tools/submission/executor/submit_execution_success`
- `backend/src/tools/submission/executor/submit_execution_blocker`
- `backend/src/tools/submission/executor/submit_workflow_handoff`
- `backend/src/tools/submission/executor/__init__.py`
- `backend/src/tools/submission/_factory.py`
- `backend/src/tools/submission/context.py`
- `backend/src/task_center/submissions.py`
- `backend/src/task_center/attempt/orchestrator.py`

Changes:

- Rename `submit_execution_success` to `submit_generator_success`.
- Rename `submit_execution_blocker` to `submit_generator_failure`.
- Convert `GeneratorSubmission.status` to `Literal["success", "failed"]`.
- Remove `blocker` as an outcome status. If the underlying task status still needs a blocked/failed distinction, keep that in task status or terminal metadata, not in `ExecutionTaskOutcome.status`.
- `submit_generator_success(outcome=...)` writes an execution outcome with `status="success"`.
- `submit_generator_failure(outcome=...)` writes an execution outcome with `status="failed"`.
- `submit_workflow_handoff(goal_handoff=...)` starts the child workflow and sets `child_workflow_id`; it writes no outcome until child workflow closure.

Compatibility option: keep old tool modules as thin aliases for one release if tests or prompts still import them, but the exposed tool names should be the new names.

### 3.3 Reducer terminal tools

Files:

- `backend/src/tools/submission/reducer/submit_reduction_success`
- `backend/src/tools/submission/reducer/submit_reduction_failure`
- `backend/src/task_center/submissions.py`
- `backend/src/task_center/attempt/orchestrator.py`

Changes:

- Keep the existing tools if minimizing churn is preferred.
- Preferred symmetry: rename to `submit_reducer_success` and `submit_reducer_failure`.
- Normalize `ReducerSubmission.status` to `Literal["success", "failed"]`.
- The reducer submitted `outcome` becomes the `ExecutionTaskOutcome.outcome` text.

### 3.4 Handoff closure

Files:

- `backend/src/task_center/attempt/orchestrator.py`
- `backend/src/task_center/workflow/lifecycle.py`
- `backend/src/task_center/run_controller.py`
- `backend/src/task_center/_core/outcomes.py`

Current behavior wraps child workflow evidence in a parent `Outcome(children=..., failure=...)`. Replace it.

```python
def apply_child_workflow_closed(parent_task_id: str, child_workflow: Workflow) -> None:
    parent_status = "done" if child_workflow.status == WorkflowStatus.SUCCEEDED else "failed"
    task_store.update_task(
        parent_task_id,
        status=parent_status,
        outcomes=child_workflow.outcomes,
    )
```

The parent generator task is identifiable as a handoff by `child_workflow_id is not None`; the UI can render it as a handoff task while still displaying the flattened outcomes.

---

## 4. New Context Shapes

### 4.1 Planner context

Planner scope:

```python
PlannerContextScope = {
    "workflow_goal": str,
    "prior_iteration_outcomes": tuple[tuple[ExecutionTaskOutcome, ...], ...],
    "current_iteration_goal": str,
    "current_iteration_previous_attempts_outcome": tuple[tuple[ExecutionTaskOutcome, ...], ...],
}
```

Rendered shape:

```xml
<context role="planner">
  <workflow>
    <goal>
      Build the complete feature.
    </goal>

    <prior_iterations>
      <iteration sequence="1">
        <task task_id="attempt1:red:verify_storage" role="reducer" status="success">
          Storage layer is implemented and verified.
        </task>
      </iteration>
    </prior_iterations>

    <current_iteration sequence="2">
      <goal>
        Finish the API and CLI slice.
      </goal>

      <previous_attempts>
        <attempt sequence="1" status="failed">
          <task task_id="attempt2:gen:api" role="generator" status="success">
            API endpoints were implemented.
          </task>
          <task task_id="attempt2:red:verify_api" role="reducer" status="failed">
            Verification failed because the CLI command still calls the old endpoint.
          </task>
        </attempt>
      </previous_attempts>
    </current_iteration>
  </workflow>
</context>
```

Rules:

- Wrap planner context in `<workflow>` because the planner is planning the current iteration to finalize the workflow goal.
- Prior iteration details are intentionally scoped to iteration outcomes; internal attempt history is hidden.
- Current iteration previous attempts include generator and reducer task outcomes, success and failed.
- Planner outcomes are hidden from planner historical context because they are not execution evidence.
- Do not emit empty wrapper sections unless they clarify the frame. Prefer omitting empty `<prior_iterations>` and `<previous_attempts>`.

### 4.2 Generator context

Generator scope:

```python
GeneratorContextScope = {
    "dependency_results": tuple[ExecutionTaskOutcome, ...],
    "assigned_task": PlannedTaskRef,
}
```

Rendered shape:

```xml
<context role="generator">
  <dependencies>
    <dependency task_id="attempt2:red:verify_storage">
      <task task_id="attempt2:red:verify_storage" role="reducer" status="success">
        Storage layer is implemented and verified.
      </task>
    </dependency>
  </dependencies>

  <assigned_task task_id="attempt2:gen:api">
    Implement the API endpoints against the verified storage layer.
  </assigned_task>
</context>
```

Rules:

- No workflow goal.
- No attempt-wide plan.
- No reducer outcomes except dependencies.
- No planner outcome.

### 4.3 Reducer context

Reducer scope:

```python
ReducerContextScope = {
    "dependency_results": tuple[ExecutionTaskOutcome, ...],
    "assigned_task": PlannedTaskRef,
}
```

Rendered shape:

```xml
<context role="reducer">
  <dependencies>
    <dependency task_id="attempt2:gen:api">
      <task task_id="attempt2:gen:api" role="generator" status="success">
        API endpoints were implemented.
      </task>
    </dependency>
  </dependencies>

  <assigned_task task_id="attempt2:red:verify_api">
    Verify the API and CLI slice and submit a reducer success or failure outcome.
  </assigned_task>
</context>
```

Rules:

- Reducer should use the same assigned block name as generator: `<assigned_task>`, not `<assigned_prompt>`.
- Reducer task guidance should say: `Complete <assigned_task> using <dependencies>.`

### 4.4 Success and failure examples

Successful attempt:

```python
attempt.outcomes == (
    ExecutionTaskOutcome("success", "generator", "a1:gen:api", "API endpoints implemented."),
    ExecutionTaskOutcome("success", "reducer", "a1:red:verify_api", "API and CLI verified."),
)

iteration.outcomes == (
    ExecutionTaskOutcome("success", "reducer", "a1:red:verify_api", "API and CLI verified."),
)

workflow.outcomes == iteration.outcomes
```

Failed attempt with generator failure:

```python
attempt.outcomes == (
    ExecutionTaskOutcome("success", "generator", "a1:gen:storage", "Storage implementation complete."),
    ExecutionTaskOutcome("failed", "generator", "a1:gen:api", "API implementation failed because migrations are missing."),
)

iteration.outcomes == (
    # previous reducer successes from this iteration, if any,
    ExecutionTaskOutcome("failed", "generator", "a1:gen:api", "API implementation failed because migrations are missing."),
)

workflow.outcomes == iteration.outcomes
```

Startup failure:

```python
attempt.status == AttemptStatus.FAILED
attempt.outcomes == ()
```

Handoff success:

```python
parent_generator_task.child_workflow_id == child_workflow.id
parent_generator_task.outcomes == child_workflow.outcomes
```

---

## 5. Context Engine Module Rewrite

Target folder:

```text
backend/src/task_center/context_engine/
├── __init__.py
├── scope.py
├── context.py
├── xml.py
└── task_guidance.py
```

Delete or fold away:

```text
backend/src/task_center/context_engine/packet.py
backend/src/task_center/context_engine/renderer.py
backend/src/task_center/context_engine/context_outline.py
backend/src/task_center/context_engine/tag_dictionary.py
backend/src/task_center/context_engine/recipes_registry.py
backend/src/task_center/context_engine/recipes/
backend/src/task_center/agent_launch/task_guidance.py
```

### 5.1 `scope.py`

Keep scope as launch identity only.

```python
@dataclass(frozen=True, slots=True)
class ContextScope:
    role: Literal["planner", "generator", "reducer"]
    workflow_id: str
    iteration_id: str | None = None
    attempt_id: str | None = None
    task_id: str | None = None

    @classmethod
    def for_planner(cls, *, workflow_id: str, iteration_id: str, attempt_id: str) -> Self: ...
    @classmethod
    def for_generator(cls, *, workflow_id: str, iteration_id: str, attempt_id: str, task_id: str) -> Self: ...
    @classmethod
    def for_reducer(cls, *, workflow_id: str, iteration_id: str, attempt_id: str, task_id: str) -> Self: ...
```

### 5.2 `context.py`

Use an explicit typed document. Do not call it a node.

```python
@dataclass(frozen=True, slots=True)
class ContextSection:
    tag: str
    attrs: Mapping[str, str] = field(default_factory=dict)
    text: str | None = None
    children: tuple["ContextSection", ...] = ()
    guidance: str | None = None

@dataclass(frozen=True, slots=True)
class AgentContext:
    role: Literal["planner", "generator", "reducer"]
    sections: tuple[ContextSection, ...]
    directive: str
    context_limits: tuple[str, ...] = ()
```

Builders:

```python
def build_agent_context(scope: ContextScope, deps: ContextEngineDeps) -> AgentContext:
    match scope.role:
        case "planner":
            return build_planner_context(scope, deps)
        case "generator":
            return build_generator_context(scope, deps)
        case "reducer":
            return build_reducer_context(scope, deps)

def build_planner_context(scope: ContextScope, deps: ContextEngineDeps) -> AgentContext: ...
def build_generator_context(scope: ContextScope, deps: ContextEngineDeps) -> AgentContext: ...
def build_reducer_context(scope: ContextScope, deps: ContextEngineDeps) -> AgentContext: ...
```

Builder responsibilities:

- Fetch state from stores.
- Project exactly the role scope.
- Return typed context sections.
- Attach concise context limits that explain hidden detail only when it matters.

Builder non-responsibilities:

- No lifecycle transitions.
- No attempt close decisions.
- No terminal tool policy.
- No token-budget priority system until there is a concrete need.
- No generic recipe registry.

### 5.3 `xml.py`

Rendering should be a pure function over `AgentContext`.

```python
def render_context_xml(context: AgentContext) -> str:
    return render_section(
        ContextSection(
            tag="context",
            attrs={"role": context.role},
            children=context.sections,
        )
    )

def render_task_outcome(outcome: ExecutionTaskOutcome) -> ContextSection:
    return ContextSection(
        tag="task",
        attrs={
            "task_id": outcome.task_id,
            "role": outcome.role,
            "status": outcome.status,
        },
        text=outcome.outcome,
    )
```

Rendering rules:

- Escape attributes and text.
- Multiline body for all non-empty text sections.
- No self-closing tags.
- No `<outcomes>` wrapper.
- No pre-rendered XML strings in section text.

### 5.4 `task_guidance.py`

Move task guidance into `context_engine` so guidance is derived from the same typed context that renders XML.

```python
def render_task_guidance(context: AgentContext) -> str:
    return "\n\n".join(
        part
        for part in (
            render_context_outline(context),
            render_context_limits(context),
            render_what_to_do(context),
        )
        if part
    )
```

Target guidance:

Planner:

```text
What's in context:
- <workflow>: workflow goal and current planning frame
- <prior_iterations>: reducer outcomes from prior iterations
- <current_iteration>: current goal and previous attempt evidence

Context limits:
- Prior iterations omit internal attempt history.
- Planner outcomes are omitted from iteration and workflow history.

What to do:
- Plan generator and reducer tasks for <current_iteration><goal>.
```

Generator:

```text
What's in context:
- <dependencies>: outcomes produced by dependency tasks
- <assigned_task>: your assigned task

What to do:
- Complete <assigned_task> using <dependencies>.
```

Reducer:

```text
What's in context:
- <dependencies>: outcomes produced by dependency tasks
- <assigned_task>: your assigned task

What to do:
- Complete <assigned_task> using <dependencies>.
```

---

## 6. Migration Workflow

### Phase 0 - Characterize current behavior

Before changing implementation, add or update focused characterization tests for:

- Planner context includes workflow goal, prior iteration outcome tasks, current iteration goal, and previous attempt outcomes.
- Generator context is dependencies plus assigned task.
- Reducer context is dependencies plus assigned task.
- Planner terminal submission stores a planner task outcome but does not affect attempt/iteration/workflow outcomes.
- Generator/reducer success/failure terminal submissions write execution task outcomes from submitted `outcome`.
- Handoff task has no immediate outcome and later copies child workflow outcomes.
- Startup failure produces no task outcome.

### Phase 1 - Replace outcome records

Files:

- `backend/src/task_center/_core/outcomes.py`
- `backend/src/task_center/_core/state.py`
- persistence/store serializers
- tests under `tests/unit_test/test_task_center`

Steps:

1. Introduce `PlannerTaskOutcome`, `ExecutionTaskOutcome`, and serializers.
2. Migrate status strings to `success | failed`.
3. Remove `children`, `failure`, and recursive handoff outcome rendering.
4. Add `Attempt.outcomes`.
5. Expose `Workflow.outcomes` as latest iteration outcomes.
6. Update parsing to tolerate legacy records during migration, but emit only the new shape.

### Phase 2 - Rewrite terminal submission projection

Files:

- `backend/src/task_center/submissions.py`
- `backend/src/tools/submission/planner/_schemas.py`
- `backend/src/tools/submission/executor/*`
- `backend/src/tools/submission/reducer/*`
- `backend/src/tools/submission/_factory.py`
- `backend/src/tools/submission/context.py`
- `backend/src/task_center/attempt/orchestrator.py`
- `backend/src/task_center/attempt/run_stage.py`
- `backend/src/task_center/iteration/attempt_coordinator.py`

Steps:

1. Convert planner submission payload into `PlannerTaskOutcome`.
2. Rename generator terminal tools or add compatibility aliases, then update prompts/tool factory imports.
3. Convert generator/reducer terminal submissions to `success | failed`.
4. Write task outcomes only from terminal success/failure submissions.
5. On attempt close, write `attempt.outcomes`.
6. On iteration close, write the new iteration projection.
7. On workflow close/read, expose the latest iteration projection.
8. On handoff child closure, copy child workflow outcomes to the parent generator task.

### Phase 3 - Replace context engine internals

Files:

- `backend/src/task_center/context_engine/*`
- `backend/src/task_center/agent_launch/composer.py`
- `backend/src/task_center/agent_launch/entry_messages.py`
- `backend/src/task_center/agent_launch/skill_message.py`
- `backend/src/task_center/attempt/launch.py`
- root re-exports in `backend/src/task_center/__init__.py`

Steps:

1. Add `context.py`, `xml.py`, and `task_guidance.py`.
2. Rework `ContextEngine.build(...)` to return `AgentContext`, or delete `ContextEngine` if a single `build_agent_context(scope, deps)` function is clearer.
3. Rewrite planner/generator/reducer builders to produce the target shapes.
4. Move `agent_launch/task_guidance.py` into `context_engine/task_guidance.py`.
5. Update `AgentEntryComposer` to render two separate rows: context XML and task guidance.
6. Delete recipe registry, generic packet/block priority classes, tag dictionary, and outline walker after callers are migrated.

### Phase 4 - Update prompts, docs, and tests

Files:

- agent profile/skill prompt files that mention old tool names or old tags
- `docs/architecture/task_center/context-engine.html`
- `docs/architecture/task_center/*` pages with outcome claims
- existing plan docs only if they are misleading entry points

Steps:

1. Replace `<needs>`/`<assigned_prompt>` guidance references with `<dependencies>`/`<assigned_task>` where applicable.
2. Replace old generator terminal tool names in prompts.
3. Remove guidance that refers to `<outcomes>` wrappers, recursive handoff children, `failure`, or `fail_reason`.
4. Refresh the architecture page metadata and evidence paths after implementation.

---

## 7. Verification

Run the narrowest checks per phase:

```bash
uv run pytest tests/unit_test/test_task_center/test_context_engine
uv run pytest tests/unit_test/test_task_center
uv run pytest tests/unit_test/test_tools/test_submission
uv run ruff check backend/src/task_center backend/src/tools/submission tests/unit_test/test_task_center
```

If `uv` is unavailable in the active shell, use the repo-supported environment documented in `AGENTS.md` before falling back to global Python. Do not treat global missing dependencies as a code failure.

Acceptance criteria:

- Stored new outcome records never include `children`, `failure`, `raw_status`, `fail_reason`, `pending`, `blocker`, or status value `failure`.
- Planner task outcomes are stored on the planner task and excluded from attempt/iteration/workflow projections.
- Attempt outcomes include only generator/reducer execution outcomes.
- Iteration outcomes equal successful reducer outcomes from all attempts plus failed generator/reducer outcomes from the final attempt.
- Workflow outcomes equal the latest iteration outcomes.
- Handoff generator outcomes equal child workflow outcomes after child closure.
- Planner context matches the target `<workflow>` shape.
- Generator and reducer contexts contain only `<dependencies>` and `<assigned_task>`.
- Task guidance is generated from `AgentContext`, not from a separate tag dictionary.
- Context and guidance remain separate initial user messages.

---

## 8. Open Decisions Before Coding

- Whether to rename reducer tools to `submit_reducer_success` / `submit_reducer_failure` in the same migration or keep current reducer tool names for now.
- Whether `Workflow.outcomes` should be a persisted database column immediately or a read-model field derived from the latest iteration. This plan recommends derived first for simplicity.
- Whether to keep temporary compatibility aliases for old generator tool names during the migration. This plan recommends aliases only if tests or external callers still import old names.
