# Workflow Reducer Removal And Worker Outcome Migration - SPEC

Status: Proposed
Date: 2026-06-09
Owner: eos-workflow / eos-types / eos-tool / agent profiles
Scope:
- `agent-core/crates/eos-types`
- `agent-core/crates/eos-workflow`
- `agent-core/crates/eos-tool`
- `.eos-agents/profile`
- `.eos-agents/tools`
- `.eos-agents/skills`

## 1. Intent

This is an aggressive cleanup migration. The target removes the reducer role as
a first-class row, converts generator terminology to worker terminology, and
collapses terminal outcome handling around one workflow worker outcome.

The workflow model becomes:

```text
WorkflowOutcome
  -> IterationOutcome[]
      -> AttemptOutcome[]
          -> PlanOutcome
          -> WorkItemOutcome[]
```

Root work remains isolated from workflow work:

```text
RootTaskOutcome
```

The planner is attempt control-plane runtime. It must not have a `TaskId` or
`WorkItemId`. Workflow workers are persisted as TaskStore rows and are also
keyed by planner-authored `WorkItemId`s.

## 2. Decisions

| Area | Decision |
| --- | --- |
| Reducer role | Delete. There are no reducer rows, reducer tasks, reducer launches, reducer outcomes, reducer context recipes, or reducer terminal tools. |
| Generator role | Rename to worker. The implementation and contracts use `Worker` / `WorkItem`; no `Generator` public contract remains. |
| Agent name | Keep `agent_name` on work items. This is the selected agent profile name, not a task id. |
| Planner identity | Planner launch is keyed by `AttemptId`; planner has no `TaskId`, no `WorkItemId`, and no TaskStore row. |
| Plan payload | `plan_spec` plus `work_items`; no `task_specs`, no `reducers`, no `disposition`. |
| Work item payload | Each work item carries its own `work_spec`; do not use `task_spec` in workflow planning contracts. |
| Outcome status | `status: bool` means the agent/tool returned a structured outcome. It does not mean the work succeeded. |
| Domain success | `is_success: bool` means the agent's domain decision for root/workflow/work-item success. |
| Workflow summary | `WorkflowOutcome` has no `summary`. It returns iteration outcomes. |
| Context size | Worker context includes the plan spec and direct-needs work item outcomes only. Workflow context renderers show only the latest iteration outcome by default. |

Do not introduce these names:

- `PlanWorkItem`
- `disposition`
- `submission_kind`
- planner `task_id`
- planner `work_item_id`
- workflow `task_specs`
- workflow reducer compatibility aliases

## 3. Resulting File And Folder Structure

Target structure under `agent-core`:

