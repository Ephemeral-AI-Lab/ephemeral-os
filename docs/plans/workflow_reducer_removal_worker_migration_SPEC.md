# Workflow Reducer Removal And Worker Migration - SPEC

Status: Proposed
Date: 2026-06-09
Owner: eos-workflow / eos-types / eos-tool / eos-agent-run / eos-db / agent profiles

Scope:
- `agent-core/crates/eos-types`
- `agent-core/crates/eos-workflow`
- `agent-core/crates/eos-tool`
- `agent-core/crates/eos-agent-run`
- `agent-core/crates/eos-db`
- `.eos-agents/profile`
- `.eos-agents/tools`
- `.eos-agents/skills`

## 1. Intent

This is an aggressive cleanup migration. The target removes reducer as a
workflow role, converts generator terminology to worker terminology, and removes
the binary generator/reducer execution model from the persisted workflow state,
run records, tool contracts, context recipes, and agent-facing profiles.

The workflow model becomes:

```text
Workflow
  -> Iteration[]
      -> Attempt[]
          -> PlanOutcome?
          -> WorkItemOutcome[]
```

The question "did it succeed?" is answered by the existing lifecycle state:

```text
TaskStatus
AttemptStatus
IterationStatus
WorkflowStatus
```

Outcome records should not duplicate that lifecycle state with extra boolean
fields. Outcome records carry the durable terminal payload that is not already
represented by lifecycle state.

Root task, advisor, and subagent terminal payloads remain outside workflow
outcome aggregation. Do not add new workflow outcome DTOs for them in this
migration.

## 2. Decisions

| Area | Decision |
| --- | --- |
| Reducer role | Delete. There are no reducer rows, reducer tasks, reducer launches, reducer outcomes, reducer context recipes, reducer terminal tools, or reducer profile/skill files. |
| Generator role | Replace with worker. Public workflow contracts use `Worker` / `WorkItem`; no `Generator` public contract remains. |
| Planner identity | Planner launch is keyed by `AttemptId`; planner has no `TaskId`, no `WorkItemId`, and no TaskStore row. |
| Worker identity | Worker rows are the only workflow TaskStore rows. Worker task ids are derived from `(AttemptId, WorkItemId)`. |
| Plan payload | `plan_spec` plus `work_items`; no `task_specs`, no `reducers`, no `disposition`. |
| Work item payload | Each work item carries its own `work_spec`; do not use a separate `task_specs` map. |
| Success/failure | Use `TaskStatus`, `AttemptStatus`, `IterationStatus`, and `WorkflowStatus`. Do not add `status: bool`, `is_success`, or renamed boolean copies to outcome DTOs. |
| Outcome text | Keep one terminal payload text field at the terminal boundary. Do not split root/worker/advisor/subagent into bespoke text field names in this migration. |
| Context data | Do not introduce public context projection DTOs. Context filtering is local to `eos-workflow` render functions. |
| Record paths | Planner run records must become attempt-owned rather than task-owned. Worker run records remain task-owned. |

Do not introduce these names:

- `PlanWorkItem`
- `disposition`
- `submission_kind`
- planner `task_id`
- planner `work_item_id`
- workflow `task_specs`
- workflow reducer compatibility aliases
- `has_structured_outcome`
- `is_successful`
- `user_result`
- `work_result`
- `review_summary`
- `answer`
- `work_instruction`
- `direct_needs`
- `direct_need_outcomes`
- `assigned_work_item`
- `agent_profile_name`
- `worker_task_by_work_item_id`
- `ContextOutcomeSlice`
- `ContextOutcomeView`
- `AttemptOutcomeForContext`
- `IterationOutcomeForContext`
- `WorkflowNodeId`

Naming rules:

