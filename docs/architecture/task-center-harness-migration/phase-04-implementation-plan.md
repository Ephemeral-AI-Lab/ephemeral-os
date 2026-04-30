# Phase 04 - Implementation Plan

Companion to
[`phase-04-complex-task-spawning.md`](./phase-04-complex-task-spawning.md).
This document is the actionable build plan for complex-task spawning, parent
executor request start, close-report delivery, and the persistence API walk over
the durable request / segment / graph model.

Phase 04 does not redefine the Phase 01 durable model, the Phase 02
single-graph orchestrator, or the Phase 03 public terminal-tool layer. It
hardens the pieces that were partially introduced in Phase 03 and closes the
remaining delivery/API gaps.

***

## 1. Scope

Phase 04 implements the runtime bridge from a generator executor's
`request_complex_task_solution(goal)` call to a delegated
`ComplexTaskRequest`, and from the delegated request's close report back to the
requesting generator task.

Deliverables:

1. A single orchestration service for complex-task request startup, replacing
   the tool file's inline request/segment/graph construction.
2. An executor-profile hard gate so only the generator executor agent can call
   `request_complex_task_solution`, `submit_execution_success`, and
   `submit_execution_failure`; verifier terminals remain verifier-only.
3. Delegated request startup that avoids leaving the parent task stuck in
   `waiting_complex_task` when delegated graph startup fails.
4. Initial delegated request creation through `ComplexTaskRequestHandler`, initial
   segment creation through the handler, and initial graph creation through the
   segment-owned `TaskSegmentManager`.
5. Continuation segment creation after `success_continue(goal)`, using the
   existing `ComplexTaskRequestHandler.handle_segment_closed(...)` path.
6. Close-report delivery through one router that calls
   `HarnessGraphOrchestrator.apply_complex_task_close_report(...)` for the
   parent graph.
7. Replay semantics for already-closed complex requests whose parent task is
   still `waiting_complex_task`. Phase 04 must make the report durable and
   replayable; full process resurrection for missing orchestrators remains
   Phase 05.
8. `/api/db/task-center-runs/{id}/graph` backed by
   `complex_task_requests -> task_segments -> harness_graphs`, with task rows
   attached per graph for the UI.
9. Focused tests covering delegated request startup, startup failure, delegated
   success, delegated failure, continuation, retry, replay, profile gates, and
   the graph API route.

Not in scope:

- Rebuilding process-local orchestrators from only persisted rows after a cold
  restart. Phase 05 owns cutover and durable recovery of live graph runtimes.
- Rich context packets, evidence summaries, failure landscapes, or
  `harness_graph_summary_id` population. Phase 06 owns that data.
- Reintroducing `submit_request_plan`, `submit_task_plan`,
  `declare_blocker`, `DeclareBlockerTool`, or conductor flows.
- A frontend implementation. This repository snapshot has no
  `frontend/web/src`; Phase 04 only restores the backend route contract.

***

## 2. Prerequisite Implementation Check

