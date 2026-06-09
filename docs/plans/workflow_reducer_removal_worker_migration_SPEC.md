# Workflow Reducer Removal And Worker Migration - SPEC

Status: Proposed (rev 5)
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

## 0. Revision 5 — adopted model

Rev 5 keeps rev 4's core fold (delete reducer, generator → worker, two outcome
families) and supersedes the rev-2 points below. The structural decisions that
drive everything else:

1. **No durable task abstraction.** Delete the target `Task` store, `TaskId`,
   `TaskRole`, `TaskStatus`, `TaskOutcome`, `task_runs`, and deterministic task-id
   helpers. Durable run identity is `AgentRunId`; durable execution rows are
   `agent_runs` and `parented_runs`. Workflow lifecycle rows, context scopes, and
   run rows carry **no task-related fields**.

2. **`plan_id` + `AttemptExecutionTree`.** The `Attempt` mints a `plan_id` at
   creation. The planner authors `work_item_id`s in `submit_plan_outcome`. The
   attempt carries an `AttemptExecutionTree` — `{ plan_id,
   nodes:[{ work_item_id, needs, status, worker_outcome? }] }` — materialized on
   `submit_plan_outcome`. **The tree is the workflow DAG + worker outcome index**,
   not an attempt↔task index.

3. **Agent runs replace task rows.** `agent_runs` stores root/planner/worker
   runtime state (`agent_run_id, request_id, role, instruction, status, agent_name,
   terminal_payload`). It has no typed outcome mirror, no `task_id`, no workflow lineage
   columns, no `needs`, and no `work_item_id`.

4. **Full plan on the attempt.** The plan (`plan_spec` + `work_items` +
   `deferred_goal_for_next_iteration`) is recorded as the attempt's
   `planner_outcome`. The planner run may mirror the same terminal payload for
   recording, but workflow lifecycle and aggregation never look up an agent-run
   row. There is **no** `work_items` table.

5. **`is_pass` only where workflow owns a leaf result.** `WorkerOutcome` carries
   `is_pass: bool` only; `PlannerOutcome` carries the plan (no
   `is_pass` — a returned plan is success by construction). Root success is request
   state, not a workflow task outcome. There is no per-run typed outcome enum.