| Surface | Rule |
| --- | --- |
| Workflow files | Name files after the runtime ownership they contain: `attempt_run`, `planner_run`, `work_items`, `work_items_run`, `workflow_run`, `iteration_run`. |
| Work item execution | Use `work_items_run` for worker wave execution and settlement. Do not use `work_dag`, `plan_dag`, `node`, `stage`, or `orchestrator` names for this owner. |
| Plan shape | Use `WorkItemSpec` for planner-authored work items. Do not introduce `PlanWorkItem`; the ownership is already clear from `PlanOutcome.work_items`. |
| Terminal text | Use one `outcome` field for terminal natural-language payloads. Do not split it into `answer`, `summary`, `user_result`, `work_result`, or `review_summary`. |
| Success/failure | Use lifecycle enums and terminal `SubmissionStatus`. Do not introduce boolean aliases such as `has_structured_outcome`, `is_successful`, `status: bool`, or `is_success: bool`. |
| Dependencies | Use `needs` for direct work item dependencies. Do not add `direct_needs` or `direct_need_outcomes`; directness is part of the `needs` contract. |
| Worker assignment | Use `agent_name: AgentName` on `WorkItemSpec`. Do not use `agent_profile_name` or `assigned_work_item`. |
| Task mapping | Use deterministic `worker_task_id(attempt_id, work_item_id)`. Do not persist `worker_task_by_work_item_id`. |

## 3. Resulting File And Folder Structure

Target structure under `agent-core`:

```text
agent-core/crates/eos-types/src/
  contracts/
    record.rs                    # root, workflow-planner, workflow-worker, parented record subjects
    workflow.rs                  # WorkflowApi + attempt submission API
  state/
    request_task/
      task.rs                    # TaskRole::Root, TaskRole::Worker
    tools/
      submissions.rs             # PlanOutcomeSubmission, WorkerOutcomeSubmission
    workflow/
      workflow.rs                # Workflow lifecycle DTOs
      iteration.rs               # Iteration lifecycle DTOs
      attempt.rs                 # Attempt lifecycle DTOs
      work_item.rs               # WorkItemId, WorkItemSpec, worker_task_id
      outcome.rs                 # PlanOutcome, WorkItemOutcome, AttemptOutcome helpers

agent-core/crates/eos-workflow/src/
  attempt/
    attempt_run.rs               # thin attempt coordinator
    active_attempt_runs.rs        # in-process planner abort / active attempt handles
    planner_run.rs               # planner launch and planner settlement
    work_items.rs                # plan validation, task materialization, readiness helpers
    work_items_run.rs            # worker waves, worker settlement, missing outcome synthesis
  context/
    planner_first_attempt.rs
    planner_retry.rs
    planner_continuation.rs
    worker_context.rs
    render.rs
  attempt_submission.rs          # submit_plan_outcome + submit_worker_outcome adapter
  workflow_run.rs                # start/check/cancel delegated workflow lifecycle
  iteration_run.rs               # retry/continuation lifecycle

agent-core/crates/eos-tool/src/
  model.rs                       # ToolName submit_* rename set
  registry.rs
  tools/
    terminal.rs                  # RootTask, Plan, Worker, Advisor, Subagent
    submission/
      mod.rs
      support.rs
      submit_root_task_outcome.rs
      submit_plan_outcome.rs
      submit_worker_outcome.rs
      submit_advisor_outcome.rs
      submit_subagent_outcome.rs

.eos-agents/profile/
  main/
    root.md
    planner.md
    executor.md                  # worker-capable profile; context_recipe = worker
  helper/
    advisor.md
  subagent/
    subagent.md

.eos-agents/tools/
  submit_root_task_outcome.md
  submit_plan_outcome.md
  submit_worker_outcome.md
  submit_advisor_outcome.md
  submit_subagent_outcome.md
```

Remove the old mechanism-oriented file names from the target workflow path:

| Delete / replace | Reason |
| --- | --- |
| `attempt/orchestrator.rs` | Too broad; split into attempt, planner, and worker run ownership. |
| `attempt/orchestrator_registry.rs` | Rename to active attempt run ownership. |
| `attempt/plan_dag.rs` | Names a data structure, not workflow ownership. |
| `attempt/run_stage.rs` | Names a stage, not the worker-run owner. |
| `context/engine.rs` | Too generic; split role renderers. |
| `state/projections.rs` | Too generic; outcome aggregation and context rendering are separate concerns. |
| `tools/submission.rs` | Too large; terminal tools should live in per-tool files named after wire tools. |