| Prerequisite                     | Current implementation                                                                                                                           | Phase 04 stance                                                                                             |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| Phase 01 durable records         | `ComplexTaskRequestRecord`, `TaskSegmentRecord`, and `HarnessGraphRecord` exist under `backend/src/db/models/`; stores return frozen DTOs.       | Reuse. Add only query helpers needed by delivery and route walking.                                         |
| Request/segment lifecycle        | `ComplexTaskRequestHandler` creates requests and segments, emits close reports through an optional callback, and spawns `TaskSegmentManager`.    | Reuse. Do not create requests or segments from any other class.                                             |
| Segment retry lifecycle          | `TaskSegmentManager` is the only creator of graphs inside one segment and already handles retry vs segment closure.                              | Reuse. Add a narrow deferred-start seam only if needed for safe request startup.                            |
| Phase 02 graph orchestration     | `HarnessGraphOrchestrator` starts planner, schedules generators, spawns evaluator, closes graphs, and keeps `waiting_complex_task` non-terminal. | Reuse. Harden close-report idempotency and validation.                                                      |
| Parent resume entry              | `HarnessGraphOrchestrator.apply_complex_task_close_report(...)` exists and maps delegated success to parent `done`, failure to parent `failed`.  | Keep as the single parent resume method. Add replay/idempotency behavior around it.                         |
| Phase 03 public tool             | `request_complex_task_solution` is registered and currently performs delegated request creation inline.                                          | Refactor the body into a starter. The tool should validate input and delegate.                              |
| Phase 03 planner validation      | Nonblank validation is implemented in `planner/_schemas.py`; valid input values are preserved rather than stripped.                               | No Phase 04 work.                                                                                           |
| Phase 03 executor/verifier split | Current hard gate checks only persisted structural role `generator`; it does not prove executor vs verifier profile.                             | Wave 0 must add profile gates before relying on executor-only delegated request behavior.                   |
| Runtime store initialization     | Server initializes `TaskCenterStore`, `AgentRunStore`, and `ModelStore`; request/segment/graph stores are test-only or runtime-object-only.      | Initialize `ComplexTaskRequestStore`, `TaskSegmentStore`, and `HarnessGraphStore` in app/runtime bootstrap. |
| Graph API route                  | `persistence.py` still returns `{"harness_graphs": []}` with `TODO(phase-04)`.                                                                   | Implement the route from the new schema.                                                                    |

Important current risk:

`request_complex_task_solution` currently marks the parent generator task
`waiting_complex_task` before `segment_manager.create_initial_harness_graph()`
returns. If graph creation or startup raises, the tool returns an inline error
while the parent task can remain waiting. Phase 04 must eliminate this state.

***

## 3. Workflow Diagrams

### 3a. Executor Delegated Request Start

```mermaid
flowchart TD
    Exec["Generator executor task E<br/>status = running"] --> Tool["request_complex_task_solution(goal)"]
    Tool --> Gates["Hard gates<br/>generator role + executor profile + before-edit"]
    Gates --> Resolve["Resolve task -> parent graph -> segment -> request -> runtime"]
    Resolve --> Starter["ComplexTaskRequestStarter.start"]

    Starter --> ParentCheck{"Parent task still running<br/>and no open child request?"}
    ParentCheck -->|"no"| InlineErr["ToolResult is_error=True<br/>parent remains running"]
    ParentCheck -->|"yes"| CreateReq["ComplexTaskRequestHandler.create_complex_task_request"]
    CreateReq --> CreateSeg["ComplexTaskRequestHandler.create_initial_segment"]
    CreateSeg --> Manager["Find registered TaskSegmentManager(S1)"]
    Manager --> CreateGraph["TaskSegmentManager creates H1<br/>inside S1<br/>without starting it yet"]
    CreateGraph --> Wait["Set parent task E<br/>waiting_complex_task"]
    Wait --> Start["Start delegated H1 orchestrator"]
    Start --> Startup{"Delegated graph startup OK?"}
    Startup -->|"no"| Rollback["Rollback parent to running<br/>cancel/compensate delegated artifacts"]
    Rollback --> InlineErr
    Startup -->|"yes"| Success["ToolResult terminal success<br/>agent run stops at delegated request boundary"]
```

The starter owns the ordering. The tool should not call
`ComplexTaskRequestHandler`, `TaskSegmentManager`, or `TaskCenterStore`
directly after Phase 04.

### 3b. Delegated Request Closure and Parent Resume

