# Phase 03B - Execution Lineage and Materialization Spec

Status: Draft
Date: 2026-06-09
Owner: eos-db / eos-agent-run / eos-workflow / eos-engine / eos-agent-core

## Placement

This phase runs after Phase 03 and before Phase 04.

Phase 04 cannot cleanly split `eos-engine` from `eos-agent-run` until the
durable execution-lineage model is explicit. This phase defines that model:
which rows are created, which crate owns each transition, which metadata reaches
the agent loop, and how message-record folders are derived from durable state.

## Scope

This phase establishes:

- the durable relationship between request, task, workflow, iteration, attempt,
  and agent run,
- task-backed versus helper agent-run semantics,
- workflow launch lineage,
- the passive record context passed into the engine loop,
- message-record path generation for `messages.jsonl` and `events.jsonl`,
- read-side materialization for request/task/workflow execution trees.

It does not move the agent loop, rename crates, or split files. Those stay in
Phase 04 after this contract exists.

## Non-Goals

- Do not create wrapper persistence objects such as `WorkflowNode`,
  `IterationNode`, or `AttemptNode`.
- Do not add child arrays to `Task` for workflows, subagents, or advisors.
- Do not turn every subagent or advisor into a `Task`; helper runs remain
  `AgentRun` rows unless they become schedulable workflow work.
- Do not put engine execution, tool behavior, provider clients, or runtime
  wiring into `eos-db`.
- Do not create a second durable tree just for message records. Message-record
  folders are a projection of execution lineage.

## Target Model

```mermaid
flowchart TD
    Request["Request"] --> RootTask["Task: root"]
    RootTask --> RootRun["AgentRun: task-backed root"]

    RootRun --> Workflow["Workflow: launched by task + agent run + tool use"]
    Workflow --> Iteration["Iteration"]
    Iteration --> Attempt["Attempt"]

    Attempt --> PlannerTask["Task: planner"]
    Attempt --> GeneratorTasks["Tasks: generators"]
    Attempt --> ReducerTasks["Tasks: reducers"]

    PlannerTask --> PlannerRun["AgentRun: planner"]
    GeneratorTasks --> GeneratorRuns["AgentRuns: generators"]
    ReducerTasks --> ReducerRuns["AgentRuns: reducers"]

    RootRun --> SubagentRun["AgentRun: subagent helper"]
    RootRun --> AdvisorRun["AgentRun: advisor helper"]
```

Rules:

| Concept | Meaning | Persistence rule |
| --- | --- | --- |
| `Request` | user intake boundary | owns `root_task_id` |
| `Task` | schedulable unit of work | root tasks and workflow role tasks are tasks |
| `Workflow` | workflow lifecycle | owns iterations and attempts; records the launching task/run/tool use |
| `Iteration` | workflow progress unit | belongs to one workflow |
| `Attempt` | one workflow attempt | names planner/generator/reducer task ids |
| `AgentRun` | one agent-loop execution | task-backed runs have `task_id`; helper runs have `parent_agent_run_id` |

## Durable Store Contract

The exact SQL and Rust store names follow `eos-db` conventions, but the logical
fields below are required.

### agent_runs

Add or preserve these lineage columns:

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | `AgentRunId` |
| `request_id` | yes | request anchor for direct query and audit |
| `task_id` | nullable | set for root and workflow role runs |
| `parent_agent_run_id` | nullable | set for helper child runs |
| `run_kind` | yes | root, workflow task, subagent, advisor, or generic task run |
| `workflow_id` | nullable | set for workflow role runs |
| `iteration_id` | nullable | set for workflow role runs |
| `attempt_id` | nullable | set for workflow role runs |
| `workflow_role` | nullable | planner, generator, or reducer |
| `launched_by_tool_use_id` | nullable | model tool-use id that launched this run, when applicable |
| `agent_name` | nullable | human/debug agent name when the caller has one |
| terminal status fields | existing | lifecycle result owned by `eos-agent-run` |