## 4. Files And Concepts To Delete

Delete files:

```text
.eos-agents/profile/main/reducer.md
.eos-agents/skills/reducer/
.eos-agents/tools/submit_generator_outcome.md
.eos-agents/tools/submit_reducer_outcome.md
.eos-agents/tools/submit_planner_outcome.md
.eos-agents/tools/submit_root_outcome.md
.eos-agents/tools/submit_advisor_feedback.md
.eos-agents/tools/submit_subagent_result.md
```

Replace with:

```text
.eos-agents/tools/submit_worker_outcome.md
.eos-agents/tools/submit_plan_outcome.md
.eos-agents/tools/submit_root_task_outcome.md
.eos-agents/tools/submit_advisor_outcome.md
.eos-agents/tools/submit_subagent_outcome.md
```

Delete or rewrite code concepts:

| Current concept | Target |
| --- | --- |
| `TaskRole::Planner` | delete from TaskStore roles |
| `TaskRole::Generator` | `TaskRole::Worker` |
| `TaskRole::Reducer` | delete |
| `WorkflowTaskRole::{Planner, Generator, Reducer}` | replace with record subjects that distinguish attempt-owned planner records from task-owned worker records |
| `WorkflowNodeId` | delete |
| `ExecutionRole::Generator` | delete |
| `ExecutionRole::Reducer` | delete |
| `PlanTask` | `WorkItemSpec` |
| `PlanReducer` | delete |
| `GeneratorId` | `WorkItemId` |
| `ReducerId` | delete |
| `PlannerId` | delete |
| `PlannerPlan.planner_task_id` | delete |
| `PlannerPlan.disposition` | delete |
| `PlannerPlan.tasks` | `PlanOutcome.work_items` |
| `PlannerPlan.task_specs` | inline `work_spec` on each work item |
| `PlannerPlan.reducers` | delete |
| `MaterializedPlan.generator_task_ids` | delete; derive worker task ids from `(AttemptId, WorkItemId)` |
| `MaterializedPlan.reducer_task_ids` | delete |
| `GeneratorSubmission` | `WorkerOutcomeSubmission` |
| `ReducerSubmission` | delete |
| `ExecutionTaskOutcome` | delete for workflow evidence; use worker task status plus `WorkItemOutcome` |

## 5. IDs

| ID | Action | Reason |
| --- | --- | --- |
| `PlannerId` | remove | Planner launch is attempt runtime; `AttemptId` is enough. |
| `GeneratorId` | replace with `WorkItemId` | The planner authors work items, not generators. |
| `ReducerId` | remove | Reducer role is deleted. |
| planner `TaskId` | remove | Planner must not create a TaskStore row. |
| reducer `TaskId` | remove | Reducer rows no longer exist. |
| worker `TaskId` | keep | TaskStore still owns persisted worker rows. |
| `WorkItemId` | add | Workflow-local id authored by the planner and used in `needs`. |
| `AttemptId` | keep | Owns planner launch and attempt aggregation. |
| `IterationId` | keep | Owns attempt aggregation. |
| `WorkflowId` | keep | Owns iteration aggregation. |

Worker task ids are deterministic:

```rust
pub fn worker_task_id(attempt_id: &AttemptId, work_item_id: &WorkItemId) -> TaskId;
```

Do not persist a separate `worker_task_by_work_item_id` map. If a mapping is
needed, derive it from `(AttemptId, WorkItemId)` or load the worker task rows.

## 6. Target Contracts

Use the following target contracts. These intentionally avoid duplicated
success/status booleans.