```mermaid
flowchart TD
    DelegatedGraph["Delegated HarnessGraph closes"] --> SegmentMgr["TaskSegmentManager.handle_harness_graph_closed"]
    SegmentMgr --> Outcome{"Segment outcome"}
    Outcome -->|"failed + budget remains"| Retry["Create next graph in same segment"]
    Retry --> DelegatedGraph
    Outcome -->|"passed + continuation_goal"| Continue["TaskSegmentClosureReport<br/>success_continue(goal)"]
    Outcome -->|"passed + no continuation"| Terminal["TaskSegmentClosureReport<br/>terminal_success"]
    Outcome -->|"failed + budget exhausted"| Failed["TaskSegmentClosureReport<br/>attempt_plan_failed"]

    Continue --> HandlerContinue["ComplexTaskRequestHandler creates S_next<br/>and fresh TaskSegmentManager"]
    HandlerContinue --> NextGraph["TaskSegmentManager creates S_next.H1"]
    NextGraph --> DelegatedGraph

    Terminal --> CloseOK["ComplexTaskRequestHandler closes request success"]
    Failed --> CloseFail["ComplexTaskRequestHandler closes request failed"]
    CloseOK --> Persist["Persist final_outcome"]
    CloseFail --> Persist
    Persist --> Router["ComplexTaskCloseReportRouter.deliver"]
    Router --> ParentOrch["Parent HarnessGraphOrchestrator.apply_complex_task_close_report"]
    ParentOrch --> ParentTask{"Close report outcome"}
    ParentTask -->|"success"| ParentDone["Parent generator task done"]
    ParentTask -->|"failed"| ParentFailed["Parent generator task failed<br/>block descendants"]
    ParentDone --> Dispatch["Parent graph dispatch continues"]
    ParentFailed --> Dispatch
```

Retry and continuation never return to the requesting executor. Only the final
`ComplexTaskRequest` close report resumes the parent generator task.

### 3c. Durable Close-Report Replay

```mermaid
flowchart TD
    Restart["Runtime or route creates stores"] --> Scan["ComplexTaskCloseReportReplay.scan"]
    Scan --> Closed["List closed ComplexTaskRequests"]
    Closed --> Filter{"Parent task status<br/>waiting_complex_task?"}
    Filter -->|"no"| Skip["Already delivered or not deliverable"]
    Filter -->|"yes"| Build["Rebuild ComplexTaskCloseReport<br/>from final_outcome + requested_by_task_id"]
    Build --> Active{"Parent orchestrator active?"}
    Active -->|"no"| Defer["Leave durable final_outcome<br/>for Phase 05/runtime recovery"]
    Active -->|"yes"| Deliver["ComplexTaskCloseReportRouter.deliver"]
```

Phase 04 replay must be idempotent. A report whose parent task is already
`done` or `failed` should be treated as already delivered, not as an error.

### 3d. Persistence Graph Route

```mermaid
flowchart LR
    Run["task_center_run_id"] --> Requests["ComplexTaskRequestStore.list_for_run"]
    Requests --> Segments["TaskSegmentStore.list_for_request"]
    Segments --> Graphs["HarnessGraphStore.list_for_segment"]
    Graphs --> Tasks["TaskCenterStore.list_tasks_for_harness_graph"]
    Tasks --> Response["/api/db/task-center-runs/{id}/graph response"]
```

The route should keep top-level `harness_graphs` for compatibility, while also
returning enough request/segment lineage for the new model.

***

## 4. Runtime Ownership

Ownership remains strict:

| Entity / transition                               | Owner                                    |
| ------------------------------------------------- | ---------------------------------------- |
| `ComplexTaskRequest` creation                     | `ComplexTaskRequestHandler` only         |
| `TaskSegment` creation                            | `ComplexTaskRequestHandler` only         |
| `TaskSegmentManager` spawning                     | `ComplexTaskRequestHandler` only         |
| `HarnessGraph` creation inside a segment          | that segment's `TaskSegmentManager` only |
| Initial delegated complex-task request start      | new `ComplexTaskRequestStarter`          |
| Close-report routing to parent graph              | new `ComplexTaskCloseReportRouter`       |
| Parent generator state mutation from close report | parent `HarnessGraphOrchestrator` only   |
| Frontend graph serialization                      | persistence router helper functions only |

The coordinator composes existing owners; it must not bypass them.

***

## 5. Folder Layout

```text
backend/src/task_center/complex_task/
|-- request.py                         # existing DTOs; keep ComplexTaskCloseReport here
|-- handler.py                         # edit only if callback/replay hook needs shaping
|-- request_start.py                   # NEW: ComplexTaskRequestStarter
|-- close_report_delivery.py           # NEW: ComplexTaskCloseReportRouter + replay helpers
`-- validation.py                      # existing request invariants

backend/src/task_center/segment/
`-- manager.py                         # EDIT: optional deferred graph start seam

backend/src/task_center/harness_graph/
|-- orchestrator.py                    # EDIT: close-report idempotency/validation
|-- runtime.py                         # EDIT: ensure manager registry/lifecycle config stay required for Phase 04 paths
`-- factory.py                         # existing factory used by the coordinator