```text
agent-core/crates/eos-types/src/
  contracts/
    workflow.rs                  # PlanOutcome input contracts, WorkItemSpec, WorkflowAttemptSubmissionApi
    record.rs                    # Workflow record roles updated from generator/reducer to planner/worker
  state/
    request_task/
      task.rs                    # TaskRole::Root, TaskRole::Worker only for TaskStore rows
    tools/
      submissions.rs             # PlanOutcomeSubmission, WorkerOutcomeSubmission
    workflow/
      attempt.rs                 # AttemptOutcome stored on closed attempts
      entity.rs
      iteration.rs               # IterationOutcome
      outcomes.rs                # RootTaskOutcome, WorkItemOutcome, PlanOutcome, AttemptOutcome, IterationOutcome, WorkflowOutcome helpers
      plan.rs                    # MaterializedPlan with work_item_task_ids only

agent-core/crates/eos-workflow/src/
  attempt/
    launch.rs                    # PlannerLaunch + WorkerLaunch; no ReducerLaunch
    orchestrator.rs              # record_plan_outcome + record_worker_outcome
    orchestrator_registry.rs
    plan_dag.rs                  # work item DAG validation
    run_stage.rs                 # planner launch then worker waves
  context/
    composer.rs
    engine.rs                    # planner + worker recipes
    scope.rs                     # ContextScope::Planner, ContextScope::Worker
    section.rs                   # ContextRole::Planner, ContextRole::Worker
    xml.rs
  lifecycle.rs
  iteration.rs
  service.rs
  state.rs
  state/
    projections.rs               # project outcomes to IterationOutcome/WorkflowOutcome
  submission.rs                  # submit_plan_outcome + submit_worker_outcome adapter

agent-core/crates/eos-tool/src/
  model.rs                       # ToolName submit_* rename set
  registry.rs
  tools/
    submission.rs                # root, plan, worker, advisor, subagent terminals
    terminal.rs                  # Root, Plan, Worker, Advisor, Subagent

.eos-agents/profile/
  main/
    root.md
    planner.md
    executor.md                  # worker-capable profile; keep agent name executor unless separately renamed
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

The `executor` profile can remain the default worker agent. The migration should
remove generator wording from that profile, change its `context_recipe` to
`worker`, and change its terminal to `submit_worker_outcome`.

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
| `ExecutionRole::Generator` | delete |
| `ExecutionRole::Reducer` | delete |
| `PlanTask` | `WorkItemSpec` |
| `PlanReducer` | delete |
| `GeneratorId` | `WorkItemId` |
| `ReducerId` | delete |
| `PlannerId` | delete unless only used as launch-local runtime id; prefer `AttemptId` |
| `PlannerPlan.planner_task_id` | delete |
| `PlannerPlan.disposition` | delete |
| `PlannerPlan.tasks` | `PlanOutcome.work_items` |
| `PlannerPlan.task_specs` | inline `work_spec` on each work item |
| `PlannerPlan.reducers` | delete |
| `MaterializedPlan.generator_task_ids` | `MaterializedPlan.work_item_task_ids` |
| `MaterializedPlan.reducer_task_ids` | delete |
| `GeneratorSubmission` | `WorkerOutcomeSubmission` |
| `ReducerSubmission` | delete |
| `ExecutionTaskOutcome` | `WorkItemOutcome` for workflow execution evidence |

## 5. IDs To Remove And Add

| ID | Action | Reason |
| --- | --- | --- |
| `PlannerId` | remove | Planner launch is attempt runtime; `AttemptId` is enough. |
| `GeneratorId` | replace with `WorkItemId` | The planner authors work items, not generators. |
| `ReducerId` | remove | Reducer role is deleted. |
| planner `TaskId` | remove | Planner must not create a TaskStore row. |
| reducer `TaskId` | remove | Reducer rows no longer exist. |
| worker `TaskId` | keep | TaskStore still owns persisted agent-run task rows. |
| `WorkItemId` | add | Workflow-local id authored by the planner and used in `needs`. |
| `AttemptId` | keep | Owns planner launch and attempt aggregation. |
| `IterationId` | keep | Owns attempt aggregation. |
| `WorkflowId` | keep | Owns iteration aggregation. |

## 6. Class Contracts

Use the following target contracts. Comments are part of the contract and should
be carried into public DTOs or schema docs where these types are exposed.

```rust
pub struct RootTaskOutcome {
    /// True when the root worker returned this structured outcome. False when
    /// the runtime synthesized a missing-outcome failure.
    pub status: bool,
    /// Root worker decision: whether the user/root task succeeded.
    pub is_success: bool,
    /// Persisted root task row.
    pub task_id: TaskId,
    /// User-facing final result or concrete blocker.
    pub summary: String,
}

pub struct WorkItemOutcome {
    /// True when the workflow worker returned this structured outcome. False
    /// when the runtime synthesized a missing-outcome failure.
    pub status: bool,
    /// Worker decision: whether the assigned work item succeeded.
    pub is_success: bool,
    /// Persisted TaskStore row for the worker run.
    pub task_id: TaskId,
    /// Planner-authored workflow-local work item id.
    pub work_item_id: WorkItemId,
    /// Factual work result, verification evidence, or concrete blocker.
    pub summary: String,
}