6. **Aggregation outcomes are recursive read-side projections** (computed, never
   stored): `AttemptOutcome`, `IterationOutcome`, `WorkflowOutcome` (§11). Each
   carries a rolled-up `status: bool`. They **replace** the lifecycle disposition
   enums (`IterationOutcome::{Complete,Continue,Failed}`,
   `WorkflowOutcome::{Succeeded,Failed,Cancelled}`); "continue vs complete" is
   `deferred_goal.is_some()`. The deferred goal's single workflow source is
   `Attempt.planner_outcome.deferred_goal_for_next_iteration`, so the
   `iterations.deferred_goal_for_next_iteration` column is **dropped** (derived on
   next-iteration creation). The stored `*Status` enums remain the in-flight /
   scheduling authority. (Supersedes rev-2's "no status on aggregation projections.")

7. **Schema renames.** `workflows.launched_by_agent_run_id → parent_agent_run_id`,
   `workflows.goal → workflow_goal`; `iterations.goal → iteration_goal` plus a
   denormalized `iterations.workflow_goal` (context locality).

Carried unchanged from rev 2: the planner keeps its own agent run for recording
only; advisor/subagent retain `ParentedOutcome`; delete `TaskOutcome`, `RunOutcome`,
`MaterializedPlan`, `PlanDisposition`, `ExecutionRole`, `ExecutionTaskOutcome`,
`PlanOutcome`, and `WorkItemOutcome`; collapse the context layer. Worker typed
state has no natural-language `outcome`.

## 1. Intent

This is an aggressive cleanup migration. The target removes reducer as a workflow
role, converts generator terminology to worker terminology, removes the binary
generator/reducer execution model, and unifies every terminal payload under one
workflow-owned status/result fields.

The workflow model becomes:

```text
Workflow
  -> Iteration[]
      -> Attempt[]   (mints plan_id; owns an AttemptExecutionTree)
          -> planner run   (raw terminal record only)
          -> worker run[]  (raw terminal record only)
```

Every terminal agent run is an `agent_runs` row keyed by `agent_run_id`, carrying
the raw terminal tool result (`terminal_payload`) for record/audit compatibility.
The plan lives on the attempt as `planner_outcome`;
the attempt's `execution_tree` stores the planner-authored `work_item_id` DAG and
each worker's lifecycle status/outcome.

The question "did it succeed?" is answered two ways: in-flight by the lifecycle
state, and as a terminal roll-up by `is_pass` on the leaf outcomes:

```text
RunStatus · AttemptStatus · IterationStatus · WorkflowStatus    (in-flight / scheduling)
WorkerOutcome.is_pass                                           (terminal pass/fail of a work item)
AttemptOutcome/IterationOutcome/WorkflowOutcome.status          (read-side roll-up)
```

Root, advisor, and subagent terminal payloads are also outcome variants on their own
rows; advisor/subagent use `ParentedOutcome` on `parented_runs`.

## 2. Decisions

| Area | Decision |
| --- | --- |
| Reducer role | Delete. No reducer rows, tasks, launches, outcomes, context recipes, terminal tools, or reducer profile/skill files. |
| Generator role | Replace with worker. Public workflow contracts use `Worker` / `WorkItem`; no `Generator` public contract remains. |
| Planner identity | Planner keeps an agent run for recording, but workflow state has no planner run field. The attempt is the planner lifecycle owner through `planner_outcome`. The model never sends a task or run id. |
| Task abstraction | Delete from the target model. No `Task`, `TaskId`, `TaskRole`, `TaskStatus`, `TaskOutcome`, `task_runs`, `worker_task_id`, `planner_task_id`, `WorkflowNodeId`, or reverse parsers. |
| Plan id | `plan_id` is minted by the `Attempt` at creation (the plan's identity). |
| Attempt↔run linkage | None in workflow persistence. The `AttemptExecutionTree` is a work-item DAG and worker-outcome index only. `agent_runs` carry **no** `attempt_id`, and attempts carry **no** run ids. |
| Agent run shape | `AgentRun` is runtime/record state: `agent_run_id, request_id, role, instruction, status, agent_name, terminal_payload`. No typed outcome mirror, workflow lineage, `needs`, or `work_item_id`. |
| Plan payload | Full plan (`plan_spec` + `work_items` + `deferred_goal_for_next_iteration`) in `Attempt.planner_outcome`. The planner run keeps only the raw terminal payload for recording. No `work_items` table, no `task_specs`, no `reducers`, no `disposition`. |
| Work item payload | Each `WorkItemSpec` carries its own `work_spec` (the worker's instruction), `agent_name`, and `needs`. |
| Outcome model | Workflow-owned typed outcomes only: `PlannerOutcome` on `Attempt`, `WorkerOutcome` on `ExecutionNode`, and `ParentedOutcome` on parented runs. `AgentRun` keeps only raw terminal payload. Delete `TaskOutcome`, `RunOutcome`, `PlanOutcome`, `WorkItemOutcome`, `ExecutionTaskOutcome`, and `Task.outcomes`. |
| Success/failure | `is_pass: bool` on `WorkerOutcome`; aggregation `status: bool`; lifecycle `*Status` enums for scheduling. Model-facing inputs carry `SubmissionStatus` (maps onto worker node status and run status where a run exists). Do not add `is_successful`, `is_success`, or `has_structured_outcome`. |
| Outcome text | No structured `outcome` field anywhere in the target contracts. Free-text terminal details, if retained, stay only inside raw audit payloads. Planner body is `plan_spec` + `work_items` + `deferred_goal_for_next_iteration`. |
| Aggregation | `AttemptOutcome`/`IterationOutcome`/`WorkflowOutcome` are recursive read-side projections; they replace the lifecycle disposition enums. Not stored. |
| Context data | No public context projection DTOs. Filtering is local to `eos-workflow` context render functions. |
| Record paths | Planner and worker records are run-owned. `format_record_dir` / run finish use lineage from the spawn context (the ephemeral launch carries `WorkflowCoordinates`), not from a task column. |

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
- `Task`, `TaskId`, `TaskRole`, `TaskStatus`, `TaskOutcome`, `task_runs`
- `RunOutcome`, `PlanOutcome`, `WorkItemOutcome`
- `worker_task_id` / `planner_task_id` **derivation functions**, `generator_id_from_task_id`,
  `reducer_id_from_task_id`, and any `{attempt}:{...}` task-id encoding
- `tasks.attempt_id` / `tasks.workflow_id` / `tasks.iteration_id` (the task store is removed)

`AttemptOutcome` / `IterationOutcome` / `WorkflowOutcome` are **allowed** as read-side
projection types (§11) — not stored DTOs and not lifecycle disposition enums.

Naming rules:

| Surface | Rule |
| --- | --- |
| Workflow files | Name files after the runtime ownership they contain: `attempt_run`, `planner_run`, `work_items`, `work_items_run`, `workflow_run`, `iteration_run`. |
| Work item execution | Use `work_items_run` for worker wave execution and settlement. Do not use `work_dag`, `plan_dag`, `node`, `stage`, or `orchestrator` names for this owner. |
| Plan shape | Use `WorkItemSpec` for planner-authored work items. Do not introduce `PlanWorkItem`. |
| Outcome type | Do not introduce a per-run agent outcome enum. Workflow typed results are `PlannerOutcome` and `WorkerOutcome`; parented runs use `ParentedOutcome`. |
| Terminal text | Do not add typed `outcome` text to any target DTO. Free-text terminal details, if retained, stay only inside raw audit payloads. |
| Success/failure | `is_pass: bool` on `WorkerOutcome`; aggregation `status: bool`; lifecycle `*Status` enums for scheduling. Model-facing inputs carry `SubmissionStatus`. |
| Dependencies | Use `needs` for direct work item dependencies (on `WorkItemSpec` and `ExecutionNode`). Do not add `direct_needs` or `direct_need_outcomes`. |
| Worker assignment | Use `agent_name: AgentName` on `WorkItemSpec`. Do not use `agent_profile_name` or `assigned_work_item`. |
| Deferred goal | Typed `DeferredGoal` newtype on internal contracts; single workflow source is `Attempt.planner_outcome.deferred_goal_for_next_iteration`. Raw `String` only on the model-facing `*Input` wire DTOs. |
| Task mapping | Do not add task mapping to workflow state. `AttemptExecutionTree` is keyed by `work_item_id` and stores worker status/outcome only. |

## 3. Resulting File And Folder Structure

Target structure under `agent-core`:

```text
agent-core/crates/eos-types/src/
  contracts/
    record.rs                    # AgentRunKind; workflow coordinates ride on spawn/record target
                                 #   SpawnAgentTarget::Workflow { coords, role, plan_id, work_item_id? }
    workflow.rs                  # WorkflowApi + 2-method attempt submission API
  state/
    request_agent_run/
      agent_run.rs               # AgentRunRole::{Root, Planner, Worker}; RunStatus::is_terminal;
                                 #   AgentRun with raw terminal_payload only (no typed outcome mirror / lineage / needs / work_item_id)
    tools/
      submissions.rs             # PlanOutcomeSubmission, WorkerOutcomeSubmission, SubmissionStatus
    workflow/
      workflow.rs                # Workflow lifecycle DTO + WorkflowStatus (was entity.rs)
      iteration.rs               # Iteration lifecycle DTO + IterationStatus
      attempt.rs                 # AttemptState, AttemptClosure, AttemptStatus, Attempt,
                                 #   AttemptExecutionTree, ExecutionNode, AttemptBudget
      work_item.rs               # WorkItemId, PlanId, WorkItemSpec, DeferredGoal
      outcome.rs                 # PlannerOutcome, WorkerOutcome, ParentedOutcome {Advisor,Subagent};
                                 #   AdvisorVerdict; read-side AttemptOutcome/IterationOutcome/WorkflowOutcome
  # Agent-run id minting lives in the run store surface (stores.rs); no eos-workflow ids.

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
    terminal.rs                  # TerminalTool::{Root, Plan, Worker, Advisor, Subagent}
    submission/
      mod.rs
      support.rs                 # SubmissionStatus (wire), OutcomeInput, shared helpers
      submit_root_outcome.rs
      submit_plan_outcome.rs
      submit_worker_outcome.rs
      submit_advisor_outcome.rs
      submit_subagent_outcome.rs

agent-core/crates/eos-db/         # edit 0001_initial.sql in place (contingent on no deployed DB)
  # workflows: launched_by_agent_run_id -> parent_agent_run_id; goal -> workflow_goal; drop outcomes
  # iterations: goal -> iteration_goal; ADD workflow_goal; drop outcomes; drop deferred_goal_for_next_iteration
  # attempts: ADD plan_id, execution_tree; drop planner_task_id, generator_task_ids, reducer_task_ids,
  #           outcomes, deferred_goal
  # agent_runs: agent_run_id PRIMARY KEY; role IN ('root','planner','worker'); drop task_id,
  #           attempt_id, workflow_id, iteration_id, work_item_id, needs, outcomes (+ coordinate indexes)
  # rows.rs: drop MaterializedPlan reconstruction + 'generator'/'reducer' parsing + outcome normalizer;
  #          drop tasks table/store; agent_runs keep terminal_payload only;
  #          ParentedRun keeps terminal_payload plus parented_outcome
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
.eos-agents/tools/submit_root_outcome.md
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
| `ExecutionRole` / `ExecutionTaskOutcome` | delete (use `WorkerOutcome.is_pass` + worker node status) |
| `Task` / `TaskId` / task store | delete; `AgentRunId` is the durable run id |
| `MaterializedPlan` | delete (plan in `Attempt.planner_outcome`; DAG and worker outcomes in `AttemptExecutionTree`) |
| `PlanDisposition` | delete (use `Option<DeferredGoal>`) |
| `PlanTask` | `WorkItemSpec` |
| `PlanReducer` | delete |
| `GeneratorId` | `WorkItemId` |
| `ReducerId` / `PlannerId` | delete |
| `PlannerPlan.{planner_task_id, disposition, tasks, task_specs, reducers}` | `PlannerOutcome { plan_spec, work_items, deferred_goal_for_next_iteration }` on the attempt |
| `GeneratorSubmission` / `ReducerSubmission` | `WorkerOutcomeSubmission` |
| `PlannerSubmission` / `PlannerFailureSubmission` / `PlannerFailReason` | `PlanOutcomeSubmission`; planner failure is an attempt lifecycle transition |
| `IterationOutcome` / `WorkflowOutcome` lifecycle enums | read-side projections (§11) |
| `summary` / `failure_summary` / `review_summary` / `outcome` text fields | delete from structured target DTOs |

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
No workflow lifecycle type stores task ids. The `AttemptExecutionTree` is the
work-item DAG and worker outcome index:

```rust
pub struct AttemptExecutionTree {
    pub plan_id: PlanId,
    pub nodes: Vec<ExecutionNode>,          // materialized on submit_plan_outcome
}
pub struct ExecutionNode {
    pub work_item_id: WorkItemId,
    pub needs: Vec<WorkItemId>,             // DAG edges
    pub status: WorkItemStatus,             // Pending | Running | Done | Failed | Cancelled
    pub worker_outcome: Option<WorkerOutcome>,
}
```

Find a worker by `work_item_id` through the node lookup. Cancellation and
in-process control use the active-run registry keyed by workflow coordinates and
`work_item_id`, not persisted task ids.

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

/// Planner result owned by the attempt, not by an agent-run row.
#[derive(Serialize, Deserialize, JsonSchema)]
pub struct PlannerOutcome {
    pub plan_spec: String,
    pub work_items: Vec<WorkItemSpec>,
    /// Concrete current-iteration goal items carried to the next iteration.
    pub deferred_goal_for_next_iteration: Option<DeferredGoal>,
}

/// Worker result owned by an execution-tree node keyed by work_item_id.
#[derive(Serialize, Deserialize, JsonSchema)]
pub struct WorkerOutcome {
    pub is_pass: bool,
}

/// PARENTED family outcome (AgentType::{Subagent, Advisor}). Stored as parented_runs.parented_outcome.
#[derive(Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ParentedOutcome {
    Advisor  { verdict: AdvisorVerdict },
    Subagent {},
}

pub enum AdvisorVerdict { Approve, Reject }   // promoted from the tool-private Verdict; homed in outcome.rs

/// Root/planner/worker agent-run row. The raw terminal payload is kept for agent-run
/// record/audit compatibility; typed workflow outcomes live on Attempt/ExecutionNode.
pub struct AgentRun {
    pub agent_run_id: AgentRunId,
    pub request_id: RequestId,
    pub role: AgentRunRole,              // Root | Planner | Worker
    pub instruction: String,
    pub status: RunStatus,
    pub agent_name: AgentName,
    pub terminal_payload: Option<JsonObject>,
    pub token_count: i64,
    pub error: Option<String>,
    pub created_at: UtcDateTime,
    pub updated_at: UtcDateTime,
    pub finished_at: Option<UtcDateTime>,
}

/// Advisor/subagent run row. Parented outcome is the typed mirror of the raw payload.
pub struct ParentedRun {
    pub agent_run_id: AgentRunId,
    pub request_id: RequestId,
    pub status: RunStatus,
    pub parent_agent_run_id: AgentRunId,
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

/// Attempt lifecycle. The plan lives on the attempt; the execution tree is the
/// work-item DAG and worker outcome index. No MaterializedPlan and no task-id fields.
pub struct Attempt {
    pub id: AttemptId,
    pub iteration_id: IterationId,
    pub workflow_id: WorkflowId,         // denormalized; derivable via iteration_id
    pub attempt_sequence_no: i64,
    pub plan_id: PlanId,                 // minted at creation
    pub planner_outcome: Option<PlannerOutcome>,
    pub execution_tree: AttemptExecutionTree,
    pub state: AttemptState,
}

pub enum AttemptState {
    Planning { started: bool },          // started guards double-start
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
pub struct AttemptOutcome {
    pub status: bool,                        // plan returned AND every worker is_pass (empty workers => false)
    pub planner_outcome: PlannerOutcome,     // from Attempt.planner_outcome
    pub worker_outcomes: Vec<WorkerOutcome>, // from execution_tree.nodes[].worker_outcome
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

Each terminal keeps a small model-facing input and records the raw terminal payload
on the run record. Typed workflow results are written only to the workflow-owned
fields (`Attempt.planner_outcome`, `ExecutionNode.worker_outcome`, request status,
or `ParentedRun.parented_outcome`).

### `submit_root_outcome`

```rust
pub struct SubmitRootOutcomeInput { pub status: SubmissionStatus }
```

Maps `SubmissionStatus` onto the root run status and `RequestStatus`; there is no
typed root outcome mirror.

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

- A model-submitted plan records the raw terminal payload on the planner run, sets
  the planner `RunStatus::Done`, records `Attempt.planner_outcome`, **and materializes
  the attempt's `execution_tree.nodes`** from `work_items` (`work_item_id` + `needs`,
  initial status).
- A planner that fails to return creates no `PlannerOutcome`; the runtime marks
  the planner run `Failed` (synthesized) and the attempt closes failed.
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
pub struct SubmitWorkerOutcomeInput { pub status: SubmissionStatus }
```

Internal submission:

```rust
pub struct WorkerOutcomeSubmission {
    pub attempt_id: AttemptId,
    pub work_item_id: WorkItemId,
    pub status: SubmissionStatus,   // maps onto is_pass + the worker node/run status
}
```

Rules:

- Records `WorkerOutcome { is_pass }` on
  `execution_tree.nodes[work_item_id]` and updates the worker run status.
- A worker that fails to return is synthesized by workflow runtime as a failed worker
  with `is_pass = false`; diagnostic text stays in run error/raw terminal audit fields.
- The worker is keyed in workflow state by planner-authored `work_item_id`.
- Terminal metadata may include `attempt_id` and `work_item_id`; it must not include
  any task/run id or `submission_kind`.

### `submit_advisor_outcome`

```rust
pub struct SubmitAdvisorOutcomeInput { pub verdict: AdvisorVerdict }
```

Records `ParentedOutcome::Advisor { verdict }`. Renames `submit_advisor_feedback`.

### `submit_subagent_outcome`

```rust
pub struct SubmitSubagentOutcomeInput {}
```

Records `ParentedOutcome::Subagent {}`. Renames `submit_subagent_result`.
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
| `submit_root_task_outcome` | `submit_root_outcome` | remove `Task` from the terminal name |
| `submit_planner_outcome` | `submit_plan_outcome` | rename; no planner task id from the model |
| `submit_generator_outcome` | `submit_worker_outcome` | replace |
| `submit_reducer_outcome` | none | delete |
| `submit_advisor_feedback` | `submit_advisor_outcome` | rename |
| `submit_subagent_result` | `submit_subagent_outcome` | rename |

Terminal tool enum target:

```rust
pub enum TerminalTool { Root, Plan, Worker, Advisor, Subagent }
```

`ToolName::ALL` shrinks by one terminal after deleting reducer:

```text
SubmitRootOutcome · SubmitPlanOutcome · SubmitWorkerOutcome · SubmitAdvisorOutcome · SubmitSubagentOutcome
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

- `planner_outcome` = `Attempt.planner_outcome`.
- `worker_outcomes` = each node's `worker_outcome`.
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

Context renders role-specific text directly from workflow state.
No public `ContextOutcomeSlice`/`ContextOutcomeView` contract.

```rust
pub enum ContextRole { Planner, Worker }

pub enum ContextScope {
    Planner { workflow_id: WorkflowId, iteration_id: IterationId, attempt_id: AttemptId },
    Worker {
        workflow_id: WorkflowId,
        iteration_id: IterationId,
        attempt_id: AttemptId,
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
  iteration (read from those attempts' `WorkerOutcome`s via the tree).
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

- `<plan_spec>`: the planner's plan-level explanation (from `Attempt.planner_outcome`).
- `<work_item>`: this worker's `work_item_id` and `work_spec`.
- `<needs>`: direct dependency statuses only, resolved by `WorkItemId` edge lookup
  in the attempt's `execution_tree`.

Worker directive:

```text
Complete <work_item> using <plan_spec> and direct <needs>. Submit exactly one
worker status.
```

Worker render shape:

```xml
<context role="worker">
  <plan_spec>The planner-level explanation of how this attempt is structured.</plan_spec>
  <needs>
    <work_item id="w1">
      <status>success</status>
    </work_item>
  </needs>
  <work_item id="w2">
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
  `PlannerOutcome`, `WorkerOutcome`, `ParentedOutcome`, `AdvisorVerdict`, the
  read-side projection structs, and the submission structs.
- Delete `MaterializedPlan`, `PlanDisposition`, `ExecutionRole`, `ExecutionTaskOutcome`,
  `PlannerId`, `GeneratorId`, `ReducerId`, `WorkflowNodeId`, the deterministic task-id
  functions + reverse parsers, `Task`, `TaskId`, `TaskRole`, `TaskStatus`,
  `TaskOutcome`, and `task_runs`; rename `is_terminal_generator -> is_terminal`.
- Add `AgentRunRole -> {Root, Planner, Worker}` and `RunStatus`; delete `WorkflowTaskRole`.
- `eos-db` schema (edit `0001_initial.sql` in place; verify no deployed DB):
  - `workflows`: `launched_by_agent_run_id -> parent_agent_run_id`, `goal -> workflow_goal`, drop `outcomes`.
  - `iterations`: `goal -> iteration_goal`, add `workflow_goal`, drop `outcomes`, drop `deferred_goal_for_next_iteration`.
  - `attempts`: add `plan_id`, `execution_tree`; drop `planner_task_id`, `generator_task_ids`, `reducer_task_ids`, `outcomes`, `deferred_goal`.
  - `tasks` / `task_runs`: delete or migrate into `agent_runs`; no `task_id` or typed outcome mirror survives.
  - `agent_runs`: `agent_run_id` primary key; keep `terminal_payload` for raw agent terminal compatibility; no `run_outcome`.
  - `parented_runs`: keep `terminal_payload` and add nullable typed `parented_outcome` mirroring the same ParentedOutcome JSON.
  - `rows.rs`: drop `MaterializedPlan` reconstruction, the `generator`/`reducer` parse, and the outcome normalizer; rebuild cancellation around the active-run registry instead of persisted attempt task ids.
- `eos-agent-run`: reshape `SpawnAgentTarget::Workflow` to `{ coords, role, plan_id, work_item_id? }`; the run store mints `agent_run_id`.

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

- Keep planner run creation; no planner task id is created or recorded.
- Replace `GeneratorLaunch`/`ReducerLaunch` with the worker arm of the `AgentLaunch`
  struct+kind (`kind = Planner { plan_id } | Worker { work_item_id }`).
- Lazy-spawn workers from the tree (no eager materialization of worker rows); readiness
  over the `execution_tree` node statuses.
- Rewrite worker run settlement and missing-terminal synthesis to record
  `WorkerOutcome`.
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
- Render worker `<needs>` from sibling `execution_tree` nodes by `work_item_id`.
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
- No `Task`, `TaskId`, `TaskRole`, `TaskStatus`, `TaskOutcome`, `RunOutcome`,
  `task_runs`, `worker_task_id`/`planner_task_id` derivation, `{attempt}:{...}`
  encoding, reverse parsers, or `WorkflowNodeId`.
- `agent_runs` are keyed by `agent_run_id`, carry no
  `attempt_id`/`workflow_id`/`iteration_id`/`needs`/`work_item_id`, and keep only
  raw terminal payload. `ParentedRun` keeps raw terminal payload beside
  `parented_outcome`.
- `Attempt` carries `plan_id` (minted) + `execution_tree`; no `MaterializedPlan`, no
  `planner_task_id`/`generator_task_ids`/`reducer_task_ids` columns.
- Workflow-owned typed results are `PlannerOutcome`, `WorkerOutcome { is_pass }`,
  and `ParentedOutcome`; `TaskOutcome`, `RunOutcome`, `PlanOutcome`,
  `WorkItemOutcome`, `ExecutionTaskOutcome`, and `Task.outcomes` do not exist.
- No structured `outcome` field exists in target DTOs.
- `AttemptOutcome`/`IterationOutcome`/`WorkflowOutcome` are recursive read-side
  projections with a rolled-up `status: bool`; the lifecycle disposition enums are
  gone; the deferred goal is derived from the planner outcome and the
  `iterations.deferred_goal_for_next_iteration` column does not exist.
- No model-facing tool named `submit_generator_outcome`, `submit_reducer_outcome`, or
  `submit_planner_outcome`; terminals are the five `Submit*Outcome` tools.
- `submit_plan_outcome` records `Attempt.planner_outcome` and materializes the
  `execution_tree`; `submit_worker_outcome` records `WorkerOutcome { is_pass }`
  and updates the worker status; `submit_root_outcome` updates request/root-run
  status only.
- Schema renames applied: `workflows.{parent_agent_run_id, workflow_goal}`,
  `iterations.{iteration_goal, workflow_goal}`.
- Worker context contains `plan_spec`, the current work item, and direct needs
  outcomes (resolved via the tree); planner context contains the current iteration
  scope and exactly one compact prior-evidence group.
- Terminal result metadata contains no `disposition` and no `submission_kind`.
- The context render layer is three files; `ids.rs` and `state.rs` no longer exist.