backend/src/tools/submission/hooks/
|-- harness_role_gate.py               # existing structural role gate
`-- harness_agent_profile_gate.py      # NEW: executor vs verifier profile gate

backend/src/tools/submission/main_agent/generator/
|-- executor/
|   |-- request_complex_task_solution.py # EDIT: delegate to request starter
|   |-- submit_execution_success.py      # EDIT: add executor profile gate
|   `-- submit_execution_failure.py      # EDIT: add executor profile gate
`-- verifier/
    |-- submit_verification_success.py   # EDIT: add verifier profile gate
    `-- submit_verification_failure.py   # EDIT: add verifier profile gate

backend/src/db/stores/
|-- complex_task_request_store.py      # EDIT: list_for_run + closed-report queries
|-- task_center_store.py               # EDIT: compare-and-set status helper
|-- task_segment_store.py              # existing list_for_request
`-- harness_graph_store.py             # existing list_for_segment

backend/src/server/
|-- app_factory.py                     # EDIT: initialize and pass request/segment/graph stores
`-- routers/persistence.py             # EDIT: implement graph route from new schema

backend/tests/task_center/lifecycle/
|-- test_phase04_complex_task_request_start.py
|-- test_phase04_close_report_delivery.py
`-- test_phase04_replay.py

backend/tests/test_tools/
|-- test_submission_tool_gates.py      # EDIT: executor/verifier profile coverage
`-- test_submission_terminal_routing.py # EDIT: request start delegates to starter

backend/tests/server/
`-- test_persistence_graph_route.py
```

***

## 6. Files, Classes, and Functions

### 6a. `task_center/complex_task/request_start.py`

Create a starter that turns an accepted executor tool call into a durable
delegated request plus initial graph. This is not a domain model; it is the
use-case boundary that composes the existing request, segment, graph, and parent
task owners.

```python
@dataclass(frozen=True, slots=True)
class StartedComplexTaskRequest:
    parent_task_id: str
    parent_harness_graph_id: str
    complex_task_request_id: str
    initial_segment_id: str
    initial_harness_graph_id: str
    goal: str


class ComplexTaskRequestStarter:
    def __init__(self, *, runtime: HarnessGraphRuntime) -> None: ...

    def start(
        self,
        *,
        task_center_run_id: str,
        parent_task_id: str,
        parent_harness_graph_id: str,
        goal: str,
    ) -> StartedComplexTaskRequest: ...
```

Required behavior:

1. Re-read the parent task from `runtime.task_store`.
2. Require parent status `running` and parent graph id matching the resolved
   submission context.
3. Reject if `ComplexTaskRequestStore.list_for_executor_task(parent_task_id)`
   contains any `OPEN` request.
4. Build a `ComplexTaskRequestHandler` with:
   - `runtime.request_store`
   - `runtime.segment_store`
   - `runtime.graph_store`
   - `runtime.manager_registry`
   - `runtime.lifecycle_config`
   - `ComplexTaskCloseReportRouter(runtime).deliver`
   - `make_harness_graph_orchestrator_factory(...)`
5. Call `create_complex_task_request(...)`.
6. Call `create_initial_segment(...)`.
7. Get the registered `TaskSegmentManager` for the new segment.
8. Create the initial delegated graph through the manager without starting its
   orchestrator yet.
9. Mark the parent task `waiting_complex_task` with a delegated request summary that
   names the delegated request, segment, graph, and goal.
10. Start the delegated graph orchestrator.
11. If startup fails, roll the parent task back to `running` and compensate the
    delegated request/segment/graph state.
12. Return `StartedComplexTaskRequest`.

Startup failure handling:

- The parent task must still be `running` when the tool returns an inline
  error.
- If a delegated request/segment was created before the failure, mark the request
  and segment `cancelled` or otherwise make the compensation explicit in tests.
- Do not leave an open request with no running graph unless the plan also adds
  a replay/recovery path for that exact state.

Recommended implementation:

