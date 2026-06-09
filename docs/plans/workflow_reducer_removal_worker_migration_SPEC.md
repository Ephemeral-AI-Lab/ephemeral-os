# Workflow Reducer Removal And Worker Migration - SPEC

Status: Proposed (rev 3)
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

> Consolidated reference: `workflow_reducer_removal_worker_migration_DESIGN.html`
> (class/field catalog, DB schema, and field-population diagrams).

## 0. Revision 3 — adopted model

Rev 3 keeps rev 2's core fold (delete reducer, generator → worker, two outcome
families) and supersedes the rev-2 points below. The structural decisions that
drive everything else:

1. **Opaque `task_id`.** Every `AgentType::Agent` run (root, planner, worker) gets
   a `task_id` minted by the **task store** inside `spawn_agent` — uniform across
   the three roles. The id does **not** encode `attempt_id`/`work_item_id` and is
   never parsed. This deletes the deterministic `worker_task_id(attempt_id,
   work_item_id)` / `planner_task_id(attempt_id)` derivation, the `{attempt}:gen:` /
   `{attempt}:red:` encoding, the `generator_id_from_task_id` / `reducer_id_from_task_id`
   reverse parsers, and `WorkflowNodeId`. (Supersedes rev-2 §5 deterministic ids.)

2. **`plan_id` + `AttemptExecutionTree`.** The `Attempt` mints a `plan_id` at
   creation. The planner authors `work_item_id`s in `submit_plan_outcome`. The
   attempt carries an `AttemptExecutionTree` — `{ plan_id, planner_task_id?,
   nodes:[{ work_item_id, needs, task_id? }] }` — materialized on
   `submit_plan_outcome`, with each `task_id` bound when its run is spawned. **The
   tree is the attempt↔task index**: worker tasks are inferred from it, not from a
   `tasks.attempt_id` column.

3. **Lean, lineage-free `Task`.** `Task` is pure execution state
   (`id, request_id, role, instruction, status, agent_name, task_outcome`).
   `attempt_id`, `workflow_id`, `iteration_id`, `needs`, `work_item_id`, and
   `outcomes` are all **dropped** from the task store; the attempt↔task linkage is
   the `AttemptExecutionTree` and lineage derives tree → attempt → iteration →
   workflow. (Supersedes rev-2, which kept `attempt_id` on `Task`.)

4. **Full plan in `TaskOutcome::Planner` JSON (approach A).** The plan (`plan_spec`
   + `work_items` + `deferred_goal_for_next_iteration`) lives only in the planner
   run's terminal result (`terminal_payload` / typed `task_outcome`). There is
   **no** `work_items` table and no attempt plan copy. The `execution_tree`
   materializes the node ids + edges + bindings for scheduling.

