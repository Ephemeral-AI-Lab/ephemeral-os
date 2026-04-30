# Complex Task Segmentation and Harness Graph Workflow

This document summarizes how a complex executor task is segmented, routed
through the harness graph runtime, and returned to the requesting executor.

The migration separates three concepts that were previously overloaded:

- `ComplexTaskRequest`: the delegated complex goal requested by an executor.
- `TaskSegment`: one vertical continuation slice of that complex goal.
- `HarnessGraph`: one concrete planner-produced graph execution inside one
  segment.

## Mental model

Complex task progression has three axes:

| Axis | Entity | Trigger | Meaning |
| ---- | ------ | ------- | ------- |
| Request origin | `ComplexTaskRequest` | Executor calls `request_complex_task_solution(goal)` | A new delegated complex goal starts, and the requesting executor pauses. |
| Vertical continuation | `TaskSegment` | Prior segment passes with non-null `continuation_goal` | The same complex request moves to its next sequential slice. |
| Horizontal retry | `HarnessGraph` | A graph fails and segment retry budget remains | The same segment receives a fresh planner-produced graph. |

```mermaid
flowchart TD
    E["Executor task"] -->|"request_complex_task_solution(goal)"| C["ComplexTaskRequest"]
    C --> S1["TaskSegment S1"]
    S1 --> H11["HarnessGraph S1.H1"]
    H11 -->|"failed, retry budget remains"| H12["HarnessGraph S1.H2"]
    H12 -->|"passed with continuation_goal"| S2["TaskSegment S2"]
    S2 --> H21["HarnessGraph S2.H1"]
    H21 -->|"passed with continuation_goal = null"| Close["Close ComplexTaskRequest"]
    Close -->|"resume with report"| E
```

The key rule is:

- A new `TaskSegment` means accepted vertical continuation.
- A new `HarnessGraph` inside the same segment means retry after failure.
- The paused executor resumes only when the whole `ComplexTaskRequest` closes.

## Layer responsibilities

| Layer | Owns | Does not own |
| ----- | ---- | ------------ |
| `ComplexTaskRequestHandler` | Request creation and close, executor pause/resume, initial segment creation, continuation segment creation, final close report to `requested_by_task_id`. | Per-segment retry policy or graph execution. |
| `TaskSegmentManager` | One segment's retry budget, next harness graph creation after failed graphs, segment close, `SegmentCloseReport`. | Request creation, continuation segment creation, or planner/generator/evaluator execution. |
| `HarnessGraphOrchestrator` | One `planner -> generator DAG -> evaluator` execution and graph pass/fail outcome. | Retry, continuation, or request close. |
| Agent roles | Planner, generator executor, verifier, and evaluator terminal submissions inside a graph. | Structural lifecycle decisions. |
| Context engine | Role-specific launch context, durable summaries, and detailed close-report payloads. | Lifecycle policy or source-of-truth state transitions. |

## End-to-end flow

```mermaid
sequenceDiagram
    participant E as Executor
    participant R as ComplexTaskRequestHandler
    participant S as TaskSegmentManager
    participant H as HarnessGraphOrchestrator
    participant A as Agents

    E->>R: request_complex_task_solution(goal)
    R->>R: create ComplexTaskRequest
    R->>R: create TaskSegment S1
    R->>S: spawn manager for S1
    S->>H: create HarnessGraph S1.H1
    H->>A: spawn planner
    A->>H: submit_full_plan or submit_partial_plan
    H->>A: spawn generator DAG
    A->>H: generator/verifier terminal submissions
    H->>A: spawn evaluator after all generators pass
    A->>H: submit_evaluation_success or submit_evaluation_failure
    H->>S: report graph passed or failed
    S->>R: emit SegmentCloseReport when segment closes
    R->>E: resume executor only when request closes
```

## Harness graph lifecycle

`HarnessGraphOrchestrator` owns exactly one graph run. It does not inspect
retry budget and does not create sibling graphs.