```rust
pub struct WorkItemSpec {
    /// Planner-authored workflow-local id.
    pub id: WorkItemId,
    /// Selected worker-capable agent profile name.
    pub agent_name: AgentName,
    /// Executable work instruction for this item.
    pub work_spec: String,
    /// Direct work item dependencies. These are context edges, not shortcuts.
    pub needs: Vec<WorkItemId>,
}

pub struct PlanOutcome {
    /// Owning workflow attempt. PlanOutcome has no task_id and no work_item_id.
    pub attempt_id: AttemptId,
    /// Planner description of the work item plan.
    pub plan_spec: String,
    /// Planner-authored work item plan. Each item includes its own work_spec.
    pub work_items: Vec<WorkItemSpec>,
    /// Concrete current-iteration goal items to carry into the next iteration.
    /// None means this plan covers the current iteration goal.
    pub deferred_goal_for_next_iteration: Option<String>,
}

pub struct WorkItemOutcome {
    /// Owning workflow attempt.
    pub attempt_id: AttemptId,
    /// Persisted TaskStore row for the worker run.
    pub task_id: TaskId,
    /// Planner-authored workflow-local work item id.
    pub work_item_id: WorkItemId,
    /// Worker terminal payload. The worker task's TaskStatus says success/failure.
    pub outcome: String,
}

pub struct AttemptOutcome {
    /// Owning attempt.
    pub attempt_id: AttemptId,
    /// Planner output. None means the planner never submitted a plan.
    pub plan_outcome: Option<PlanOutcome>,
    /// Worker terminal payloads from this attempt.
    pub work_item_outcomes: Vec<WorkItemOutcome>,
}
```

Iteration and workflow records should keep lifecycle state in
`IterationStatus`/`WorkflowStatus` and carry attempt ids/outcomes as needed for
read-side rendering. Do not introduce separate `IterationOutcome.status`,
`IterationOutcome.is_success`, `WorkflowOutcome.status`, or
`WorkflowOutcome.is_success` booleans.

Root, advisor, and subagent terminal payloads should not create new workflow
outcome structs. They remain terminal payloads on their existing task/parented
run records.

## 7. Model-Facing Tool Contracts

Terminal tools should keep the model-facing payload small.

### `submit_root_task_outcome`

Input:

```rust
pub struct SubmitRootTaskOutcomeInput {
    pub status: SubmissionStatus,
    pub outcome: String,
}
```

The terminal maps `SubmissionStatus` onto the root task's `TaskStatus` and stores
the flattened terminal payload on the task run. It does not produce a workflow
outcome DTO.

### `submit_plan_outcome`

Input:

```rust
pub struct SubmitPlanOutcomeInput {
    pub plan_spec: String,
    #[serde(default)]
    pub deferred_goal_for_next_iteration: Option<String>,
    pub work_items: Vec<WorkItemSpecInput>,
}

pub struct WorkItemSpecInput {
    pub id: String,
    pub agent_name: String,
    pub work_spec: String,
    #[serde(default)]
    pub needs: Vec<String>,
}
```

Internal submission:

```rust
pub struct PlanOutcomeSubmission {
    pub attempt_id: AttemptId,
    pub plan_spec: String,
    pub work_items: Vec<WorkItemSpec>,
    pub deferred_goal_for_next_iteration: Option<String>,
    pub terminal_payload: JsonObject,
}
```

Rules:

- A model-submitted plan creates `PlanOutcome`.
- A planner failure to return creates no `PlanOutcome`; attempt failure state
  records the failure.
- `PlanOutcomeSubmission` has no `task_id`.
- `PlanOutcomeSubmission` has no `status`, no `is_success`, and no
  `submission_kind`.
- Terminal result metadata must include `attempt_id` and
  `has_deferred_goal_for_next_iteration`; it must not include `disposition`.

Model JSON:

```json
{
  "plan_spec": "Implement and verify the migration in focused worker items.",
  "deferred_goal_for_next_iteration": null,
  "work_items": [
    {
      "id": "w1",
      "agent_name": "executor",
      "work_spec": "Replace generator/reducer workflow DTOs with worker DTOs.",
      "needs": []
    }
  ]
}
```

### `submit_worker_outcome`

Input:

```rust
pub struct SubmitWorkerOutcomeInput {
    pub status: SubmissionStatus,
    pub outcome: String,
}
```

Internal submission:

```rust
pub struct WorkerOutcomeSubmission {
    pub attempt_id: AttemptId,
    pub task_id: TaskId,
    pub work_item_id: WorkItemId,
    pub status: SubmissionStatus,
    pub outcome: String,
    pub terminal_payload: JsonObject,
}
```