Indexes and constraints:

| Constraint | Rule |
| --- | --- |
| task-backed uniqueness | one current main `agent_run` per `task_id`; if retries need multiple runs, introduce an explicit task-execution id before allowing duplicates |
| helper parent index | index `parent_agent_run_id` |
| request index | index `request_id` |
| workflow coordinate index | index `workflow_id`, `iteration_id`, and `attempt_id` together for workflow role runs |
| helper invariant | `Subagent` and `Advisor` runs require `parent_agent_run_id` and require `task_id IS NULL` |
| workflow role invariant | workflow role runs require `task_id`, `workflow_id`, `iteration_id`, `attempt_id`, and `workflow_role` |
| root invariant | root runs require `task_id` and must match `Request.root_task_id` |

If `run_kind` stores variant payloads natively, `workflow_role` can be derived
in Rust. A relational store may still keep `workflow_role` as a nullable query
column, but it must be validated against `run_kind`.

### workflows

Add or preserve these launch-lineage columns:

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | `WorkflowId` |
| `request_id` | yes | request anchor |
| `parent_task_id` | yes | task whose agent launched the workflow |
| `launched_by_agent_run_id` | yes | agent run that executed the workflow tool call |
| `launched_by_tool_use_id` | nullable | exact model tool-use id that launched the workflow |
| lifecycle fields | existing | workflow status, iteration ids, attempt ids |

`WorkflowService::find_outstanding_workflows` must either query by
`launched_by_agent_run_id` or be renamed/documented as task-scoped. It must not
accept an agent-run id and then ignore it.

### tasks

`Task` remains the schedulable unit. This phase does not add nested task
children.

Required relationships:

| Task kind | How it is found |
| --- | --- |
| root task | `Request.root_task_id` |
| planner task | `Attempt.planner_task_id` or equivalent attempt accessor |
| generator task | `Attempt.generator_task_id` or equivalent attempt accessor |
| reducer task | `Attempt.reducer_task_id` or equivalent attempt accessor |

If the current schema cannot distinguish workflow role tasks from root tasks
without loading the owning `Attempt`, add a passive role field or query helper in
`eos-db`; do not add a second hierarchy in TaskCenter.

## Run Kind Contract

`eos-types` owns passive DTOs and enums for lineage. Behavior stays in the owning
crates.

```rust
pub enum AgentRunKind {
    Root,
    Task,
    WorkflowTask { role: WorkflowRole },
    Subagent,
    Advisor,
}

pub enum WorkflowRole {
    Planner,
    Generator,
    Reducer,
}

pub struct AgentRunLineage {
    pub request_id: RequestId,
    pub task_id: Option<TaskId>,
    pub parent_agent_run_id: Option<AgentRunId>,
    pub run_kind: AgentRunKind,
    pub workflow_id: Option<WorkflowId>,
    pub iteration_id: Option<IterationId>,
    pub attempt_id: Option<AttemptId>,
    pub launched_by_tool_use_id: Option<ToolUseId>,
}
```

The names may be adjusted to match existing type names, but the invariants may
not be weakened.

## Creation Flow

```mermaid
sequenceDiagram
    participant Core as eos-agent-core
    participant Db as eos-db
    participant Run as eos-agent-run
    participant Engine as eos-engine
    participant Workflow as eos-workflow
    participant Tool as eos-tool

    Core->>Db: create Request + root Task
    Core->>Run: spawn root task-backed AgentRun
    Run->>Db: insert agent_runs lineage row
    Run->>Engine: AgentLoopExecutionRequest + passive record context
    Engine->>Tool: execute tools
    Tool->>Workflow: start workflow with launching task/run/tool use
    Workflow->>Db: insert Workflow + iterations/attempt tasks
    Workflow->>Run: spawn planner/generator/reducer task-backed AgentRuns
    Tool->>Run: spawn subagent/advisor helper AgentRun
    Engine-->>Run: terminal AgentLoopOutcome
    Run->>Db: finalize agent_runs row
```