pub struct PlanOutcome {
    /// True when the planner returned this structured plan outcome. False when
    /// the runtime synthesized a missing-plan failure.
    pub status: bool,
    /// Owning workflow attempt. PlanOutcome has no task_id and no work_item_id.
    pub attempt_id: AttemptId,
    /// Natural-language description of the plan for worker context.
    pub plan_spec: String,
    /// Planner-authored work item DAG. Each item includes its own work_spec.
    pub work_items: Vec<WorkItemSpec>,
    /// Concrete current-iteration goal items to carry into the next iteration.
    /// None means this plan covers the current iteration goal.
    pub deferred_goal_for_next_iteration: Option<String>,
}

pub struct AdvisorOutcome {
    /// True when the advisor returned this structured outcome. False when the
    /// runtime synthesized a missing-outcome failure.
    pub status: bool,
    /// Advisor decision about the reviewed terminal payload.
    pub verdict: AdvisorVerdict,
    /// Concrete review findings.
    pub summary: String,
}

pub struct SubagentOutcome {
    /// True when the subagent returned this structured outcome. False when the
    /// runtime synthesized a missing-outcome failure.
    pub status: bool,
    /// Concrete answer to the subagent goal.
    pub summary: String,
}

pub struct AttemptOutcome {
    /// True when the attempt produced a structured aggregate outcome. False
    /// when the runtime synthesized a missing-attempt failure.
    pub status: bool,
    /// Attempt-level decision derived from plan and work item outcomes.
    pub is_success: bool,
    /// Owning attempt.
    pub attempt_id: AttemptId,
    /// Planner output or synthesized planner failure.
    pub plan_outcome: PlanOutcome,
    /// Worker outcomes from this attempt.
    pub work_item_outcomes: Vec<WorkItemOutcome>,
}

pub struct IterationOutcome {
    /// True when the iteration produced a structured aggregate outcome. False
    /// when the runtime synthesized a missing-iteration failure.
    pub status: bool,
    /// Iteration-level decision derived from its attempts.
    pub is_success: bool,
    /// Owning iteration.
    pub iteration_id: IterationId,
    /// Attempt outcomes in this iteration.
    pub attempt_outcomes: Vec<AttemptOutcome>,
}

pub struct WorkflowOutcome {
    /// True when the workflow produced a structured aggregate outcome. False
    /// when the runtime synthesized a missing-workflow failure.
    pub status: bool,
    /// Workflow-level decision derived from the latest iteration outcome.
    pub is_success: bool,
    /// Owning workflow.
    pub workflow_id: WorkflowId,
    /// Iteration outcomes. The context renderer includes only the latest
    /// iteration by default to control prompt size.
    pub iteration_outcomes: Vec<IterationOutcome>,
}
```

Supporting contracts:

```rust
pub struct WorkItemSpec {
    /// Planner-authored workflow-local id.
    pub id: WorkItemId,
    /// Agent profile name selected for this work item.
    pub agent_name: String,
    /// Executable work instruction for this item.
    pub work_spec: String,
    /// Direct work item dependencies. These are context edges, not shortcuts.
    pub needs: Vec<WorkItemId>,
}

pub enum AdvisorVerdict {
    Approve,
    Reject,
}
```

## 7. Model-Facing Tool Contracts

### `submit_root_task_outcome`

Input:

```rust
pub struct SubmitRootTaskOutcomeInput {
    /// Root worker decision: whether the user/root task succeeded.
    pub is_success: bool,
    /// User-facing final result or concrete blocker.
    pub summary: String,
}
```

Output recorded:

```rust
pub struct RootTaskOutcome {
    pub status: bool,
    pub is_success: bool,
    pub task_id: TaskId,
    pub summary: String,
}
```

The tool call itself sets `status = true`. If the root run fails to return, the
runtime synthesizes `RootTaskOutcome { status: false, is_success: false, ... }`.

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

The accepted internal submission is:

```rust
pub struct PlanOutcomeSubmission {
    pub attempt_id: AttemptId,
    pub status: bool,
    pub plan_spec: String,
    pub work_items: Vec<WorkItemSpec>,
    pub deferred_goal_for_next_iteration: Option<String>,
    pub terminal_payload: JsonObject,
}
```

Rules:

- `status` is not model input. The tool call sets `status = true`.
- A planner failure to return is synthesized by workflow runtime with
  `status = false`.
- `PlanOutcomeSubmission` has no `task_id`.
- `PlanOutcomeSubmission` has no `submission_kind`.
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
    /// Worker decision: whether the assigned work item succeeded.
    pub is_success: bool,
    /// Free-text summary of the completed or failed work item.
    pub summary: String,
}
```