Add a narrow deferred-start seam to `TaskSegmentManager` so the coordinator can
separate durable graph insertion from orchestrator startup:

```python
class TaskSegmentManager:
    def create_initial_harness_graph(
        self, *, start_orchestrator: bool = True
    ) -> HarnessGraph: ...

    def start_harness_graph(self, harness_graph_id: str) -> None: ...
```

Default behavior stays compatible with existing Phase 01/02 tests. The
starter should use `start_orchestrator=False`, persist the parent delegated
request summary, then call `start_harness_graph(...)`. This prevents a synchronously
closing test orchestrator from delivering a close report before the parent task
has entered `waiting_complex_task`.

### 6b. `task_center/complex_task/close_report_delivery.py`

Create one delivery service for final reports.

```python
CloseReportDeliveryStatus = Literal[
    "delivered",
    "already_delivered",
    "deferred_no_orchestrator",
]


@dataclass(frozen=True, slots=True)
class CloseReportDeliveryResult:
    status: CloseReportDeliveryStatus
    requested_by_task_id: str
    parent_harness_graph_id: str | None
    reason: str | None = None


class ComplexTaskCloseReportRouter:
    def __init__(self, *, runtime: HarnessGraphRuntime) -> None: ...

    def deliver(
        self, report: ComplexTaskCloseReport
    ) -> CloseReportDeliveryResult: ...
```

Delivery algorithm:

1. Load `report.requested_by_task_id` from `runtime.task_store`.
2. If the task does not exist, raise `GraphInvariantViolation`.
3. Read `task_center_harness_graph_id` from the parent task.
4. If the parent task is `done` or `failed` and its summaries already contain
   this `complex_task_request_id`, return `already_delivered`.
5. If the parent task is not `waiting_complex_task`, raise
   `GraphInvariantViolation` because the report is not applicable.
6. If no parent orchestrator is registered for the graph id, return
   `deferred_no_orchestrator`; the durable `final_outcome` remains the replay
   source.
7. Call `orchestrator.apply_complex_task_close_report(report)`.
8. Return `delivered`.

Replay helper:

```python
def build_close_report_from_request(
    request: ComplexTaskRequest,
) -> ComplexTaskCloseReport | None: ...


def deliver_pending_complex_task_close_reports(
    *,
    runtime: HarnessGraphRuntime,
    task_center_run_id: str | None = None,
) -> list[CloseReportDeliveryResult]: ...
```

`build_close_report_from_request(...)` reconstructs a report from:

- `ComplexTaskRequest.id`
- `ComplexTaskRequest.requested_by_task_id`
- `ComplexTaskRequest.final_outcome["outcome"]`
- `ComplexTaskRequest.final_outcome["final_segment_id"]`
- `ComplexTaskRequest.final_outcome["final_harness_graph_id"]`

If `final_outcome` is missing or malformed for a closed request, raise
`GraphInvariantViolation`.

### 6c. `task_center/harness_graph/orchestrator.py`

Edit `apply_complex_task_close_report(...)` in place.

Current behavior is the right core behavior:

- delegated success -> parent generator `done`
- delegated failure -> parent generator `failed`
- failure blocks pending descendants and lets graph quiescence close the graph

Add:

1. Idempotency: if the task is already `done` or `failed` with a summary whose
   payload contains the same `complex_task_request_id`, return without
   mutation.
2. Stronger summary payload:

```python
{
    "outcome": report.outcome,
    "summary": "...",
    "payload": {
        "complex_task_close_report": asdict(report),
        "submission_kind": "complex_task_close_report",
    },
}
```

1. A tight invariant when the parent task belongs to another graph. The current
   `assert_generator_task_for_submission(...)` already does most of this; keep
   that as the durable guard.

Do not make the orchestrator create delegated requests or segments.

### 6d. `tools/submission/main_agent/generator/request_complex_task_solution.py`

Keep the public schema and terminal registration, but reduce the body to
validation plus delegation.

```python
class RequestComplexTaskSolutionInput(BaseModel):
    goal: str = Field(..., min_length=1)

    @field_validator("goal")
    @classmethod
    def _validate_goal(cls, value: str) -> str: ...
```