5. **`is_pass` on root/worker outcomes.** `TaskOutcome::Root` and
   `TaskOutcome::Worker` carry `is_pass: bool` + `outcome: String`; `TaskOutcome::Planner`
   carries the plan (no `is_pass` — a returned plan is success by construction). The
   terminal records `is_pass` **and** maps it onto `TaskStatus` (Done/Failed) so the
   two stay consistent. (Supersedes rev-2's "pass/fail is never stored on an
   outcome.")

6. **Aggregation outcomes are recursive read-side projections** (computed, never
   stored): `AttemptOutcome`, `IterationOutcome`, `WorkflowOutcome` (§11). Each
   carries a rolled-up `status: bool`. They **replace** the lifecycle disposition
   enums (`IterationOutcome::{Complete,Continue,Failed}`,
   `WorkflowOutcome::{Succeeded,Failed,Cancelled}`); "continue vs complete" is
   `deferred_goal.is_some()`. The deferred goal's single source is
   `TaskOutcome::Planner.deferred_goal_for_next_iteration`, so the
   `iterations.deferred_goal_for_next_iteration` column is **dropped** (derived on
   next-iteration creation). The stored `*Status` enums remain the in-flight /
   scheduling authority. (Supersedes rev-2's "no status on aggregation projections.")

7. **Schema renames.** `workflows.launched_by_agent_run_id → parent_agent_run_id`,
   `workflows.goal → workflow_goal`; `iterations.goal → iteration_goal` plus a
   denormalized `iterations.workflow_goal` (context locality).

Carried unchanged from rev 2: the planner keeps its own task-anchored run; two
outcome families split by `AgentType` (`TaskOutcome` for `Agent`, `ParentedOutcome`
for `Subagent`/`Advisor`) — **do not merge them**; delete `MaterializedPlan`,
`PlanDisposition`, `ExecutionRole`, `ExecutionTaskOutcome`, `PlanOutcome`,
`WorkItemOutcome`; collapse the context layer; the single natural-language field is
`outcome`.

## 1. Intent

This is an aggressive cleanup migration. The target removes reducer as a workflow
role, converts generator terminology to worker terminology, removes the binary
generator/reducer execution model, and unifies every terminal payload under one
`TaskOutcome` type per family.

The workflow model becomes:

```text
Workflow
  -> Iteration[]
      -> Attempt[]   (mints plan_id; owns an AttemptExecutionTree)
          -> planner run   (TaskOutcome::Planner = full plan)
          -> worker run[]  (TaskOutcome::Worker)
```

Every terminal agent run is a `task_runs` row carrying the terminal tool result
as both the raw agent payload (`terminal_payload`) and typed `task_outcome`; the
`task_id` is opaque (store-minted at spawn). The plan lives
in the planner's `TaskOutcome::Planner`; the attempt's `execution_tree` maps each
authored `work_item_id` to the opaque `task_id` of the worker that ran it.

The question "did it succeed?" is answered two ways: in-flight by the lifecycle
state, and as a terminal roll-up by `is_pass` on the leaf outcomes:

```text
TaskStatus · AttemptStatus · IterationStatus · WorkflowStatus   (in-flight / scheduling)
TaskOutcome::{Root,Worker}.is_pass                              (terminal pass/fail of a run)
AttemptOutcome/IterationOutcome/WorkflowOutcome.status          (read-side roll-up)
```

Root, advisor, and subagent terminal payloads are also outcome variants on their own
rows; advisor/subagent use `ParentedOutcome` on `parented_runs`.

## 2. Decisions

| Area | Decision |
| --- | --- |
| Reducer role | Delete. No reducer rows, tasks, launches, outcomes, context recipes, terminal tools, or reducer profile/skill files. |
| Generator role | Replace with worker. Public workflow contracts use `Worker` / `WorkItem`; no `Generator` public contract remains. |
| Planner identity | Planner keeps its task-anchored run (recording anchor) but is never a worker-DAG member. The planner run's `task_id` is opaque and lives in `execution_tree.planner_task_id`. The model never sends a planner task id. |
| Task id | **Opaque, store-minted at `spawn_agent`**, uniform for root/planner/worker. Not composed, not parsed. No `worker_task_id`/`planner_task_id` derivation, no `WorkflowNodeId`, no reverse parsers. |
| Plan id | `plan_id` is minted by the `Attempt` at creation (the plan's identity). |
| Attempt↔task linkage | The `AttemptExecutionTree` (on the attempt) maps `work_item_id → task_id` and holds `planner_task_id`. Worker tasks are inferred from it. `Task`/`task_runs` carry **no** `attempt_id`. |
| Task shape | `Task` is pure execution state: `id, request_id, role, instruction, status, agent_name, task_outcome: Option<TaskOutcome>`. No lineage, `needs`, `work_item_id`, or `outcomes`. |
| Plan payload | Full plan (`plan_spec` + `work_items` + `deferred_goal_for_next_iteration`) in the planner run's `TaskOutcome::Planner`. No `work_items` table, no attempt plan copy, no `task_specs`, no `reducers`, no `disposition`. |
| Work item payload | Each `WorkItemSpec` carries its own `work_spec` (the worker's instruction), `agent_name`, and `needs`. |
| Outcome model | Per-run: `TaskOutcome {Root,Planner,Worker}` (`Agent` rows) and `ParentedOutcome {Advisor,Subagent}` (parented rows). `Task` exposes only `task_outcome`; `AgentRun` / `ParentedRun` keep the raw terminal payload and the typed outcome (`task_outcome` / `parented_outcome`) side by side. Delete `PlanOutcome`, `WorkItemOutcome`, `ExecutionTaskOutcome`, and `Task.outcomes`. |
| Success/failure | `is_pass: bool` on `TaskOutcome::{Root,Worker}` (model-reported, also mapped onto `TaskStatus`). Aggregation `status: bool` is a read-side roll-up (§11). Stored `*Status` enums remain the in-flight authority. Do not add `is_successful`, `is_success`, or `has_structured_outcome`. |
| Outcome text | One `outcome: String` field on the run variants (`Root`, `Worker`, `Advisor`, `Subagent`). The `Planner` variant has no free-text `outcome`; its body is `plan_spec` + `work_items` + `deferred_goal_for_next_iteration`. |
| Aggregation | `AttemptOutcome`/`IterationOutcome`/`WorkflowOutcome` are recursive read-side projections; they replace the lifecycle disposition enums. Not stored. |
| Context data | No public context projection DTOs. Filtering is local to `eos-workflow` context render functions. |
| Record paths | Planner and worker records are run-owned. `format_record_dir` / `finish_task_run` use lineage from the spawn context (the ephemeral launch carries `WorkflowCoordinates`), not from a task column. |

Do not introduce these names:

- `PlanWorkItem`
- `disposition`, `submission_kind`, `PlanDisposition`
- planner `work_item_id`
- workflow `task_specs`
- workflow reducer compatibility aliases
- `has_structured_outcome`, `is_successful`, `is_success`
- `user_result`, `work_result`, `review_summary`, `answer`, `work_instruction`
- `direct_needs`, `direct_need_outcomes`, `assigned_work_item`, `agent_profile_name`
- `ContextOutcomeSlice`, `ContextOutcomeView`, `AttemptOutcomeForContext`, `IterationOutcomeForContext`
- `WorkflowNodeId`, `MaterializedPlan`, `ExecutionRole`, `ExecutionTaskOutcome`
- `PlanOutcome` / `WorkItemOutcome` (folded into `TaskOutcome`)
- `worker_task_id` / `planner_task_id` **derivation functions**, `generator_id_from_task_id`,
  `reducer_id_from_task_id`, and any `{attempt}:{...}` task-id encoding
- `tasks.attempt_id` / `tasks.workflow_id` / `tasks.iteration_id` (lineage columns on the task store)

`AttemptOutcome` / `IterationOutcome` / `WorkflowOutcome` are **allowed** as read-side
projection types (§11) — not stored DTOs and not lifecycle disposition enums.

Naming rules:

| Surface | Rule |
| --- | --- |
| Workflow files | Name files after the runtime ownership they contain: `attempt_run`, `planner_run`, `work_items`, `work_items_run`, `workflow_run`, `iteration_run`. |
| Work item execution | Use `work_items_run` for worker wave execution and settlement. Do not use `work_dag`, `plan_dag`, `node`, `stage`, or `orchestrator` names for this owner. |
| Plan shape | Use `WorkItemSpec` for planner-authored work items. Do not introduce `PlanWorkItem`. |
| Outcome type | Two per-family enums: `TaskOutcome {Root, Planner, Worker}` and `ParentedOutcome {Advisor, Subagent}`. `AgentRun` / `ParentedRun` retain the terminal payload while exposing typed outcome fields beside it. Do not merge the two families. |
| Terminal text | The single natural-language field is `outcome`, everywhere. Rename every `summary` / `failure_summary` / `review_summary` / `answer` / `user_result` / `work_result` to `outcome`. |
| Success/failure | `is_pass: bool` on `TaskOutcome::{Root,Worker}`; aggregation `status: bool`; lifecycle `*Status` enums for scheduling. Model-facing inputs carry `SubmissionStatus` (maps onto `is_pass` + `TaskStatus`). |
| Dependencies | Use `needs` for direct work item dependencies (on `WorkItemSpec` and `ExecutionNode`). Do not add `direct_needs` or `direct_need_outcomes`. |
| Worker assignment | Use `agent_name: AgentName` on `WorkItemSpec`. Do not use `agent_profile_name` or `assigned_work_item`. |
| Deferred goal | Typed `DeferredGoal` newtype on internal contracts; single stored source is `TaskOutcome::Planner.deferred_goal_for_next_iteration`. Raw `String` only on the model-facing `*Input` wire DTOs. |
| Task mapping | The attempt↔task and work_item↔task bindings live in `AttemptExecutionTree`. Do not persist `attempt_id` on the task store and do not derive task ids. |

## 3. Resulting File And Folder Structure

Target structure under `agent-core`:

```text
agent-core/crates/eos-types/src/
  contracts/
    record.rs                    # TaskAgentRunKind; WorkflowTaskRole = {Planner, Worker};
                                 #   SpawnAgentTarget::Workflow { coords, role, plan_id, work_item_id? }
    workflow.rs                  # WorkflowApi + 2-method attempt submission API
  state/
    request_task/
      task.rs                    # TaskRole::{Root, Planner, Worker}; TaskStatus::is_terminal;
                                 #   lean Task with typed task_outcome (no lineage / needs / work_item_id / outcomes)
    tools/
      submissions.rs             # PlanOutcomeSubmission, WorkerOutcomeSubmission, SubmissionStatus
    workflow/
      workflow.rs                # Workflow lifecycle DTO + WorkflowStatus (was entity.rs)
      iteration.rs               # Iteration lifecycle DTO + IterationStatus
      attempt.rs                 # AttemptState, AttemptClosure, AttemptStatus, Attempt,
                                 #   AttemptExecutionTree, ExecutionNode, AttemptBudget
      work_item.rs               # WorkItemId, PlanId, WorkItemSpec, DeferredGoal
      outcome.rs                 # TaskOutcome {Root,Planner,Worker}; ParentedOutcome {Advisor,Subagent};
                                 #   AdvisorVerdict; read-side AttemptOutcome/IterationOutcome/WorkflowOutcome
  # Task-id minting lives in the existing TaskAgentRunStore surface (stores.rs); no eos-workflow ids.

agent-core/crates/eos-workflow/src/
  attempt/
    attempt_run.rs               # one attempt's lifecycle (start/close/asserts)
    active_attempt_runs.rs       # in-process active attempt handles + OpenIterationCoordinatorRegistry home
    planner_run.rs               # planner launch + settle + record plan + materialize execution_tree + RUN handoff
    work_items.rs                # PURE sync: plan validation residual + DAG readiness over the execution tree
    work_items_run.rs            # worker waves, worker settlement, missing-outcome synthesis, lazy spawn
    launch.rs                    # AgentLaunch (struct+kind), AgentLaunchFactory, AgentRunner, AttemptResources
  context/
    planner_context.rs           # all planner cases (one "exactly one of" match)
    worker_context.rs            # plan_spec + needs + work_item (+ dependency rendering)
    render.rs                    # recipe dispatch + xml/section render; homes AgentContext/ContextSection/ContextRole
    composer.rs                  # AgentEntryComposer (skill/terminal-block plumbing)
    scope.rs                     # ContextScope::{Planner, Worker}
  workflow_run.rs                # WorkflowApi start/check/cancel + create/close_workflow (absorbs starter.rs + lifecycle.rs)
  iteration_run.rs               # coordinator + retry + continuation + handle_iteration_closed
  attempt_submission.rs          # submit_plan_outcome + submit_worker_outcome adapter
  config.rs                      # WorkflowLifecycleConfig (relocated from the deleted ids.rs)
  # DELETED: ids.rs, state.rs (inline mod projections), attempt/{orchestrator,run_stage,plan_dag}.rs,
  #          context/engine.rs, starter.rs, lifecycle.rs, submission.rs (renamed)

agent-core/crates/eos-tool/src/
  model.rs                       # ToolName submit_* rename set (6 -> 5 terminals)
  registry.rs
  tools/
    terminal.rs                  # TerminalTool::{RootTask, Plan, Worker, Advisor, Subagent}
    submission/
      mod.rs
      support.rs                 # SubmissionStatus (wire), OutcomeInput, shared helpers
      submit_root_task_outcome.rs
      submit_plan_outcome.rs
      submit_worker_outcome.rs
      submit_advisor_outcome.rs
      submit_subagent_outcome.rs

agent-core/crates/eos-db/         # edit 0001_initial.sql in place (contingent on no deployed DB)
  # workflows: launched_by_agent_run_id -> parent_agent_run_id; goal -> workflow_goal; drop outcomes
  # iterations: goal -> iteration_goal; ADD workflow_goal; drop outcomes; drop deferred_goal_for_next_iteration
  # attempts: ADD plan_id, execution_tree; drop planner_task_id, generator_task_ids, reducer_task_ids,
  #           outcomes, deferred_goal
  # task_runs / tasks: task_id OPAQUE; role IN ('root','planner','worker'); drop attempt_id, workflow_id,
  #           iteration_id, work_item_id, needs, outcomes (+ the coordinate indexes)
  # rows.rs: drop MaterializedPlan reconstruction + 'generator'/'reducer' parsing + outcome normalizer;
  #          tasks drop terminal_tool_result in favor of task_outcome;
  #          AgentRun / ParentedRun keep terminal_payload plus typed outcome mirrors
```

Remove the old mechanism-oriented file names from the target workflow path:

| Delete / replace | Reason |
| --- | --- |
| `attempt/orchestrator.rs` | Too broad; split into attempt, planner, and worker run ownership. |
| `attempt/orchestrator_registry.rs` | Rename to `active_attempt_runs.rs`. |
| `attempt/plan_dag.rs` | Names a data structure, not workflow ownership; folds into `work_items.rs`. |
| `attempt/run_stage.rs` | Names a stage, not the worker-run owner; folds into `work_items_run.rs`. |
| `context/engine.rs` | Too generic; split into `render.rs` + role renderers. |
| `state.rs` (inline `mod projections`) | Dissolves: the reducer-gate filter is deleted; aggregation becomes the read-side projections in `outcome.rs` + render. |
| `ids.rs` | Dissolves: task ids are store-minted (no derivation); `WorkflowLifecycleConfig` moves to `config.rs`. |
| `tools/submission.rs` | Too large; one per-tool file named after each wire tool. |
| `starter.rs` / `lifecycle.rs` | Fold into `workflow_run.rs` (same owner). |

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
| `TaskRole::Planner` | keep (planner has a run; never a worker-DAG member) |
| `TaskRole::Generator` | `TaskRole::Worker` |
| `TaskRole::Reducer` | delete |
| `WorkflowTaskRole::{Planner, Generator, Reducer}` | `WorkflowTaskRole::{Planner, Worker}` (record-path label) |
| `WorkflowNodeId` | delete (spawn target carries `WorkflowTaskRole` + `plan_id` + `work_item_id?`) |
| `workflow_task_id(attempt, WorkflowNodeId)` / `generator_task_id` / `reducer_task_id` | delete (task ids are opaque store mints) |
| `generator_id_from_task_id` / `reducer_id_from_task_id` (reverse parsers) | delete |
| `ExecutionRole` / `ExecutionTaskOutcome` | delete (use `TaskOutcome` + `is_pass`) |
| `Task.outcomes` / `Task.attempt_id` / `Task.workflow_id` / `Task.iteration_id` / `Task.needs` | delete (lean Task; linkage via `AttemptExecutionTree`) |
| `MaterializedPlan` | delete (plan in `TaskOutcome::Planner`; bindings in `AttemptExecutionTree`) |
| `PlanDisposition` | delete (use `Option<DeferredGoal>`) |
| `PlanTask` | `WorkItemSpec` |
| `PlanReducer` | delete |
| `GeneratorId` | `WorkItemId` |
| `ReducerId` / `PlannerId` | delete |
| `PlannerPlan.{planner_task_id, disposition, tasks, task_specs, reducers}` | `TaskOutcome::Planner { plan_spec, work_items, deferred_goal_for_next_iteration }` |
| `GeneratorSubmission` / `ReducerSubmission` | `WorkerOutcomeSubmission` |
| `PlannerSubmission` / `PlannerFailureSubmission` / `PlannerFailReason` | `PlanOutcomeSubmission`; planner failure is an attempt lifecycle transition |
| `IterationOutcome` / `WorkflowOutcome` lifecycle enums | read-side projections (§11) |
| `summary` / `failure_summary` / `review_summary` text fields | `outcome` |

## 5. IDs

| ID | Action | Reason |
| --- | --- | --- |
| `task_id` (root/planner/worker) | **opaque, store-minted at `spawn_agent`** | Uniform across roles; not composed, not parsed. |
| `PlannerId` / `GeneratorId` (as `generator_id`) / `ReducerId` | remove | Roles are task lineage; ids are not authored per role. |
| `WorkItemId` | add | Workflow-local id authored by the planner in `submit_plan_outcome`; used in `needs` and `execution_tree` nodes. |
| `PlanId` | add | Minted by the `Attempt` at creation (the plan's identity). |
| reducer `TaskId` | remove | Reducer rows no longer exist. |
| `AttemptId` / `IterationId` / `WorkflowId` | keep | Lifecycle aggregation keys (on the lifecycle rows, not on the task store). |

No deterministic task-id functions, no reverse parsers, no `{attempt}:{...}` encoding.
The attempt↔task and work_item↔task bindings are materialized in the
`AttemptExecutionTree`:

```rust
pub struct AttemptExecutionTree {
    pub plan_id: PlanId,
    pub planner_task_id: Option<TaskId>,    // bound when the planner is spawned
    pub nodes: Vec<ExecutionNode>,          // materialized on submit_plan_outcome
}
pub struct ExecutionNode {
    pub work_item_id: WorkItemId,
    pub needs: Vec<WorkItemId>,             // DAG edges
    pub task_id: Option<TaskId>,            // bound when this work item's worker is spawned
}
```

Enumerate an attempt's worker tasks from `execution_tree.nodes[].task_id`; find the
worker for `work_item_id` by node lookup. The only query that filtered tasks by
attempt (`latch_attempt_tasks_cancelled`) now reads task ids from the tree.

## 6. Target Contracts

```rust
pub struct WorkItemSpec {
    /// Planner-authored workflow-local id.
    pub id: WorkItemId,
    /// Selected worker-capable agent profile name.
    pub agent_name: AgentName,
    /// Executable work instruction (becomes the worker run's instruction).
    pub work_spec: String,
    /// Direct work item dependencies. Context edges, not scheduling shortcuts.
    pub needs: Vec<WorkItemId>,
}

/// WORKFLOW-TASK family outcome (AgentType::Agent). Stored as task_outcome JSON.
#[derive(Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum TaskOutcome {
    /// Root request result (user-facing).            (TaskRole::Root)
    Root { is_pass: bool, outcome: String },
    /// The planner's full plan (single source of truth for the attempt plan).
    ///                                               (TaskRole::Planner)
    Planner {
        plan_spec: String,
        work_items: Vec<WorkItemSpec>,
        /// Concrete current-iteration goal items carried to the next iteration.
        deferred_goal_for_next_iteration: Option<DeferredGoal>,
    },
    /// One worker's deliverable or blocker.          (TaskRole::Worker)
    Worker { is_pass: bool, outcome: String },
}

/// PARENTED family outcome (AgentType::{Subagent, Advisor}). Stored as parented_runs.parented_outcome.
#[derive(Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ParentedOutcome {
    Advisor  { verdict: AdvisorVerdict, outcome: String },
    Subagent { outcome: String },
}

pub enum AdvisorVerdict { Approve, Reject }   // promoted from the tool-private Verdict; homed in outcome.rs

/// Lean, lineage-free persisted task (Task store).
pub struct Task {
    pub id: TaskId,                      // opaque, store-minted
    pub request_id: RequestId,
    pub role: TaskRole,                  // Root | Planner | Worker
    pub instruction: String,             // universal runner input (worker = work_spec)
    pub status: TaskStatus,
    pub agent_name: Option<String>,
    pub task_outcome: Option<TaskOutcome>,
}

/// Root/planner/worker agent-run row. The raw terminal payload is kept for agent-run
/// compatibility; task_outcome is the typed mirror of that same terminal tool result.
pub struct AgentRun {
    pub task_id: TaskId,
    pub agent_run_id: AgentRunId,
    pub request_id: RequestId,
    pub role: TaskRole,                  // Root | Planner | Worker
    pub status: TaskStatus,
    pub agent_name: AgentName,
    pub terminal_payload: Option<JsonObject>,
    pub task_outcome: Option<TaskOutcome>,
    pub token_count: i64,
    pub error: Option<String>,
    pub created_at: UtcDateTime,
    pub updated_at: UtcDateTime,
    pub finished_at: Option<UtcDateTime>,
}

/// Advisor/subagent run row. Parented outcome is the typed mirror of the raw payload.
pub struct ParentedRun {
    pub task_id: TaskId,
    pub agent_run_id: AgentRunId,
    pub request_id: RequestId,
    pub status: TaskStatus,
    pub parent_agent_run_id: AgentRunId,
    pub parent_task_id: TaskId,
    pub kind: ParentedAgentRunKind,       // Advisor | Subagent
    pub tool_use_id: Option<ToolUseId>,
    pub agent_name: AgentName,
    pub terminal_payload: Option<JsonObject>,
    pub parented_outcome: Option<ParentedOutcome>,
    pub token_count: i64,
    pub error: Option<String>,
    pub created_at: UtcDateTime,
    pub updated_at: UtcDateTime,
    pub finished_at: Option<UtcDateTime>,
}

/// Attempt lifecycle. The plan lives in the planner's TaskOutcome::Planner; the
/// execution tree is the attempt↔task index. No MaterializedPlan, no planner_task_id field.
pub struct Attempt {
    pub id: AttemptId,
    pub iteration_id: IterationId,
    pub workflow_id: WorkflowId,         // denormalized; derivable via iteration_id
    pub attempt_sequence_no: i64,
    pub plan_id: PlanId,                 // minted at creation
    pub execution_tree: AttemptExecutionTree,
    pub state: AttemptState,
}

pub enum AttemptState {
    Planning { started: bool },          // started guards double-start (the planner_task_id lives in the tree)
    Running,
    Closed { closure: AttemptClosure },
}

pub enum AttemptClosure {                // no stored outcomes Vec
    Passed    { closed_at: UtcDateTime },
    Failed    { reason: AttemptFailReason, closed_at: UtcDateTime },
    Cancelled { reason: String, closed_at: UtcDateTime },
}
```

Aggregation outcomes are **read-side projections** (recursive; computed, not stored):

```rust
// PlannerOutcome ≡ TaskOutcome::Planner payload; WorkerOutcome ≡ TaskOutcome::Worker payload.
pub struct AttemptOutcome {
    pub status: bool,                        // plan returned AND every worker is_pass (empty workers => false)
    pub planner_outcome: PlannerOutcome,     // via execution_tree.planner_task_id
    pub worker_outcomes: Vec<WorkerOutcome>, // via execution_tree.nodes[].task_id
}
pub struct IterationOutcome {
    pub status: bool,                        // = returned_attempt.status
    pub deferred_goal: Option<DeferredGoal>, // = returned_attempt.planner_outcome.deferred_goal_for_next_iteration
    pub attempts: Vec<AttemptOutcome>,       // via iteration.attempt_ids
}
pub struct WorkflowOutcome {
    pub status: bool,                        // = returned_iteration.status
    pub iterations: Vec<IterationOutcome>,   // via workflow.iteration_ids
}
```

`"returned"` = the last attempt / iteration after which no further retry /
continuation was spawned (the terminal one). The stored `IterationStatus` /
`WorkflowStatus` / `AttemptStatus` enums remain the lifecycle authority for
scheduling; the projection `status` is the terminal pass/fail roll-up.

## 7. Model-Facing Tool Contracts

Each terminal keeps a small model-facing input, maps it onto the owning `TaskStatus`,
and records the raw terminal payload plus the equivalent typed outcome on the run.
The schedulable `Task` exposes only the typed `task_outcome`.

### `submit_root_task_outcome`

```rust
pub struct SubmitRootTaskOutcomeInput { pub status: SubmissionStatus, pub outcome: String }
```

Maps `SubmissionStatus` onto the root task `TaskStatus` (+ `RequestStatus`) and
records `TaskOutcome::Root { is_pass, outcome }` (`is_pass = status == Success`).

### `submit_plan_outcome`

```rust
pub struct SubmitPlanOutcomeInput {
    pub plan_spec: String,
    #[serde(default)]
    pub deferred_goal_for_next_iteration: Option<String>,   // wire String; -> DeferredGoal on ingest
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
    pub deferred_goal_for_next_iteration: Option<DeferredGoal>,
}
```

Rules:

- A model-submitted plan records `TaskOutcome::Planner` on the planner run and sets
  the planner `TaskStatus::Done`, **and materializes the attempt's
  `execution_tree.nodes`** from `work_items` (`work_item_id` + `needs`, `task_id = None`).
- A planner that fails to return creates no `TaskOutcome::Planner`; the runtime marks
  the planner task `Failed` (synthesized) and the attempt closes failed.
- `SubmitPlanOutcomeInput` has no `status` (a returned plan is success by
  construction) and the model never sends a task id.
- Terminal metadata may include `attempt_id` and
  `has_deferred_goal_for_next_iteration`; it must not include `disposition` or
  `submission_kind`.

Model JSON:

```json
{
  "plan_spec": "Implement and verify the migration in focused worker items.",
  "deferred_goal_for_next_iteration": null,
  "work_items": [
    { "id": "w1", "agent_name": "executor", "work_spec": "Replace generator/reducer DTOs with worker DTOs.", "needs": [] }
  ]
}
```

### `submit_worker_outcome`

```rust
pub struct SubmitWorkerOutcomeInput { pub status: SubmissionStatus, pub outcome: String }
```

Internal submission:

```rust
pub struct WorkerOutcomeSubmission {
    pub attempt_id: AttemptId,
    pub task_id: TaskId,
    pub work_item_id: WorkItemId,
    pub status: SubmissionStatus,   // maps onto is_pass + the worker task's TaskStatus
    pub outcome: String,
}
```

Rules:

- Records `TaskOutcome::Worker { is_pass, outcome }` and updates the worker
  `TaskStatus` (`is_pass = status == Success`).
- A worker that fails to return is synthesized by workflow runtime as a failed worker
  whose `TaskOutcome::Worker.outcome` explains the missing terminal.
- The worker is keyed by both opaque `task_id` and planner-authored `work_item_id`;
  the runtime knows both from the launch context.
- Terminal metadata may include `attempt_id`, `task_id`, `work_item_id`; it must not
  include `submission_kind`.

### `submit_advisor_outcome`

```rust
pub struct SubmitAdvisorOutcomeInput { pub verdict: AdvisorVerdict, pub outcome: String }
```

Records `ParentedOutcome::Advisor { verdict, outcome }`. Renames `submit_advisor_feedback`.

### `submit_subagent_outcome`

```rust
pub struct SubmitSubagentOutcomeInput { pub outcome: String }
```

Records `ParentedOutcome::Subagent { outcome }`. Renames `submit_subagent_result`.
Do not keep a half-typed `findings`/`references` shape.

## 8. Workflow Submission API

```rust
#[async_trait]
pub trait WorkflowAttemptSubmissionApi: Send + Sync {
    async fn submit_plan_outcome(&self, submission: PlanOutcomeSubmission)
        -> Result<SubmissionAck, CoreError>;

    async fn submit_worker_outcome(&self, submission: WorkerOutcomeSubmission)
        -> Result<SubmissionAck, CoreError>;
}
```

Delete `apply_plan` / `submit_generator` / `apply_reducer`.

## 9. Tool Name Diff

| Current | Target | Action |
| --- | --- | --- |
| `submit_root_outcome` | `submit_root_task_outcome` | rename |
| `submit_planner_outcome` | `submit_plan_outcome` | rename; no planner task id from the model |
| `submit_generator_outcome` | `submit_worker_outcome` | replace |
| `submit_reducer_outcome` | none | delete |
| `submit_advisor_feedback` | `submit_advisor_outcome` | rename |
| `submit_subagent_result` | `submit_subagent_outcome` | rename |

Terminal tool enum target:

```rust
pub enum TerminalTool { RootTask, Plan, Worker, Advisor, Subagent }
```

`ToolName::ALL` shrinks by one terminal after deleting reducer:

```text
SubmitRootTaskOutcome · SubmitPlanOutcome · SubmitWorkerOutcome · SubmitAdvisorOutcome · SubmitSubagentOutcome
```

## 10. Work Item Plan Contract

Rules:

- Work item ids are unique within the plan.
- `agent_name` is required and must resolve to a worker-capable (`AgentType::Agent`)
  profile.
- `work_spec` is required and nonblank.
- `needs` may reference only work item ids in the same plan.
- `needs` are direct context inputs, not scheduling shortcuts. A worker receives only
  the outcomes of the work items in its own `needs`; transitive ancestors are not
  included unless listed directly.
- At least one work item is required.
- There is no special sink node. A work item with no downstream dependents is a valid
  leaf and contributes directly to the attempt result.

Plan validation (`work_items.rs`) is the residual of the old `validate_plan_shape`:
unique work item ids, `needs` reference known ids, acyclic, at least one item.
**Deleted** reducer rules: the `>=1 reducer` requirement, reducer-needs rules, the
fixed-reducer-profile check, and the "dangling generator with no downstream"
rejection (leaves are now valid). `assert_acyclic` survives modulo the
`GeneratorId` -> `WorkItemId` rename and operating over `WorkItemSpec`/`ExecutionNode`
edges.

This is the key simplification: every leaf worker is already a reducer for its own
branch. Attempt success is derived from worker `is_pass`, not from a separate reducer
row.

## 11. Outcome Aggregation Rules

All aggregation outcomes are **read-side projections** (computed on demand from the
runs + the `AttemptExecutionTree`; never stored). Status rolls up bottom-up.

Attempt (`AttemptOutcome`):

- `planner_outcome` = the planner run's `TaskOutcome::Planner` (via
  `execution_tree.planner_task_id`).
- `worker_outcomes` = each node's worker `TaskOutcome::Worker` (via
  `execution_tree.nodes[].task_id`).
- `status` = the planner returned a plan **and** every worker `is_pass`. An attempt
  whose planner failed (no plan, no workers) is `status = false` — do not read the
  vacuously-true empty-`all()`.
- The attempt passes only when every required worker is `Done`; it fails when any is
  `Failed`/`Blocked`/`Cancelled` or worker readiness reaches a failed quiescent state
  (the existing `dag_resolution` "all Done" / quiescence logic, over the tree).

Iteration (`IterationOutcome`):

- `attempts` = the iteration's `AttemptOutcome`s (via `iteration.attempt_ids`).
- `status` = the "returned" (terminal) attempt's `status`.
- `deferred_goal` = that attempt's `planner_outcome.deferred_goal_for_next_iteration`
  (the single source). The next iteration's `iteration_goal` is derived from it; no
  stored iteration deferred-goal column.

Workflow (`WorkflowOutcome`):

- `iterations` = the workflow's `IterationOutcome`s (via `workflow.iteration_ids`).
- `status` = the "returned" (terminal) iteration's `status`.
- Workflow rendering defaults to the latest iteration only.

These replace the old `IterationOutcome`/`WorkflowOutcome` lifecycle disposition
enums. "Continue vs complete" for an iteration is `deferred_goal.is_some()`.

## 12. Context Recipe Design

Context renders role-specific text directly from workflow state + run `TaskOutcome`s.
No public `ContextOutcomeSlice`/`ContextOutcomeView` contract.

```rust
pub enum ContextRole { Planner, Worker }

pub enum ContextScope {
    Planner { workflow_id: WorkflowId, iteration_id: IterationId, attempt_id: AttemptId },
    Worker {
        workflow_id: WorkflowId,
        iteration_id: IterationId,
        attempt_id: AttemptId,
        task_id: TaskId,
        work_item_id: WorkItemId,
    },
}
```

The recipe router validates `recipe_id = "planner"` only with `ContextRole::Planner`
and `recipe_id = "worker"` only with `ContextRole::Worker`.

Context files (three render files): `planner_context.rs`, `worker_context.rs`,
`render.rs` (`composer.rs` and `scope.rs` are separate owners). The planner's
first-attempt / retry / continuation cases are one match in `planner_context.rs`.

### Planner Recipe

Planner context always includes `<workflow_goal>` and `<current_iteration_goal>`,
plus **exactly one** of:

- `<previous_attempts>`: retry evidence from failed attempts in the current
  iteration (read from those attempts' worker `TaskOutcome::Worker` via the tree).
- `<latest_iteration>`: continuation evidence from the latest successful previous
  iteration.

Planner directive:

```text
Create one work item plan for the current iteration goal. Submit exactly one
plan outcome. Use deferred_goal_for_next_iteration only for concrete current
iteration goal items intentionally carried into the next iteration.
```

### Worker Recipe

Worker context includes:

- `<plan_spec>`: the planner's plan-level explanation (from `TaskOutcome::Planner`).
- `<work_item>`: this worker's `work_item_id`, `task_id`, and `work_spec`.
- `<needs>`: direct dependency outcomes only — each need's worker
  `TaskOutcome::Worker.outcome`, resolved by mapping the `WorkItemId` edge through the
  attempt's `execution_tree` (`work_item_id -> task_id`).

Worker directive:

```text
Complete <work_item> using <plan_spec> and direct <needs>. Submit exactly one
worker outcome.
```

Worker render shape:

```xml
<context role="worker">
  <plan_spec>The planner-level explanation of how this attempt is structured.</plan_spec>
  <needs>
    <work_item id="w1" task_id="...">
      <outcome>Direct dependency outcome.</outcome>
    </work_item>
  </needs>
  <work_item id="w2" task_id="...">
    <agent_name>executor</agent_name>
    <work_spec>The exact instruction for this worker only.</work_spec>
  </work_item>
</context>
```

Direct-needs example `w1 -> w2 -> w3`: if `w3.needs = ["w2"]`, worker `w3` sees `w2`
only, not `w1`, unless the planner sets `"needs": ["w1", "w2"]`. Filtering lives in
`eos-workflow/src/context/*`, not in stores.

## 13. Implementation Migration Phases

### Phase 1 - Types, DB, And Records

- Add `WorkItemId`, `PlanId`, `WorkItemSpec`, `AttemptExecutionTree`, `ExecutionNode`,
  the `TaskOutcome` enum (with `is_pass`), `ParentedOutcome`, `AdvisorVerdict`, the
  read-side projection structs, and the submission structs.
- Delete `MaterializedPlan`, `PlanDisposition`, `ExecutionRole`, `ExecutionTaskOutcome`,
  `PlannerId`, `GeneratorId`, `ReducerId`, `WorkflowNodeId`, the deterministic task-id
  functions + reverse parsers, and `Task.{outcomes, attempt_id, workflow_id,
  iteration_id, needs, work_item_id}`; rename `is_terminal_generator -> is_terminal`.
- `TaskRole` -> `{Root, Planner, Worker}`; `WorkflowTaskRole` -> `{Planner, Worker}`.
- `eos-db` schema (edit `0001_initial.sql` in place; verify no deployed DB):
  - `workflows`: `launched_by_agent_run_id -> parent_agent_run_id`, `goal -> workflow_goal`, drop `outcomes`.
  - `iterations`: `goal -> iteration_goal`, add `workflow_goal`, drop `outcomes`, drop `deferred_goal_for_next_iteration`.
  - `attempts`: add `plan_id`, `execution_tree`; drop `planner_task_id`, `generator_task_ids`, `reducer_task_ids`, `outcomes`, `deferred_goal`.
  - `tasks`: `task_id` opaque; `role IN ('root','planner','worker')`; drop `attempt_id`, `workflow_id`, `iteration_id`, `work_item_id`, `needs`, `outcomes`, `terminal_tool_result`, and their indexes; add nullable `task_outcome` TaskOutcome JSON.
  - `task_runs`: drop workflow coordinate columns/indexes; keep `terminal_payload` for raw agent terminal compatibility and add nullable typed `task_outcome` mirroring the same TaskOutcome JSON.
  - `parented_runs`: keep `terminal_payload` and add nullable typed `parented_outcome` mirroring the same ParentedOutcome JSON.
  - `rows.rs`: drop `MaterializedPlan` reconstruction, the `generator`/`reducer` parse, and the outcome normalizer; rebuild `latch_attempt_tasks_cancelled` to read ids from the `execution_tree`.
- `eos-agent-run`: reshape `SpawnAgentTarget::Workflow` to `{ coords, role, plan_id, work_item_id? }`; the store mints the opaque `task_id`.

Verification:

```text
cd agent-core && cargo check -p eos-types --all-targets
cd agent-core && cargo check -p eos-db --all-targets
cd agent-core && cargo check -p eos-agent-run --all-targets
```

### Phase 2 - Tools

- Rename terminal tool names and model-facing docs; delete the reducer terminal;
  replace generator with worker.
- Split `tools/submission.rs` into per-tool files under `submission/`; shared
  `SubmissionStatus` (wire), `OutcomeInput`, and helpers go in `support.rs`.
- Each terminal records the matching outcome variant (`is_pass` from `SubmissionStatus`);
  `submit_plan_outcome` also materializes `execution_tree.nodes`. Drop `submission_kind`.

Verification: `cd agent-core && cargo check -p eos-tool --all-targets`

### Phase 3 - Workflow Runtime

- Keep planner run creation; the planner `task_id` is opaque (recorded in the tree).
- Replace `GeneratorLaunch`/`ReducerLaunch` with the worker arm of the `AgentLaunch`
  struct+kind (`kind = Planner { plan_id } | Worker { work_item_id }`).
- Lazy-spawn workers from the tree (no eager materialization of worker rows); readiness
  over the `execution_tree` nodes + spawned worker statuses.
- Rewrite worker run settlement and missing-terminal synthesis to record
  `TaskOutcome::Worker`.
- Dissolve `ids.rs` and `state.rs`; rewrite the stranded test trees.

Verification: `cd agent-core && cargo test -p eos-workflow attempt -- --nocapture`

### Phase 4 - Outcome Aggregation

- Implement the read-side `AttemptOutcome`/`IterationOutcome`/`WorkflowOutcome`
  projections; remove any stored outcome cache.
- Close attempts from worker `is_pass`/statuses; derive the next iteration goal from
  the returned attempt's `planner_outcome`.

Verification:

```text
cd agent-core && cargo test -p eos-workflow iteration -- --nocapture
cd agent-core && cargo test -p eos-workflow service -- --nocapture
```

### Phase 5 - Context Recipes

- `ContextScope`/`ContextRole` -> `{Planner, Worker}`; collapse context to
  `planner_context.rs` + `worker_context.rs` + `render.rs`.
- Render worker `<needs>` from the `execution_tree` (`work_item_id -> task_id`) +
  sibling `TaskOutcome::Worker`.
- Update `.eos-agents/profile/main/planner.md` and `executor.md`.

Verification: `cd agent-core && cargo test -p eos-workflow context -- --nocapture`

### Phase 6 - Cleanup Gate

- Delete reducer files, generated references, stale snapshots, and stale docs.
- Remove `Generator*`, `Reducer*`, `MaterializedPlan`, `PlanDisposition`,
  `ExecutionRole`, `WorkflowNodeId`, the task-id derivation/parsers, and reducer
  profile/tool docs.

Verification:

```text
cd agent-core && cargo check --workspace --all-targets
cd agent-core && cargo test --workspace
rg "Generator|generator|Reducer|reducer|submit_generator_outcome|submit_reducer_outcome|disposition|submission_kind|MaterializedPlan|ExecutionRole|ExecutionTaskOutcome|WorkflowNodeId|worker_task_id|is_terminal_generator" agent-core .eos-agents docs
```

Remaining matches must be historical migration docs or explicit compatibility notes
scheduled for deletion.

## 14. Acceptance Criteria

- No reducer row can be created.
- Exactly one planner run per attempt; the planner is never a worker-DAG member and
  the model never sends a task id.
- `task_id` is opaque (store-minted at `spawn_agent`), uniform for root/planner/worker;
  no `worker_task_id`/`planner_task_id` derivation, no `{attempt}:{...}` encoding, no
  reverse parsers, no `WorkflowNodeId`.
- `Task`/`task_runs` carry no `attempt_id`/`workflow_id`/`iteration_id`/`needs`/
  `work_item_id`/`outcomes`; `Task` drops `terminal_tool_result` and exposes
  `task_outcome: Option<TaskOutcome>`; `AgentRun` keeps the raw terminal payload
  alongside the equivalent typed `task_outcome`; `ParentedRun` keeps the raw
  terminal payload alongside the equivalent typed `parented_outcome`.
- `Attempt` carries `plan_id` (minted) + `execution_tree`; no `MaterializedPlan`, no
  `planner_task_id`/`generator_task_ids`/`reducer_task_ids` columns.
- Two per-family outcome enums: `TaskOutcome {Root,Planner,Worker}` and
  `ParentedOutcome {Advisor,Subagent}`; `PlanOutcome`/`WorkItemOutcome`/
  `ExecutionTaskOutcome`/`Task.outcomes` do not exist.
- `TaskOutcome::{Root,Worker}` carry `is_pass`; `Planner` carries the full plan
  (`plan_spec` + `work_items` + `deferred_goal_for_next_iteration`) and no `is_pass`.
- `AttemptOutcome`/`IterationOutcome`/`WorkflowOutcome` are recursive read-side
  projections with a rolled-up `status: bool`; the lifecycle disposition enums are
  gone; the deferred goal is derived from the planner outcome and the
  `iterations.deferred_goal_for_next_iteration` column does not exist.
- No model-facing tool named `submit_generator_outcome`, `submit_reducer_outcome`, or
  `submit_planner_outcome`; terminals are the five `Submit*Outcome` tools.
- `submit_plan_outcome` records `TaskOutcome::Planner` and materializes the
  `execution_tree`; `submit_worker_outcome` records `TaskOutcome::Worker` and updates
  the worker status; `submit_root_task_outcome` records `TaskOutcome::Root`.
- Schema renames applied: `workflows.{parent_agent_run_id, workflow_goal}`,
  `iterations.{iteration_goal, workflow_goal}`.
- Worker context contains `plan_spec`, the current work item, and direct needs
  outcomes (resolved via the tree); planner context contains the current iteration
  scope and exactly one compact prior-evidence group.
- Terminal result metadata contains no `disposition` and no `submission_kind`.
- The context render layer is three files; `ids.rs` and `state.rs` no longer exist.