Internal submission:

```rust
pub struct WorkerOutcomeSubmission {
    pub attempt_id: AttemptId,
    pub task_id: TaskId,
    pub work_item_id: WorkItemId,
    pub is_success: bool,
    pub summary: String,
    pub terminal_payload: JsonObject,
}
```

Rules:

- `status` is not model input. The tool call sets `status = true`.
- A worker failure to return is synthesized by workflow runtime with
  `status = false` and `is_success = false`.
- The worker outcome is keyed by both persisted `task_id` and planner-authored
  `work_item_id`.
- Terminal result metadata should include `attempt_id`, `task_id`, and
  `work_item_id`; it must not include `submission_kind`.

Model JSON:

```json
{
  "is_success": true,
  "summary": "Implemented the assigned change and verified it with cargo check."
}
```

### `submit_advisor_outcome`

Input:

```rust
pub struct SubmitAdvisorOutcomeInput {
    pub verdict: AdvisorVerdict,
    pub summary: String,
}
```

Recorded outcome:

```rust
pub struct AdvisorOutcome {
    pub status: bool,
    pub verdict: AdvisorVerdict,
    pub summary: String,
}
```

The rename from `submit_advisor_feedback` to `submit_advisor_outcome` makes the
terminal naming consistent with the other submit tools.

### `submit_subagent_outcome`

Input:

```rust
pub struct SubmitSubagentOutcomeInput {
    pub summary: String,
}
```

Recorded outcome:

```rust
pub struct SubagentOutcome {
    pub status: bool,
    pub summary: String,
}
```

The old `findings` and `references` arrays should be folded into `summary`
unless a future typed subagent evidence contract is designed. Do not keep a
half-typed shape in this cleanup.

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

## 10. Plan DAG Contract

The planner submits a work item DAG.

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
  valid leaf and contributes directly to the attempt outcome.

This is the key simplification: every leaf worker is already a reducer for its
own branch. Attempt success is derived from worker outcomes, not from a separate
reducer row.

## 11. Outcome Aggregation Rules

Attempt outcome:

- `status = true` when workflow runtime can produce an `AttemptOutcome`.
- `is_success = true` when the plan returned successfully and all required work
  item outcomes have `status = true` and `is_success = true`.
- If the planner fails to return, synthesize `PlanOutcome.status = false` and
  `AttemptOutcome.is_success = false`.
- If any launched worker fails to return, synthesize a failed
  `WorkItemOutcome`.

Iteration outcome:

- `status = true` when workflow runtime can produce an `IterationOutcome`.
- `is_success = true` when the accepted terminal attempt for the iteration is
  successful.
- Keep attempt outcomes in the iteration outcome so retry history is inspectable
  without a separate summary.

Workflow outcome:

- `status = true` when workflow runtime can produce a `WorkflowOutcome`.
- `is_success = true` when the latest terminal iteration succeeds and there is no
  deferred goal requiring a next iteration.
- `iteration_outcomes` replaces summary. Context rendering defaults to the
  latest iteration only.

## 12. Context Recipe Design

The context engine is a recipe router plus a projection layer:

```text
ContextScope + recipe_id + persisted workflow state -> AgentContext
```

The workflow stores comprehensive outcomes once. The context engine must not
mutate or truncate persisted `WorkflowOutcome`, `IterationOutcome`, or
`AttemptOutcome` records. It renders role-specific context slices from those
complete outcome trees.

Core rule:

```text
Persist comprehensive outcome.
Render only the slice needed by the current agent role and lifecycle moment.
```

The target context system has two workflow recipes:

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
- `<latest_iteration_outcome>`: continuation evidence from the latest successful
  previous iteration.

Planner context should not include:

- planner task rows,
- reducer outcomes,
- full historical workflow summaries,
- all old iterations by default,
- `disposition`.