Rules:

- The terminal maps `SubmissionStatus` onto the worker task's `TaskStatus`.
- A worker failure to return is synthesized by workflow runtime as a failed
  worker task with an outcome payload explaining the missing terminal submission.
- The worker outcome is keyed by both persisted `task_id` and planner-authored
  `work_item_id`.
- Terminal result metadata should include `attempt_id`, `task_id`, and
  `work_item_id`; it must not include `submission_kind`.

Model JSON:

```json
{
  "status": "success",
  "outcome": "Implemented the assigned change and verified it with cargo check."
}
```

### `submit_advisor_outcome`

Input:

```rust
pub struct SubmitAdvisorOutcomeInput {
    pub verdict: AdvisorVerdict,
    pub outcome: String,
}
```

This renames `submit_advisor_feedback` without adding a workflow outcome DTO.

### `submit_subagent_outcome`

Input:

```rust
pub struct SubmitSubagentOutcomeInput {
    pub outcome: String,
}
```

This renames `submit_subagent_result` without adding a workflow outcome DTO. Do
not keep a half-typed `findings`/`references` shape in this cleanup.

## 8. Workflow Submission API

Replace the current three-method attempt submission API with two methods:

```rust
#[async_trait]
pub trait WorkflowAttemptSubmissionApi: Send + Sync {
    async fn submit_plan_outcome(
        &self,
        submission: PlanOutcomeSubmission,
    ) -> Result<SubmissionAck, CoreError>;

    async fn submit_worker_outcome(
        &self,
        submission: WorkerOutcomeSubmission,
    ) -> Result<SubmissionAck, CoreError>;
}
```

Delete:

```rust
async fn apply_plan(&self, plan: PlannerPlan) -> Result<SubmissionAck, CoreError>;
async fn submit_generator(&self, submission: GeneratorSubmission) -> Result<SubmissionAck, CoreError>;
async fn apply_reducer(&self, submission: ReducerSubmission) -> Result<SubmissionAck, CoreError>;
```

## 9. Tool Name Diff

| Current | Target | Action |
| --- | --- | --- |
| `submit_root_outcome` | `submit_root_task_outcome` | rename |
| `submit_planner_outcome` | `submit_plan_outcome` | rename and remove planner task id |
| `submit_generator_outcome` | `submit_worker_outcome` | replace |
| `submit_reducer_outcome` | none | delete |
| `submit_advisor_feedback` | `submit_advisor_outcome` | rename |
| `submit_subagent_result` | `submit_subagent_outcome` | rename |

Terminal tool enum target:

```rust
pub enum TerminalTool {
    RootTask,
    Plan,
    Worker,
    Advisor,
    Subagent,
}
```

`ToolName::ALL` should shrink by one terminal entry after deleting reducer:

```text
SubmitRootTaskOutcome
SubmitPlanOutcome
SubmitWorkerOutcome
SubmitAdvisorOutcome
SubmitSubagentOutcome
```

## 10. Work Item Plan Contract

The planner submits a work item plan.

Rules:

- Work item ids are unique within the attempt.
- `agent_name` is required and must resolve to a worker-capable agent profile.
- `work_spec` is required and nonblank.
- `needs` may reference only work item ids in the same plan.
- `needs` are direct context inputs, not scheduling shortcuts.
- A worker receives only the outcomes of the work items listed in its own
  `needs`; transitive ancestors are not included unless listed directly.
- At least one work item is required.
- There is no special sink node. A work item with no downstream dependents is a
  valid leaf and contributes directly to the attempt result.

This is the key simplification: every leaf worker is already a reducer for its
own branch. Attempt success is derived from worker task statuses, not from a
separate reducer row.

## 11. Outcome Aggregation Rules

Attempt:

- If the planner fails to return, `PlanOutcome` is absent and the attempt closes
  failed.
- If the planner returns a plan, the attempt stores one `PlanOutcome`.
- Each launched worker that returns a terminal submission records one
  `WorkItemOutcome` and updates its worker task status.
- If a worker fails to return, workflow runtime records a failed worker task and
  a synthesized `WorkItemOutcome` explaining the missing terminal submission.