The validator rejects empty or whitespace-only goals and returns the original
value unchanged. The request-start path should not strip or normalize the durable
goal.

Tool handler flow:

1. Resolve `HarnessSubmissionContext`.
2. Confirm the persisted task is `running`.
3. Instantiate `ComplexTaskRequestStarter(runtime=submission_context.runtime)`.
4. Call `start(...)`.
5. Return terminal `ToolResult` with metadata from `StartedComplexTaskRequest`.

The tool should not contain delivery callbacks, handler factories, registry
lookups, or direct task status mutation after Phase 04.

### 6e. `tools/submission/hooks/harness_agent_profile_gate.py`

Add a gate for profile-level roles such as `executor` and `verifier`.

```python
@dataclass(frozen=True, slots=True)
class HarnessAgentProfileGate:
    target_tool: str
    expected_profile_role: str

    async def run(
        self,
        tool_input: BaseModel,
        context: ToolExecutionContextService,
    ) -> HookResult[Any]: ...
```

Read the profile role from `ExecutionMetadata["role"]`, which is already set
from the launched `AgentDefinition.role` by `engine/runtime/agent.py`.

Attach it to:

| Tool                            | Expected profile role |
| ------------------------------- | --------------------- |
| `request_complex_task_solution` | `executor`            |
| `submit_execution_success`      | `executor`            |
| `submit_execution_failure`      | `executor`            |
| `submit_verification_success`   | `verifier`            |
| `submit_verification_failure`   | `verifier`            |

Keep `HarnessRoleGate(..., HarnessTaskRole.GENERATOR)` on all of these tools.
The structural role proves the persisted graph task is a generator; the profile
gate proves the currently running agent has the matching executor/verifier tool
contract.

### 6f. Store Helpers

`backend/src/db/stores/complex_task_request_store.py`

```python
def list_for_run(self, task_center_run_id: str) -> list[ComplexTaskRequest]: ...

def list_closed_for_run(
    self, task_center_run_id: str
) -> list[ComplexTaskRequest]: ...

def list_closed(self) -> list[ComplexTaskRequest]: ...

def set_status(
    ...,
    # existing method remains
) -> ComplexTaskRequest: ...
```

Use `list_for_run(...)` for the route and `list_closed_for_run(...)` for
targeted replay. `list_closed()` is useful for process-level replay when a
runtime has no specific run id.

Optional compensation helpers if the coordinator needs them:

```python
def cancel(self, request_id: str, *, closed_at: datetime | None = None) -> ComplexTaskRequest: ...
```

`backend/src/db/stores/task_segment_store.py`

Optional compensation helper:

```python
def cancel(self, segment_id: str, *, closed_at: datetime | None = None) -> TaskSegment: ...
```

`backend/src/db/stores/task_center_store.py`

Add a compare-and-set helper to avoid racing duplicate request starts or stale
delivery:

```python
def set_task_status_if_current(
    self,
    task_id: str,
    *,
    expected_status: str,
    status: str,
    summary: SerializedRow | None = None,
) -> SerializedRow | None: ...
```

Return `None` when the current status does not match `expected_status`.
Callers should convert that to a `GraphInvariantViolation` with a clear
message.

### 6g. Runtime Store Initialization

`backend/src/server/app_factory.py`

Add module-level stores:

```python
complex_task_request_store = ComplexTaskRequestStore()
task_segment_store = TaskSegmentStore()
harness_graph_store = HarnessGraphStore()
```

Initialize them in `ensure_runtime_stores_ready(...)` when a session factory is
available. Pass them to `create_persistence_router(...)`.

This does not by itself build the TaskCenter runtime used by spawned harness
agents. It only makes the persistence API route and replay helpers able to use
the same DB-backed stores.

### 6h. Persistence Router

`backend/src/server/routers/persistence.py`

Change the factory signature:

```python
def create_persistence_router(
    task_center_store: TaskCenterStore,
    agent_run_store: AgentRunStore,
    complex_task_request_store: ComplexTaskRequestStore,
    task_segment_store: TaskSegmentStore,
    harness_graph_store: HarnessGraphStore,
) -> APIRouter: ...
```