The planner prompt must keep this distinction clear:

```text
previous_attempts = retry evidence inside the same iteration
latest_iteration_outcome = continuation evidence from the previous iteration
current_iteration_goal = the authoritative scope for this planner
```

Planner directive:

```text
Create one work item DAG for the current iteration goal. Submit exactly one
plan outcome. Use deferred_goal_for_next_iteration only for concrete current
iteration goal items intentionally carried into the next iteration.
```

#### Planner First Attempt

For the first attempt in the first iteration, render only the workflow and
iteration scope:

```xml
<context role="planner">
  <workflow_goal>...</workflow_goal>
  <current_iteration_goal>...</current_iteration_goal>
</context>
```

There is no prior attempt evidence and no prior iteration evidence.

#### Planner Retry After Attempt Failure

When a new planner launches after a failed attempt in the same iteration, the
iteration goal does not change. The retry planner sees the same
`current_iteration_goal` plus filtered failed-attempt evidence.

Render:

```xml
<context role="planner">
  <workflow_goal>...</workflow_goal>
  <current_iteration_goal>...</current_iteration_goal>
  <previous_attempts>
    <attempt id="attempt_1">
      <status>true</status>
      <is_success>false</is_success>
      <plan_outcome status="true">
        <plan_spec>...</plan_spec>
        <deferred_goal_for_next_iteration>...</deferred_goal_for_next_iteration>
      </plan_outcome>
      <worker_evidence>
        <work_item id="w_leaf_ok" task_id="task_...">
          <status>true</status>
          <is_success>true</is_success>
          <summary>Reusable successful leaf outcome.</summary>
        </work_item>
        <work_item id="w_failed" task_id="task_...">
          <status>true</status>
          <is_success>false</is_success>
          <summary>Concrete worker failure.</summary>
        </work_item>
        <work_item id="w_missing" task_id="task_...">
          <status>false</status>
          <is_success>false</is_success>
          <summary>Worker did not return a structured outcome.</summary>
        </work_item>
      </worker_evidence>
    </attempt>
  </previous_attempts>
</context>
```

For each failed previous attempt, include:

- `attempt_id`, `status`, and `is_success`.
- the full `plan_spec` if the planner returned.
- `PlanOutcome.status`; if `status = false`, no worker outcomes exist.
- `deferred_goal_for_next_iteration`, if the failed attempt had returned one.
- successful leaf worker outcomes that are reusable.
- failed worker outcomes.
- missing/non-returned worker outcomes synthesized by runtime.
- successful direct needs of failed workers only when needed to explain the
  failure.

Do not include every successful internal worker by default. A successful
internal worker appears only when it is a leaf, or when it is a direct need of a
failed worker and helps explain the failure.

Leaf definition:

```text
leaf worker = a work item that no other work item lists in needs
```

Examples:

```text
w1 -> w2 -> w3
```

If `w3` failed, retry context includes `w3` and may include `w2` as the direct
need that `w3` received. It does not include `w1` unless `w1` is also directly
relevant.

```text
w1 -> w2
w3
```

If `w2` succeeded and `w3` succeeded, both are leaf outcomes and can be shown as
reusable successful evidence.

If the planner itself failed to return:

```text
PlanOutcome.status = false
work_item_outcomes = []
AttemptOutcome.is_success = false
```

The next planner sees that planner-return failure as retry evidence, not as an
empty successful plan.

#### Planner For A Second Iteration

When the previous iteration succeeded but returned
`deferred_goal_for_next_iteration`, workflow lifecycle creates a new iteration.
The second iteration planner sees continuation evidence, not retry evidence.

Render:

```xml
<context role="planner">
  <workflow_goal>original delegated workflow goal</workflow_goal>
  <latest_iteration_outcome>
    <iteration id="iteration_1">
      <status>true</status>
      <is_success>true</is_success>
      <plan_spec>optional previous plan header</plan_spec>
      <worker_evidence>
        <work_item id="w_leaf_1" task_id="task_...">
          <status>true</status>
          <is_success>true</is_success>
          <summary>Successful leaf result from the previous iteration.</summary>
        </work_item>
      </worker_evidence>
    </iteration>
  </latest_iteration_outcome>
  <current_iteration_goal>
    previous deferred_goal_for_next_iteration
  </current_iteration_goal>
</context>
```