- The attempt passes only when every required worker task is `Done`.
- The attempt fails when any required worker task is `Failed`, `Blocked`, or
  `Cancelled`, or when worker readiness reaches a failed quiescent state.

Iteration:

- Iteration status is derived from the accepted terminal attempt.
- Keep attempt ids/outcomes available so retry history is inspectable.

Workflow:

- Workflow status is derived from the latest terminal iteration and whether a
  deferred goal requires another iteration.
- Workflow rendering defaults to the latest iteration only.

## 12. Context Recipe Design

The context system should render role-specific text directly from workflow
state. Do not add a public `ContextOutcomeSlice`/`ContextOutcomeView` contract.

Target recipes:

```rust
pub enum ContextRole {
    Planner,
    Worker,
}

pub enum ContextScope {
    Planner {
        workflow_id: WorkflowId,
        iteration_id: IterationId,
        attempt_id: AttemptId,
    },
    Worker {
        workflow_id: WorkflowId,
        iteration_id: IterationId,
        attempt_id: AttemptId,
        task_id: TaskId,
        work_item_id: WorkItemId,
    },
}
```

The recipe router must validate that `recipe_id = "planner"` is only used with
`ContextRole::Planner` and `recipe_id = "worker"` is only used with
`ContextRole::Worker`.

### Planner Recipe

Planner scope has no task identity:

```rust
ContextScope::Planner {
    workflow_id,
    iteration_id,
    attempt_id,
}
```

Planner context is built from the workflow/iteration/attempt lifecycle position,
not from a planner task row.

Planner context should always include:

- `<workflow_goal>`: original delegated workflow goal.
- `<current_iteration_goal>`: the current iteration goal.

Planner context may include exactly one of these evidence groups:

- `<previous_attempts>`: retry evidence from failed attempts in the current
  iteration.
- `<latest_iteration>`: continuation evidence from the latest successful
  previous iteration.

Planner context should not include:

- planner task rows,
- reducer outcomes,
- full historical workflow summaries,
- all old iterations by default,
- `disposition`.

Planner directive:

```text
Create one work item plan for the current iteration goal. Submit exactly one
plan outcome. Use deferred_goal_for_next_iteration only for concrete current
iteration goal items intentionally carried into the next iteration.
```

### Worker Recipe

Worker scope has both persisted task identity and planner-authored work item
identity:

```rust
ContextScope::Worker {
    workflow_id,
    iteration_id,
    attempt_id,
    task_id,
    work_item_id,
}
```

Worker context should include:

- `<plan_spec>`: the planner's plan-level explanation.
- `<work_item>`: this worker's `work_item_id`, `task_id`, and `work_spec`.
- `<needs>`: direct dependency outcomes only.

Worker context should not include:

- the full work item plan unless needed for orientation,
- transitive dependency outcomes,
- unrelated sibling work items,
- reducer-specific guidance,
- workflow lifecycle decisions.

Worker directive:

```text
Complete <work_item> using <plan_spec> and direct <needs>. Submit exactly one
worker outcome.
```

Worker render shape:

```xml
<context role="worker">
  <plan_spec>
    The planner-level explanation of how this attempt is structured.
  </plan_spec>
  <needs>
    <work_item id="w1" task_id="task_...">
      <outcome>Direct dependency outcome.</outcome>
    </work_item>
  </needs>
  <work_item id="w2" task_id="task_...">
    <agent_name>executor</agent_name>
    <work_spec>The exact instruction for this worker only.</work_spec>
  </work_item>
</context>
```

Direct-needs example:

```text
w1 -> w2 -> w3
```

If `w3.needs = ["w2"]`, worker `w3` sees `w2` only. It does not see `w1`
unless the planner explicitly sets:

```json
{
  "id": "w3",
  "needs": ["w1", "w2"]
}
```

Filtering belongs in `eos-workflow/src/context/*`, not in stores. Stores return
complete records; context rendering decides what the agent should see.

## 13. Implementation Migration Phases

### Phase 1 - Types, DB, And Records