Route behavior for `/api/db/task-center-runs/{task_center_run_id}/graph`:

1. Return `503` if any store is not ready.
2. Load all complex-task requests for the run ordered by creation time.
3. For each request, load segments ordered by `sequence_no`.
4. For each segment, load graphs ordered by `graph_sequence_no`.
5. For each graph, attach `TaskCenterStore.list_tasks_for_harness_graph(...)`.
6. Return:

```python
{
    "complex_task_requests": [...],
    "task_segments": [...],
    "harness_graphs": [
        {
            "id": "...",
            "task_center_run_id": "...",
            "complex_task_request_id": "...",
            "requested_by_task_id": "...",
            "request_status": "open|succeeded|failed|cancelled",
            "task_segment_id": "...",
            "segment_sequence_no": 1,
            "segment_status": "open|succeeded|failed|cancelled",
            "graph_sequence_no": 1,
            "stage": "planning|generating|evaluating|closed",
            "status": "running|passed|failed",
            "planner_task_id": "...",
            "generator_task_ids": [...],
            "evaluator_task_id": "...",
            "continuation_goal": None,
            "fail_reason": None,
            "tasks": [...],
            "created_at": "...",
            "updated_at": "...",
            "closed_at": None,
        }
    ],
}
```

Keep the route name and the top-level `harness_graphs` key stable.

***

## 7. Build Waves

### Wave 0 - Close Phase 03 Gate Gaps

1. Add `HarnessAgentProfileGate`.
2. Attach it to executor and verifier tools.
3. Add tests:
   - verifier profile cannot call `submit_execution_success`
   - verifier profile cannot call `request_complex_task_solution`
   - executor profile cannot call verifier terminals
4. Run the focused submission gate tests.

### Wave 1 - Extract Request Starter

1. Add `ComplexTaskRequestStarter` and `StartedComplexTaskRequest`.
2. Move handler factory and close-report sink construction out of
   `request_complex_task_solution.py`.
3. Add duplicate-open-request and parent-status checks.
4. Refactor the tool to delegate.
5. Preserve existing successful delegated request behavior in
   `test_request_complex_task_solution_starts_delegated_request`.

### Wave 2 - Safe Startup and Compensation

1. Decide the minimal safe seam:
   - preferred: `TaskSegmentManager.create_initial_harness_graph(start_orchestrator=False)`
     plus `start_harness_graph(...)`;
   - acceptable: a coordinator-level compensation path with explicit cancel
     tests.
2. Ensure startup failure returns an inline tool error and leaves the parent
   task `running`.
3. Ensure no orphan open request is left without an initial graph.
4. Add tests using a factory that raises during delegated graph startup.

### Wave 3 - Close-Report Router and Replay

1. Add `ComplexTaskCloseReportRouter`.
2. Wire `ComplexTaskRequestHandler.deliver_close_report` to the router from
   the coordinator-created handler.
3. Add idempotency to `HarnessGraphOrchestrator.apply_complex_task_close_report`.
4. Add `build_close_report_from_request(...)` and
   `deliver_pending_complex_task_close_reports(...)`.
5. Add tests:
   - final success resumes parent generator and dispatches evaluator
   - final failure blocks descendants and closes/advances parent graph
   - replay delivers a closed request whose parent is still waiting
   - replay skips already-delivered reports
   - replay defers when parent orchestrator is not active

### Wave 4 - Continuation and Retry Integration

1. Add an end-to-end lifecycle test where delegated segment 1 passes with
   `continuation_goal`, segment 2 passes terminally, and only then the parent
   generator resumes.
2. Add an end-to-end lifecycle test where delegated graph 1 fails, graph 2 passes
   within the same segment, and only the final request close resumes the
   parent.
3. Assert continuation creates a new `TaskSegment`, while retry creates a new
   `HarnessGraph` in the same segment.

### Wave 5 - Persistence Route

1. Initialize request/segment/graph stores in `app_factory.py`.
2. Expand `create_persistence_router(...)` dependencies.
3. Implement route serialization helpers.
4. Add route tests with:
   - one request, one segment, one graph
   - one request, two continuation segments
   - one segment with two retry graphs
   - delegated request linked by `requested_by_task_id`