For the latest previous iteration, include:

- the latest successful iteration outcome only.
- successful leaf worker outcomes from the successful terminal attempt.
- enough ids to reference the prior evidence.
- the previous plan spec as an optional compact header when it helps interpret
  the leaf outcomes.

Do not include by default:

- failed attempts from the previous iteration,
- every internal successful worker,
- older iterations before the latest previous iteration,
- a full workflow summary,
- lifecycle closure decisions.

This rule keeps the continuation planner focused. Prior iteration outcomes are
evidence; the new `current_iteration_goal` is the scope.

Planner selection matrix:

| Planner launch | Include |
| --- | --- |
| First attempt, first iteration | `workflow_goal`, `current_iteration_goal` |
| Same-iteration retry | failed previous attempts: full `plan_spec`, successful leaf outcomes, failed/missing worker outcomes, relevant direct needs |
| Second iteration | latest successful previous iteration: successful leaf outcomes, optional previous `plan_spec`, current deferred goal |

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
- `<assigned_work>`: this worker's `work_item_id`, `task_id`, and `work_spec`.
- `<needs>`: direct dependency outcomes only.

Worker context should not include:

- the full plan DAG unless needed for orientation,
- transitive dependency outcomes,
- unrelated sibling work items,
- reducer-specific guidance,
- workflow lifecycle decisions.

Worker directive:

```text
Complete <assigned_work> using <plan_spec> and direct <needs>. Submit exactly
one worker outcome.
```

Worker render shape:

```xml
<context role="worker">
  <plan_spec>
    The planner-level explanation of how this attempt is structured and why this
    work item exists.
  </plan_spec>
  <needs>
    <work_item id="w1" task_id="task_...">
      <status>true</status>
      <is_success>true</is_success>
      <summary>Direct dependency outcome summary.</summary>
    </work_item>
  </needs>
  <assigned_work id="w2" task_id="task_...">
    <agent_name>executor</agent_name>
    <work_spec>The exact instruction for this worker only.</work_spec>
  </assigned_work>
</context>
```

Worker filter:

```text
include:
- plan_outcome.plan_spec
- plan_outcome.work_items[current_work_item_id]
- work_item_outcomes where work_item_id in current_work_item.needs

exclude:
- sibling work items
- transitive ancestors not directly listed in needs
- previous attempts
- workflow lifecycle decisions
- the full attempt outcome tree
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

### Outcome Projection API

Store complete outcomes, then project them for a context role:

```rust
pub enum ContextOutcomeView {
    WorkerNeeds {
        attempt_id: AttemptId,
        work_item_id: WorkItemId,
    },
    PlannerRetry {
        iteration_id: IterationId,
        current_attempt_id: AttemptId,
    },
    PlannerContinuation {
        workflow_id: WorkflowId,
        current_iteration_id: IterationId,
    },
    WorkflowLatest {
        workflow_id: WorkflowId,
    },
}

pub struct ContextOutcomeSlice {
    pub workflow_goal: Option<String>,
    pub current_iteration_goal: Option<String>,
    pub plan_spec: Option<String>,
    pub assigned_work: Option<WorkItemSpec>,
    pub needs: Vec<WorkItemOutcome>,
    pub previous_attempts: Vec<AttemptOutcomeForContext>,
    pub latest_iteration_outcome: Option<IterationOutcomeForContext>,
}

pub fn project_outcomes_for_context(
    workflow_outcome: &WorkflowOutcome,
    view: ContextOutcomeView,
) -> ContextOutcomeSlice;
```

The projection structs should be smaller than the persisted structs. They are
prompt DTOs, not durable state.

Suggested projection fields:

```rust
pub struct AttemptOutcomeForContext {
    pub attempt_id: AttemptId,
    pub status: bool,
    pub is_success: bool,
    pub plan_status: bool,
    pub plan_spec: Option<String>,
    pub deferred_goal_for_next_iteration: Option<String>,
    pub reusable_leaf_outcomes: Vec<WorkItemOutcome>,
    pub failed_or_missing_outcomes: Vec<WorkItemOutcome>,
    pub relevant_need_outcomes: Vec<WorkItemOutcome>,
}