```mermaid
flowchart TD
    Start["HarnessGraph starts"] --> Plan["planning: run planner"]
    Plan -->|"valid plan submitted"| Gen["generating: run executor/verifier DAG"]
    Plan -->|"planner ends without valid plan"| PlannerFail["close graph failed: planner_step_budget_exhausted"]
    Gen -->|"any generator failed or blocked after quiescence"| GenFail["close graph failed: generator_failed"]
    Gen -->|"all generators done"| Eval["evaluating: run evaluator"]
    Eval -->|"submit_evaluation_success"| Passed["close graph passed"]
    Eval -->|"submit_evaluation_failure"| EvalFail["close graph failed: evaluator_failed"]
    PlannerFail --> Segment["report graph outcome to TaskSegmentManager"]
    GenFail --> Segment
    Passed --> Segment
    EvalFail --> Segment
```

Generator failure waits for quiescence: failed generators block dependents,
independent siblings may finish, and the graph closes only after all generator
nodes are terminal.

## Segment decision flow

`TaskSegmentManager` reacts to the closed graph. It is the only layer that can
spend segment retry budget.

```mermaid
flowchart TD
    HClose["HarnessGraph closes"] --> Passed{"Graph passed?"}

    Passed -->|"no"| Retry{"Retry budget remains?"}
    Retry -->|"yes"| NextH["Create next HarnessGraph in same TaskSegment"]
    NextH --> RunNext["Run next graph through HarnessGraphOrchestrator"]
    Retry -->|"no"| FailSeg["Close TaskSegment failed"]
    FailSeg --> FailReport["Emit SegmentCloseReport: failed_exhausted(reason)"]

    Passed -->|"yes"| SetCont["Set segment.continuation_goal = graph.continuation_goal"]
    SetCont --> HasCont{"continuation_goal is non-null?"}
    HasCont -->|"yes"| ContinueReport["Emit SegmentCloseReport: success_continue(goal)"]
    HasCont -->|"no"| TerminalReport["Emit SegmentCloseReport: success_terminal"]
```

A passed graph always closes its segment. There is no retry after a passing
graph; graph quality is enforced by the evaluator.

## Request decision flow

`ComplexTaskRequestHandler` reacts only to `SegmentCloseReport`.

```mermaid
flowchart TD
    Report["SegmentCloseReport"] --> Outcome{"Outcome"}
    Outcome -->|"success_continue(goal)"| NextSeg["Create TaskSegment N+1"]
    NextSeg --> Link["previous_segment_id = closed segment"]
    Link --> Goal["goal = continuation_goal"]
    Goal --> NewManager["Spawn fresh TaskSegmentManager"]
    NewManager --> NewGraph["TaskSegmentManager creates initial HarnessGraph"]

    Outcome -->|"success_terminal"| Success["Close ComplexTaskRequest succeeded"]
    Outcome -->|"failed_exhausted(reason)"| Failed["Close ComplexTaskRequest failed"]
    Success --> Resume["Resume requested_by_task_id with close report"]
    Failed --> Resume
```

Continuation does not return to the requesting executor. It keeps the same
complex request open and creates another segment. The requesting executor sees
one final report for the whole complex request.

## Happy path

```mermaid
flowchart TD
    E["Executor decides task is non-atomic"] --> Request["request_complex_task_solution(goal)"]
    Request --> C["Create ComplexTaskRequest C1"]
    C --> S1["Create TaskSegment S1"]
    S1 --> H1["Create HarnessGraph S1.H1"]
    H1 --> P["Planner submits submit_full_plan"]
    P --> G["Generator DAG completes successfully"]
    G --> V["Evaluator submits success"]
    V --> HP["HarnessGraph passes"]
    HP --> SC["TaskSegment closes with continuation_goal = null"]
    SC --> RC["ComplexTaskRequest closes success"]
    RC --> Resume["Executor resumes with complex task success report"]
```

## Partial continuation path