Creation rules:

| Event | Owner | Required write |
| --- | --- | --- |
| user request accepted | `eos-agent-core` runtime through `eos-db` | `Request` and root `Task` |
| task enters main loop | `eos-agent-run` | task-backed `AgentRun` with `request_id` and `task_id` |
| workflow tool accepted | `eos-workflow` | `Workflow` with `parent_task_id`, `launched_by_agent_run_id`, and optional `launched_by_tool_use_id` |
| workflow attempt starts role work | `eos-workflow` | planner/generator/reducer `Task` rows |
| workflow role task enters main loop | `eos-agent-run` | task-backed `AgentRun` with workflow coordinate fields |
| subagent tool accepted | `eos-agent-run` through `eos-tool` caller | helper `AgentRun` with `parent_agent_run_id`, no `task_id` |
| advisor tool accepted | `eos-agent-run` through `eos-tool` caller | helper `AgentRun` with `parent_agent_run_id`, no `task_id` |
| loop finishes | `eos-agent-run` | terminal run status and final outcome fields |

## Agent Loop Input Contract

`eos-agent-run` creates the durable run row before the engine loop starts. The
engine receives passive lineage and record facts, not lifecycle ownership.

Allowed engine input:

```rust
pub struct AgentLoopExecutionRequest {
    pub agent_run_id: AgentRunId,
    pub lineage: AgentRunLineage,
    pub record_context: AgentRunRecordContext,
    // prompt, model, tools, cancellation, event sink, and runtime inputs
}

pub struct AgentRunRecordContext {
    pub request_id: RequestId,
    pub root_task_id: TaskId,
    pub agent_run_id: AgentRunId,
    pub task_id: Option<TaskId>,
    pub parent_agent_run_id: Option<AgentRunId>,
    pub workflow_id: Option<WorkflowId>,
    pub iteration_id: Option<IterationId>,
    pub attempt_id: Option<AttemptId>,
    pub workflow_role: Option<WorkflowRole>,
}
```

Forbidden engine input:

| Do not pass | Reason |
| --- | --- |
| active-run registry handles | owned by `eos-agent-run` |
| lifecycle finalization callbacks | finalization is one terminal handoff from engine to run |
| DB mutation handles for agent-run status | run lifecycle writes stay in `eos-agent-run` |
| tool-family-specific lineage strings | lineage is typed and request-rooted |
| a generic metadata bag with resource wiring | metadata is facts only, not dependency injection |

## Message Record Layout Contract

The normal production layout is request-rooted and generated from persisted
lineage.

```text
requests/<request_id>/
  root-task-<task_id>/
    agent-run-<agent_run_id>/
      messages.jsonl
      events.jsonl
      workflows/
        workflow-<workflow_id>/
          iteration-<iteration_id>/
            attempt-<attempt_id>/
              planner-task-<task_id>/agent-run-<agent_run_id>/
                messages.jsonl
                events.jsonl
              generator-task-<task_id>/agent-run-<agent_run_id>/
                messages.jsonl
                events.jsonl
              reducer-task-<task_id>/agent-run-<agent_run_id>/
                messages.jsonl
                events.jsonl
      subagents/subagent-run-<agent_run_id>/
        messages.jsonl
        events.jsonl
      advisors/advisor-run-<agent_run_id>/
        messages.jsonl
        events.jsonl
```

Rules:

| Rule | Owner |
| --- | --- |
| `messages.jsonl` is plural | `eos-engine` records |
| `events.jsonl` is plural | `eos-engine` records |
| root path uses `Request.root_task_id` | `eos-db` lineage query |
| workflow paths use workflow coordinate fields on `agent_runs` | `eos-db` lineage query |
| subagent/advisor paths use `parent_agent_run_id` and parent record directory | `eos-db` lineage query |
| callers choose `AgentRunKind` once at spawn | `eos-agent-run` validates and persists |
| records do not reconstruct hierarchy from ad hoc callsite strings | `eos-engine` records consume `AgentRunRecordContext` |
| `parents-missing/` is not part of the normal production path | tests must prove parent lineage exists before child records are written |