pub struct IterationOutcomeForContext {
    pub iteration_id: IterationId,
    pub status: bool,
    pub is_success: bool,
    pub plan_spec: Option<String>,
    pub successful_leaf_outcomes: Vec<WorkItemOutcome>,
}
```

Filtering belongs in the context engine or a context projection module, not in
the stores. Stores return complete records; context projection decides what the
agent should see.

## 13. Implementation Migration Phases

### Phase 1 - Types And Tools

- Add `WorkItemId`, `WorkItemSpec`, outcome structs, and submission structs.
- Rename terminal tool names and model-facing docs.
- Delete reducer terminal tool registration.
- Replace generator terminal registration with worker terminal registration.
- Update terminal descriptors and advisor guidance.

Verification:

```text
cd agent-core && cargo check -p eos-types --all-targets
cd agent-core && cargo check -p eos-tool --all-targets
```

### Phase 2 - Workflow Lifecycle

- Remove planner TaskStore row creation.
- Replace `PlannerLaunch` task identity with attempt identity only.
- Replace `GeneratorLaunch` with `WorkerLaunch`.
- Delete `ReducerLaunch`.
- Rewrite plan materialization to create one TaskStore worker row per work
  item.
- Rewrite scheduler readiness over `work_item_id -> task_id`.

Verification:

```text
cd agent-core && cargo test -p eos-workflow attempt -- --nocapture
```

### Phase 3 - Outcome Aggregation

- Replace `ExecutionTaskOutcome` projections with `WorkItemOutcome`.
- Add `AttemptOutcome`, `IterationOutcome`, and `WorkflowOutcome` projections.
- Remove workflow summary projection.
- Update workflow closure logic to read worker leaves directly.

Verification:

```text
cd agent-core && cargo test -p eos-workflow iteration -- --nocapture
cd agent-core && cargo test -p eos-workflow service -- --nocapture
```

### Phase 4 - Context Recipes

- Replace generator/reducer context roles with worker.
- Render planner context from iteration outcomes and current failed attempts.
- Render worker context from `plan_spec`, assigned work, and direct needs
  outcomes.
- Update `.eos-agents/profile/main/planner.md` and
  `.eos-agents/profile/main/executor.md`.

Verification:

```text
cd agent-core && cargo test -p eos-workflow context -- --nocapture
```

### Phase 5 - Cleanup Gate

- Delete reducer files, generated references, stale snapshots, and stale docs.
- Remove `Generator*`, `Reducer*`, and reducer-specific profile/tool docs.
- Regenerate class inventory only after source cleanup is complete.

Verification:

```text
cd agent-core && cargo check --workspace --all-targets
cd agent-core && cargo test --workspace
rg "Generator|generator|Reducer|reducer|submit_generator_outcome|submit_reducer_outcome|disposition|submission_kind" agent-core .eos-agents docs
```

Remaining matches must be either historical migration docs or explicit
compatibility notes scheduled for deletion.

## 14. Acceptance Criteria

- No reducer TaskStore row can be created.
- No planner TaskStore row can be created.
- No model-facing tool named `submit_generator_outcome`,
  `submit_reducer_outcome`, or `submit_planner_outcome` is registered.
- `submit_worker_outcome` records one `WorkItemOutcome`.
- `submit_plan_outcome` records one `PlanOutcome` with no `task_id`.
- `WorkflowOutcome` contains iteration outcomes and no summary.
- `IterationOutcome` contains attempt outcomes.
- `AttemptOutcome` contains `PlanOutcome` plus `Vec<WorkItemOutcome>`.
- Worker context contains `plan_spec`, assigned work, and direct needs outcomes.
- Planner context contains current iteration scope and compact prior evidence.
- Terminal result metadata contains no `disposition` and no `submission_kind`.