```mermaid
flowchart TD
    P1["Planner in S1.H1 submits submit_partial_plan"] --> CG["S1.H1.continuation_goal = G"]
    CG --> Work1["Generators complete partial DAG"]
    Work1 --> Eval1["Evaluator accepts S1.H1"]
    Eval1 --> CloseS1["TaskSegmentManager closes S1"]
    CloseS1 --> Report["SegmentCloseReport: success_continue(G)"]
    Report --> S2["ComplexTaskRequestHandler creates S2"]
    S2 --> H2["TaskSegmentManager creates S2.H1"]
    H2 --> Gate["S2 planner must submit_full_plan"]
    Gate --> Work2["S2.H1 runs and passes"]
    Work2 --> Done["Request closes and resumes executor"]
```

The segment inherits `continuation_goal` only from the passing graph that
closed it. Failed graphs in the same segment do not propagate their
`continuation_goal` to later graphs.

## Retry-then-pass path

```mermaid
flowchart TD
    H1["S1.H1 runs"] --> Fail["S1.H1 fails"]
    Fail --> Budget{"Retry budget remains?"}
    Budget -->|"yes"| H2["TaskSegmentManager creates S1.H2"]
    H2 --> Fresh["S1.H2 planner decides full or partial independently"]
    Fresh --> Pass["S1.H2 passes"]
    Pass --> Close["S1 closes using S1.H2.continuation_goal"]
    Budget -->|"no"| Exhaust["S1 closes failed and request fails"]
```

Retry history is horizontal inside the segment. The next planner receives the
failure landscape as context, but lifecycle state does not inherit the prior
failed graph's `continuation_goal`.

## Recursive complex task request

Any generator executor can request its own complex task before it edits. That
creates a new request, not a child segment in the outer request.

```mermaid
flowchart TD
    C1["Outer ComplexTaskRequest C1"] --> S1["TaskSegment S1"]
    S1 --> H1["HarnessGraph S1.H1"]
    H1 --> E7["Generator executor E7"]
    E7 -->|"request_complex_task_solution(goal)"| C2["Nested ComplexTaskRequest C2"]
    C2 --> C2S1["C2 TaskSegment S1"]
    C2S1 --> C2H1["C2 HarnessGraph S1.H1"]
    C2H1 --> C2Close["C2 closes"]
    C2Close -->|"resume report"| E7
    E7 --> OuterContinue["E7 continues inside C1.S1.H1"]
```

Only the executor that requested the nested complex task pauses. The nested
request has its own segment chain and retry history.

## Tool and role boundaries

| Role | Scope | Main terminals |
| ---- | ----- | -------------- |
| Planner | One `HarnessGraph` | `submit_full_plan`, `submit_partial_plan` |
| Generator executor | One graph DAG node | `submit_execution_success`, `submit_execution_failure`, `request_complex_task_solution` |
| Generator verifier | One graph DAG node | `submit_verification_success`, `submit_verification_failure` |
| Evaluator | Sink for one graph | `submit_evaluation_success`, `submit_evaluation_failure` |

Important gates:

- `submit_partial_plan` is blocked if the current request already has a prior
  segment with non-null `continuation_goal`.
- malformed planner DAG submissions fail inline without marking the graph
  failed.
- `request_complex_task_solution` is blocked after the executor has edited.
- evaluator spawn is blocked until every generator in the current graph is
  `DONE`.
- next graph creation is blocked once the segment retry budget is exhausted.

## Context engine boundary

The context engine composes structured context packets and summaries for each
role, but lifecycle decisions read structural state:

- planner context includes request goal, segment goal, prior segment summaries,
  and retry failure landscape when applicable;
- generator context includes the planned task spec and dependency summaries;
- evaluator context includes the graph task specification, evaluation criteria,
  and completed generator/verifier summaries;
- request resume context includes the final complex task summary and close
  report for `requested_by_task_id`.

Generated summaries are evidence. They do not decide whether to retry, create
the next segment, or close the request.