- Add `WorkItemId`, `WorkItemSpec`, `PlanOutcome`, `WorkItemOutcome`,
  `AttemptOutcome`, and submission structs.
- Remove `PlannerId`, `GeneratorId`, `ReducerId`, `PlanDisposition`,
  `ExecutionRole`, and `ExecutionTaskOutcome` from the workflow target state.
- Replace task roles with `TaskRole::Root` and `TaskRole::Worker`.
- Update `eos-db` attempt rows/repositories to remove planner/generator/reducer
  columns and persist the new attempt state.
- Update `eos-agent-run` record creation so planner records are attempt-owned and
  worker records are task-owned.

Verification:

```text
cd agent-core && cargo check -p eos-types --all-targets
cd agent-core && cargo check -p eos-db --all-targets
cd agent-core && cargo check -p eos-agent-run --all-targets
```

### Phase 2 - Tools

- Rename terminal tool names and model-facing docs.
- Delete reducer terminal tool registration.
- Replace generator terminal registration with worker terminal registration.
- Split terminal submission implementation into per-tool files.
- Update terminal descriptors and advisor guidance.

Verification:

```text
cd agent-core && cargo check -p eos-tool --all-targets
```

### Phase 3 - Workflow Runtime

- Remove planner TaskStore row creation.
- Replace `PlannerLaunch` task identity with attempt identity only.
- Replace `GeneratorLaunch` with `WorkerLaunch`.
- Delete `ReducerLaunch`.
- Rewrite plan materialization to create one TaskStore worker row per work item.
- Rewrite worker readiness over deterministic `worker_task_id`.
- Rewrite worker run settlement and missing terminal synthesis.

Verification:

```text
cd agent-core && cargo test -p eos-workflow attempt -- --nocapture
```

### Phase 4 - Outcome Aggregation

- Replace generator/reducer execution evidence with `PlanOutcome` and
  `WorkItemOutcome`.
- Close attempts from worker task statuses.
- Remove workflow summary projection.
- Update workflow closure logic to read worker leaves directly.

Verification:

```text
cd agent-core && cargo test -p eos-workflow iteration -- --nocapture
cd agent-core && cargo test -p eos-workflow service -- --nocapture
```

### Phase 5 - Context Recipes

- Replace generator/reducer context roles with worker.
- Render planner context from iteration outcomes and current failed attempts.
- Render worker context from `plan_spec`, current work item, and direct needs
  outcomes.
- Update `.eos-agents/profile/main/planner.md` and
  `.eos-agents/profile/main/executor.md`.

Verification:

```text
cd agent-core && cargo test -p eos-workflow context -- --nocapture
```

### Phase 6 - Cleanup Gate

- Delete reducer files, generated references, stale snapshots, and stale docs.
- Remove `Generator*`, `Reducer*`, and reducer-specific profile/tool docs.
- Regenerate class inventory only after source cleanup is complete.

Verification:

```text
cd agent-core && cargo check --workspace --all-targets
cd agent-core && cargo test --workspace
rg "Generator|generator|Reducer|reducer|submit_generator_outcome|submit_reducer_outcome|disposition|submission_kind|has_structured_outcome|is_successful|worker_task_by_work_item_id" agent-core .eos-agents docs
```

Remaining matches must be either historical migration docs or explicit
compatibility notes scheduled for deletion.

## 14. Acceptance Criteria

- No reducer TaskStore row can be created.
- No planner TaskStore row can be created.
- No model-facing tool named `submit_generator_outcome`,
  `submit_reducer_outcome`, or `submit_planner_outcome` is registered.
- No target workflow DTO contains duplicated `status: bool` / `is_success`
  outcome fields.
- `submit_worker_outcome` records one `WorkItemOutcome` and updates the worker
  task status.
- `submit_plan_outcome` records one `PlanOutcome` with no `task_id`.
- Worker task ids are derived from `(AttemptId, WorkItemId)`.
- Attempt pass/fail is derived from worker task statuses.
- Worker context contains `plan_spec`, current work item, and direct needs
  outcomes.
- Planner context contains current iteration scope and compact prior evidence.
- Terminal result metadata contains no `disposition` and no `submission_kind`.