If a repair/debug fallback for missing parents is retained, it must be isolated
from the normal writer path and must emit a hard diagnostic event. It must not
be used to satisfy acceptance tests.

### messages.jsonl Rows

Each row represents one model-visible message or message delta committed to the
record.

Required base fields:

| Field | Meaning |
| --- | --- |
| `sequence` | monotonic sequence within one agent run |
| `timestamp` | write time |
| `request_id` | request anchor |
| `agent_run_id` | run anchor |
| `task_id` | task anchor when task-backed |
| `role` | system, user, assistant, or tool |
| `message` | serialized provider-neutral message payload |
| `tool_use_id` | set when the row belongs to a tool call/result |

### events.jsonl Rows

Each row represents one audit or lifecycle event visible to message-record
readers.

Required base fields:

| Field | Meaning |
| --- | --- |
| `sequence` | monotonic event sequence within one agent run |
| `timestamp` | write time |
| `request_id` | request anchor |
| `agent_run_id` | run anchor |
| `task_id` | task anchor when task-backed |
| `event_type` | node_started, messages_initialized, child_created, turn_started, tool_started, tool_finished, workflow_started, node_finished, or record_error |
| `payload` | event-specific structured payload |

`child_created` and `workflow_started` events must include the child run or
workflow ids they announce. The durable DB row is still the source of truth; the
event row is the audit trail.

## Materialized Read Model

The database stores normalized lineage. `eos-agent-core` exposes a read-side
materialization for callers that need the tree.

Target DTOs:

```rust
pub struct RequestExecutionTree {
    pub request: Request,
    pub root_task: TaskExecutionTree,
}

pub struct TaskExecutionTree {
    pub task: Task,
    pub main_agent_run: Option<AgentRun>,
    pub workflows: Vec<WorkflowExecutionTree>,
    pub subagents: Vec<AgentRun>,
    pub advisors: Vec<AgentRun>,
}

pub struct WorkflowExecutionTree {
    pub workflow: Workflow,
    pub iterations: Vec<IterationExecutionTree>,
}

pub struct IterationExecutionTree {
    pub iteration: Iteration,
    pub attempts: Vec<AttemptExecutionTree>,
}

pub struct AttemptExecutionTree {
    pub attempt: Attempt,
    pub planner: Option<Box<TaskExecutionTree>>,
    pub generators: Vec<TaskExecutionTree>,
    pub reducers: Vec<TaskExecutionTree>,
}
```

Rules:

| Rule | Reason |
| --- | --- |
| materialization is read-side only | avoids duplicating workflow/task ownership |
| TaskCenter does not store workflow/iteration/attempt wrapper nodes | `Workflow`, `Iteration`, and `Attempt` already own those identities |
| helper runs appear under the parent task's main run | subagents/advisors are child runs, not scheduled tasks |
| missing run rows are represented as `None` plus diagnostics | materialization must not fabricate rows |
| ordering is deterministic | stable audit and UI rendering |

## Crate Ownership

| Crate | Owns | Must not own |
| --- | --- | --- |
| `eos-types` | passive ids, DTOs, run-kind enums, lineage DTOs | DB queries or lifecycle behavior |
| `eos-db` | migrations, constraints, repository queries, materialization queries | engine loop or tool behavior |
| `eos-agent-run` | run admission, lineage validation, agent-run row creation/finalization | workflow lifecycle or message path guessing |
| `eos-workflow` | workflow/iteration/attempt/task creation and workflow launch lineage | agent-loop execution |
| `eos-engine` | writes loop-visible `messages.jsonl` and `events.jsonl` from passive context | run-row lifecycle ownership |
| `eos-agent-core` | request creation and public read-side materialization facade | normalized workflow persistence internals |
| `eos-tool` | passes typed launch facts for workflow/subagent/advisor tools | durable lineage derivation |