### Wave 6 - Verification and Docs Sync

1. Update `phase-04-complex-task-spawning.md` only if the
   implementation changes the target behavior.
2. Add `phase-04-implementation-report.md` after implementation.
3. Run:

```bash
uv run pytest backend/tests/test_tools/test_submission_tool_gates.py backend/tests/test_tools/test_submission_terminal_routing.py -q
uv run pytest backend/tests/task_center/lifecycle/test_phase04_complex_task_request_start.py backend/tests/task_center/lifecycle/test_phase04_close_report_delivery.py backend/tests/task_center/lifecycle/test_phase04_replay.py -q
uv run pytest backend/tests/server/test_persistence_graph_route.py -q
uv run pytest backend/tests/task_center -q
uv run ruff check backend/src/task_center backend/src/tools/submission backend/src/server backend/tests
uv run mypy --config-file backend/mypy.ini backend/src/task_center backend/src/agents
```

1. If code files changed, run:

```bash
graphify update .
```

***

## 8. Test Plan

| Test                                                                            | Purpose                                                                                                |
| ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `test_executor_profile_required_for_complex_task_request`                       | Verifier-owned generator run cannot call `request_complex_task_solution`.                              |
| `test_executor_profile_required_for_execution_terminals`                        | Verifier cannot submit executor terminals even though persisted role is `generator`.                   |
| `test_verifier_profile_required_for_verification_terminals`                     | Executor cannot submit verifier terminals.                                                             |
| `test_request_start_creates_request_segment_graph_and_marks_parent_waiting`     | Main happy path from tool/starter.                                                                     |
| `test_request_start_startup_failure_leaves_parent_running`                      | Regression for Phase 03 review finding.                                                               |
| `test_request_start_rejects_second_open_child_request_for_same_executor`        | Prevent duplicate delegated requests from one running task.                                            |
| `test_delegated_success_close_report_marks_parent_done`                         | Final delegated success resumes parent graph.                                                          |
| `test_delegated_failure_close_report_marks_parent_failed_and_blocks_dependents` | Final delegated failure propagates as generator failure.                                               |
| `test_delegated_continuation_waits_until_final_segment`                         | `success_continue` creates a later segment and does not resume parent early.                           |
| `test_delegated_retry_waits_until_final_graph`                                  | Failed first graph with remaining budget retries inside same segment and does not resume parent early. |
| `test_replay_delivers_closed_request_to_waiting_parent`                         | Durable final outcome can be replayed after callback was missed.                                       |
| `test_replay_is_idempotent_after_delivery`                                      | Duplicate replay does not append duplicate parent summaries.                                           |
| `test_replay_defers_without_parent_orchestrator`                                | Phase 04 does not pretend to recover absent process-local orchestrators.                               |
| `test_graph_route_walks_request_segment_graph_schema`                           | API returns harness graphs through the new stores.                                                     |
| `test_graph_route_includes_tasks_per_graph`                                     | UI can render task rows attached to each graph.                                                        |

***

## 9. Exit Criteria

Phase 04 is complete when:

- `request_complex_task_solution` creates a delegated `ComplexTaskRequest`, its
  initial `TaskSegment`, and its initial `HarnessGraph` through the proper
  owners.
- The requesting executor task enters `waiting_complex_task` only when the
  delegated graph startup has succeeded or when the implementation has a tested
  rollback path.
- Delegated request success becomes the requesting generator task's final success
  result.
- Delegated request failure becomes the requesting generator task's final failure
  result and blocks downstream generator dependents.
- Continuation creates later ordered `TaskSegment` records and does not resume
  the parent until the whole complex request closes.
- Retry creates later `HarnessGraph` records inside the same segment and does
  not resume the parent until the request closes.
- Closed request reports can be replayed idempotently when the parent task is
  still `waiting_complex_task`.
- `/api/db/task-center-runs/{id}/graph` returns graph data from
  `complex_task_requests`, `task_segments`, and `harness_graphs` instead of an
  empty stub.
- Focused tests, `backend/tests/task_center`, ruff, and strict mypy for
  `task_center` and `agents` are green.