## Redundancy Rules

- Do not maintain a separate message-record hierarchy independent of DB
  lineage.
- Do not pass both `record_kind` strings and typed `AgentRunLineage`; typed
  lineage is the contract.
- Do not duplicate workflow role task ids in `Task` if `Attempt` already owns
  them and a query helper can materialize them.
- Do not add both `parent_task_id` and `parent_agent_run_id` to helper runs;
  helper runs use `parent_agent_run_id`, and the parent task is reached through
  the parent run.
- Denormalized `request_id` on `agent_runs` and `workflows` is allowed because
  it is the audit/query anchor; it is not optional.

## Migration Steps

1. Add passive lineage DTOs and run-kind enums in `eos-types`.
2. Add `eos-db` migrations, constraints, and focused repository tests for
   `agent_runs` and `workflows` lineage fields.
3. Update request intake to guarantee `Request.root_task_id` is persisted before
   root run spawn.
4. Update `eos-agent-run` spawn APIs to require typed lineage and to create the
   durable run row before engine startup.
5. Update workflow start APIs to persist `launched_by_agent_run_id` and
   `launched_by_tool_use_id`.
6. Update workflow role task spawning to pass workflow coordinates into
   `eos-agent-run`.
7. Update subagent/advisor spawning to persist helper runs with parent run
   lineage.
8. Update message-record path resolution to use `AgentRunRecordContext`.
9. Add request/task/workflow execution-tree materialization queries and facade
   DTOs.
10. Only then start Phase 04 engine/run file and crate-boundary movement.

## Progress Tracker

| Item | Status |
| --- | --- |
| Add passive lineage DTOs in `eos-types` | Not started |
| Add `agent_runs` lineage columns and constraints | Not started |
| Add `workflows` launch-lineage columns | Not started |
| Update root request and root run creation flow | Not started |
| Update workflow launch and role task creation flow | Not started |
| Update subagent/advisor helper run creation flow | Not started |
| Add passive `AgentRunRecordContext` for engine loop input | Not started |
| Generate message-record paths from persisted lineage | Not started |
| Add execution-tree materialization queries | Not started |
| Add focused store and materialization tests | Not started |

## Acceptance Criteria

- `Request`, root `Task`, and root task-backed `AgentRun` are persisted before
  the root engine loop starts.
- Every task that enters the agent loop has a main task-backed `AgentRun`.
- Workflow start persists `parent_task_id`, `launched_by_agent_run_id`, and
  `launched_by_tool_use_id` when available.
- Planner, generator, and reducer tasks each produce task-backed agent runs with
  workflow, iteration, attempt, and role coordinates.
- Subagent and advisor launches produce helper `AgentRun` rows with
  `parent_agent_run_id` and no `task_id`.
- `AgentLoopExecutionRequest` carries passive lineage and record context only;
  it does not carry active-run registries or lifecycle finalization handles.
- `messages.jsonl` and `events.jsonl` are created from the request-rooted
  lineage layout above.
- Normal production tests do not create or rely on `parents-missing/`.
- The materialized read model can return
  request -> root task -> main run -> workflows -> iterations -> attempts ->
  planner/generator/reducer task runs, plus subagents and advisors.
- TaskCenter does not grow wrapper nodes or child arrays for workflow,
  iteration, attempt, subagent, or advisor ownership.
- `cargo test -p eos-db` passes for lineage and materialization tests.
- `cargo test -p eos-agent-run` passes for spawn/finalization lineage tests.
- `cargo test -p eos-workflow` passes for workflow launch-lineage tests.
- Phase 04 work does not start unless this phase is implemented or explicitly
  waived in the index.
