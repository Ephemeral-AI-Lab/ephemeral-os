# TaskCenter Class Inventory

Source scope: `backend/src/task_center` plus TaskCenter persistence models and
store under `backend/src/db`.

This is a class-level reference for the current TaskCenter implementation. It
lists each class, where it lives, what it owns, its fields, and its main
methods. Module-level helper functions are included at the end because several
class methods delegate core behavior to them.

## Class Map

| Area | Classes |
| --- | --- |
| Core model | `Status`, `TaskSummary`, `Task`, `HarnessGraph` |
| Graph/runtime | `TaskGraph`, `TaskCenter`, `Orchestrator`, `MaterializationFailure`, `RunController` |
| Launch contexts | `DependencyBundle`, `ExecutorLaunchContext`, `VerifierLaunchContext`, `PlannerLaunchContext`, `EvaluatorLaunchContext`, `AdvisorLaunchContext` |
| Advisor/errors | `AdvisorAccept`, `TaskCenterError`, `BlockedTerminal`, `PlanValidationError` |
| Persistence | `TaskCenterRequestRecord`, `TaskCenterRunRecord`, `TaskCenterTaskRecord`, `TaskCenterHarnessGraphRecord`, `TaskCenterStore` |

## Type Aliases And Literals

| Name | Definition | Purpose |
| --- | --- | --- |
| `TaskId` | `str` | In-run task identifier. |
| `HarnessGraphId` | `str` | In-run harness graph identifier. |
| `TaskRole` | `Literal["executor", "planner", "verifier", "evaluator", "advisor"]` | Role stored on every `Task`. |
| `GeneratorRole` | `Literal["executor", "verifier"]` | Roles a planner may emit in a DAG. |
| `SummaryKind` | `Literal[...]` | Allowed `TaskSummary.kind` categories. |
| `SpawnFunc` | `Callable[[TaskId, TaskCenter, str | None], Awaitable[None]]` | Async adapter used by `TaskCenter.run_query`. |

## Core Model

### `Status`

Source: `backend/src/task_center/model/task.py`  
Kind: `str`, `Enum`  
Responsibility: defines every lifecycle state used by `TaskGraph` transition
rules.

Fields:

- `PENDING = "pending"`: created and waiting for dependency completion or readiness promotion.
- `READY = "ready"`: eligible for dispatcher pickup.
- `RUNNING = "running"`: owned by a live spawned agent coroutine.
- `HANDOFF = "handoff"`: planner/root task has delegated work to a `HarnessGraph` and waits for closure.
- `DONE = "done"`: terminal success.
- `FAILED = "failed"`: terminal failure.
- `FIXING = "fixing"`: verifier failed and a bounded fix-executor is running.

Main functions:

- None. Legal transition edges are defined in `TaskGraph`.

### `TaskSummary`

Source: `backend/src/task_center/model/task.py`  
Kind: `dataclass`  
Responsibility: append-only event or terminal summary attached to a `Task`.

Fields:

- `kind: SummaryKind`: category such as `success`, `failure`, `handoff`, `segment_success`, or `advisor_feedback`.
- `text: str`: human-readable summary payload.
- `source_task_id: TaskId`: task that produced the summary.
- `created_at: float = field(default_factory=time.time)`: creation timestamp.

Main functions:

- None. Pure data container.

### `Task`

Source: `backend/src/task_center/model/task.py`  
Kind: `dataclass`  
Responsibility: one node in the request-scoped graph. Role and status determine
dispatch behavior, terminal tool eligibility, and lifecycle handling.

Fields:

- `id: TaskId`: in-run task id. Persisted ids are prefixed by `run_id`.
- `role: TaskRole`: one of `executor`, `planner`, `verifier`, `evaluator`, or `advisor`.
- `input: str`: role-specific work or prompt input.
- `status: Status`: current lifecycle state.
- `task_center_harness_graph_id: HarnessGraphId | None = None`: containing harness graph; absent for root executor and advisor tasks.
- `needs: frozenset[TaskId] = field(default_factory=frozenset)`: direct dependency ids that must be `DONE` before readiness.
- `summaries: list[TaskSummary] = field(default_factory=list)`: append-only summaries for this task.
- `created_at: float = field(default_factory=time.time)`: creation timestamp.
- `fix_target_id: TaskId | None = None`: verifier targeted by a fix-executor.
- `spawn_reason: str | None = None`: special dispatch tag, currently `fix_verification`.

Main functions:

- None. Mutation is coordinated by `TaskGraph`, `TaskCenter`, and role lifecycle modules.

### `HarnessGraph`

Source: `backend/src/task_center/model/harness.py`  
Kind: `dataclass`  
Responsibility: one planner-led decomposition: root task, planner, generator
DAG nodes, evaluator, and partial-plan chain metadata.

Fields:

- `id: HarnessGraphId`: in-run graph id.
- `run_id: str`: persistence run id when available.
- `root_task_id: TaskId`: executor/evaluator task that requested planning.
- `planner_task_id: TaskId`: legacy planner slot.
- `root_goal: str = ""`: root input captured as anti-drift context.
- `request_plan_note: str = ""`: decomposition request passed to planner.
- `handoff_plan_note: str = ""`: legacy planner handoff note.
- `evaluator_note: str = ""`: evaluation specification or evaluator input.
- `evaluator_task_id: TaskId | None = None`: legacy evaluator slot.
- `executor_task_ids: list[TaskId] = field(default_factory=list)`: legacy child id list; currently also used for generator children.
- `planner: TaskId = ""`: current structural planner slot.
- `dag_nodes: list[TaskId] = field(default_factory=list)`: planner-emitted executor/verifier DAG ids.
- `evaluator: TaskId | None = None`: current structural evaluator slot.
- `plan_shape: Literal["full", "partial"] | None = None`: planner terminal shape.
- `what_to_do_next: str = ""`: partial-plan continuation directive.
- `prior_graph_id: HarnessGraphId | None = None`: previous graph in a partial-plan chain.

Main functions:

- `__post_init__() -> None`: keeps current structural slots synced with legacy `planner_task_id`, `evaluator_task_id`, and `executor_task_ids`.

## Graph And Runtime

### `TaskGraph`

Source: `backend/src/task_center/graph/store.py`  
Kind: `dataclass`  
Responsibility: in-memory graph state plus status transition validation.

Fields:

- `tasks: dict[TaskId, Task] = field(default_factory=dict)`: all tasks in the current request/run.
- `harness_graphs: dict[HarnessGraphId, HarnessGraph] = field(default_factory=dict)`: all harness graphs opened during the request/run.

Main functions:

- `add(task: Task) -> None`: insert a task; rejects duplicate ids.
- `get(task_id: TaskId) -> Task`: return a task or raise `TaskCenterError`.
- `add_harness_graph(graph: HarnessGraph) -> None`: insert a harness graph; rejects duplicate graph ids.
- `get_harness_graph(graph_id: HarnessGraphId) -> HarnessGraph`: return a graph or raise `TaskCenterError`.
- `ready_tasks() -> list[Task]`: return tasks that are already `READY` plus `PENDING` tasks whose direct dependencies are all `DONE`.
- `transition(task_id: TaskId, new_status: Status) -> None`: validate the allowed status edge and mutate `task.status`.

### `TaskCenter`

Source: `backend/src/task_center/runtime/task_center.py`  
Kind: runtime class  
Responsibility: request-scoped store, persistence bridge, terminal-tool facade,
and dispatcher loop.

Fields:

- `_graph: TaskGraph`: current in-memory task/harness graph.
- `_spawn_func: SpawnFunc | None`: async adapter used to run each task.
- `_wakeup: asyncio.Event`: dispatcher wake signal after state changes.
- `_counter: itertools.count`: task id counter.
- `_graph_counter: itertools.count`: harness graph id counter.
- `_id_prefix: str = "t"`: prefix for generated task ids.
- `_on_event: Callable[[Any], Awaitable[None]] | None`: optional runtime event callback.
- `request_id: str | None`: persisted request id.
- `run_id: str | None`: persisted run id used for saved ids.
- `_task_center_store: TaskCenterStore | None`: optional SQL persistence store.
- `_advisor_accepts: dict[TaskId, AdvisorAccept]`: created lazily by `pre_hooks` for gated terminal approvals.

Main functions:

- `set_event_callback(on_event) -> None`: replace the async event callback.
- `_emit_event(event) -> None`: invoke the event callback if one is configured.
- `graph -> TaskGraph`: expose the current `TaskGraph`.
- `_new_id() -> TaskId`: generate a new task id.
- `_new_graph_id() -> HarnessGraphId`: generate a new harness graph id.
- `persisted_task_id(task_id) -> str`: prefix a task id with `run_id` for persistence.
- `persisted_graph_id(graph_id) -> str`: prefix a graph id with `run_id` for persistence.
- `_persist_task(task) -> None`: serialize one `Task` through `TaskCenterStore`.
- `_persist_harness_graph(graph) -> None`: serialize one `HarnessGraph` through `TaskCenterStore`.
- `_persist_all() -> None`: persist all current tasks and harness graphs.
- `_finish_persisted_run(status) -> None`: mark the persisted run finished.
- `_create_task(...) -> Task`: generic task creation primitive.
- `_create_executor(...) -> Task`: create an executor task.
- `_create_planner(...) -> Task`: create a planner task, always `READY`.
- `_create_verifier(...) -> Task`: create a verifier task.
- `_create_evaluator(...) -> Task`: create an evaluator task, always `PENDING`.
- `_create_advisor(...) -> Task`: create a transient advisor task, always `READY`.
- `create_advisor(...) -> Task`: build advisor prompt, create advisor, persist it, and wake the dispatcher.
- `_open_graph(...) -> HarnessGraph`: create and insert a `HarnessGraph` for a planning request.
- `_create_root_executor(prompt) -> Task`: delegate root executor creation to executor lifecycle.
- `dependency_blocked_descendants(task_id) -> list[Task]`: delegate descendant query to graph helpers.
- `is_harness_graph_ready_for_evaluation(graph_id) -> bool`: delegate evaluator readiness query to graph helpers.
- `submit_task_success(task_id, summary) -> None`: route executor/evaluator success terminals to lifecycle modules.
- `submit_task_failure(task_id, summary) -> None`: route executor failure terminal.
- `submit_evaluation_failure(task_id, summary) -> None`: route evaluator failure terminal.
- `submit_evaluation_success(task_id, summary) -> None`: evaluator-specific success terminal.
- `submit_verification_success(task_id, summary) -> None`: route verifier success terminal.
- `submit_verification_failure(task_id, summary) -> None`: route verifier failure terminal; failure may spawn a fix-executor.
- `submit_advisor_feedback(task_id, verdict, reason) -> None`: route advisor terminal.
- `request_plan(task_id, request_plan_note) -> None`: route a planning request.
- `submit_plan_handoff(...) -> None`: legacy planner terminal route.
- `submit_full_plan(...) -> MaterializationFailure | None`: structured full-plan planner terminal.
- `submit_partial_plan(...) -> MaterializationFailure | None`: structured partial-plan planner terminal.
- `_notify_child_terminal_changed() -> None`: wake dispatcher after child terminal changes.
- `_mark_terminal(task, terminal) -> None`: transition a task to terminal status if not already there.
- `run_query(prompt, sandbox_id=None) -> Task`: create root, dispatch ready work until root terminal, persist final state, and return root task.
- `_run_one(task_id, sandbox_id) -> None`: run spawn adapter and handle crashes or silent exits.
- `_handle_silent_termination(task, reason) -> None`: route silent exit handling by task role.

### `MaterializationFailure`

Source: `backend/src/task_center/runtime/orchestrator.py`  
Kind: `dataclass(frozen=True)`  
Responsibility: structured non-exception rejection returned to planner
`submit_full_plan` or `submit_partial_plan`.

Fields:

- `code: str`: machine-readable reason. Current values include `empty_dag`, `duplicate_ids`, `missing_details`, `unknown_role`, `unknown_dep`, `cycle`, and `verifier_sink`.
- `message: str`: human-readable tool feedback.

Main functions:

- None. Pure data container.

### `Orchestrator`

Source: `backend/src/task_center/runtime/orchestrator.py`  
Kind: `dataclass(frozen=True)`  
Responsibility: facade for one `HarnessGraph`. Materializes planner DAGs and
closes graph outcomes.

Fields:

- `graph_id: HarnessGraphId`: controlled harness graph id.
- `tc: TaskCenter`: owning request-scoped `TaskCenter`.

Main functions:

- `spawn(tc, root_task_id, request_plan_note, prior_graph_id=None) -> Orchestrator`: create planner id, open graph, create `READY` planner, and return the facade.
- `graph -> HarnessGraph`: resolve the current graph.
- `root_task -> Task`: resolve the graph root task.
- `planner -> Task`: resolve the planner task.
- `evaluator -> Task | None`: resolve the evaluator task if one exists.
- `dag_nodes -> list[Task]`: resolve all planner-emitted DAG nodes.
- `materialize_full_plan(...) -> MaterializationFailure | None`: validate DAG, create generator children/evaluator, mark `plan_shape="full"`.
- `materialize_partial_plan(...) -> MaterializationFailure | None`: same as full plan plus store `what_to_do_next` and mark `plan_shape="partial"`.
- `_materialize_dag(...) -> None`: transition planner to `HANDOFF`, create executor/verifier nodes in topological order, then create evaluator with sink deps.
- `close_partial_success(summary) -> Orchestrator`: mark planner done, append `segment_success` to root, and spawn a continuation graph.
- `build_continuation_note() -> str`: build chained partial-plan prompt from prior graphs.
- `create_harness_fix_executor(verifier_id, failure_summary) -> Task`: create bounded repair executor for a failed verifier.
- `close_success(summary) -> None`: delegate graph success closure to evaluator lifecycle.
- `close_failure(summary) -> None`: delegate graph failure closure to evaluator lifecycle.

### `RunController`

Source: `backend/src/task_center/runtime/run_controller.py`  
Kind: `dataclass`  
Responsibility: run-level helper for the root executor, which lives outside any
`HarnessGraph`.

Fields:

- `tc: TaskCenter`: owning `TaskCenter`.
- `root_task_id: TaskId | None = None`: root executor id after `start()`.

Main functions:

- `start(prompt: str) -> Task`: create root executor `READY`, persist it, and set the persisted run root when available.
- `root_task -> Task`: resolve root task or raise if not started.
- `is_done() -> bool`: return true when root is `DONE` or `FAILED`.

## Launch Context Classes

### `DependencyBundle`

Source: `backend/src/task_center/harness_agents/executor/context.py`  
Kind: `dataclass`  
Responsibility: completed dependency packaged for executor/verifier prompts.

Fields:

- `task_id: TaskId`: dependency id.
- `task_input: str`: dependency input.
- `summaries: list[TaskSummary]`: dependency summaries.

Main functions:

- None. Pure data container.

### `ExecutorLaunchContext`

Source: `backend/src/task_center/harness_agents/executor/context.py`  
Kind: `dataclass`  
Responsibility: prompt context for executor dispatch.

Fields:

- `task_id: TaskId`: executor id.
- `task_input: str`: owned work.
- `harness_graph_id: HarnessGraphId | None`: containing graph or `None` for root.
- `completed_dependencies: list[DependencyBundle] = field(default_factory=list)`: `DONE` dependencies only.

Main functions:

- `to_executor_prompt() -> str`: render instructions, dependency summaries, `TASK_INPUT`, and decision guide.

### `VerifierLaunchContext`

Source: `backend/src/task_center/harness_agents/verifier/context.py`  
Kind: `dataclass`  
Responsibility: prompt context for verifier dispatch.

Fields:

- `task_id: TaskId`: verifier id.
- `task_input: str`: verification specification.
- `harness_graph_id: HarnessGraphId | None`: containing graph id.
- `completed_dependencies: list[DependencyBundle] = field(default_factory=list)`: `DONE` dependencies to verify.

Main functions:

- `to_verifier_prompt() -> str`: render verifier instructions, dependency summaries, and `TASK_INPUT`.

### `PlannerLaunchContext`

Source: `backend/src/task_center/harness_agents/planner/context.py`  
Kind: `dataclass`  
Responsibility: prompt context for planner dispatch.

Fields:

- `root_goal: str`: original root input.
- `request_plan_note: str`: specific decomposition request.

Main functions:

- `to_planner_input() -> str`: render instructions, `ROOT_GOAL`, and `REQUEST_PLAN_NOTE`.

### `EvaluatorLaunchContext`

Source: `backend/src/task_center/harness_agents/evaluator/context.py`  
Kind: `dataclass`  
Responsibility: prompt context for evaluator dispatch.

Fields:

- `task_id: TaskId`: evaluator id.
- `harness_graph_id: HarnessGraphId`: containing graph.
- `root_goal: str`: graph root goal.
- `request_plan_note: str`: planning request to judge.
- `handoff_plan_note: str`: legacy plan note.
- `evaluator_note: str`: evaluation specification.
- `success_child_summaries: list[TaskSummary] = field(default_factory=list)`: successful child summaries.
- `fail_child_summaries: list[TaskSummary] = field(default_factory=list)`: failed child summaries.
- `blocked_child_summaries: list[TaskSummary] = field(default_factory=list)`: dependency-blocked summaries.

Main functions:

- `to_evaluator_prompt() -> str`: render graph notes, summary buckets, and `TASK_INPUT`.

### `AdvisorLaunchContext`

Source: `backend/src/task_center/harness_agents/advisor/context.py`  
Kind: `dataclass`  
Responsibility: prompt context for advisor terminal approval.

Fields:

- `caller_id: TaskId`: task asking for approval.
- `proposed_terminal_tool: str`: terminal tool under review.
- `proposed_input: dict[str, Any]`: exact proposed terminal input.
- `agent_reason: str`: caller rationale.
- `calling_agent_context: str`: context available to caller.

Main functions:

- `to_advisor_prompt() -> str`: render advisor instructions, caller context, and JSON proposal.

## Advisor Gates And Error Classes

### `AdvisorAccept`

Source: `backend/src/task_center/runtime/pre_hooks.py`  
Kind: `dataclass(frozen=True)`  
Responsibility: latest advisor verdict for one caller and terminal proposal.

Fields:

- `caller_id: TaskId`: task that consulted advisor.
- `terminal_tool: str`: terminal reviewed by advisor.
- `proposed_input: dict[str, Any] = field(default_factory=dict)`: exact reviewed payload.
- `verdict: str = "accept"`: `accept` or `reject`.
- `reason: str = ""`: advisor explanation.

Main functions:

- None. Stored and checked by `record_accept`, `get_accept`, and `check_advisor_accept`.

### `TaskCenterError`

Source: `backend/src/task_center/errors.py`  
Kind: `Exception`  
Responsibility: base exception for the `task_center` package.

Fields:

- None.

Main functions:

- Inherited `Exception` behavior.

### `BlockedTerminal`

Source: `backend/src/task_center/runtime/pre_hooks.py`  
Kind: `TaskCenterError` subclass  
Responsibility: raised when a gated terminal lacks a matching advisor accept.

Fields:

- None.

Main functions:

- Inherited exception behavior. Raised by `check_advisor_accept()`.

### `PlanValidationError`

Source: `backend/src/task_center/graph/errors.py`  
Kind: `TaskCenterError` subclass  
Responsibility: raised by legacy DAG/id validation helpers.

Fields:

- None.

Main functions:

- Inherited exception behavior. Used by graph validation helpers.

## Persistence Classes

### `TaskCenterRequestRecord`

Source: `backend/src/db/models/task_center.py`  
Kind: SQLAlchemy model  
Responsibility: persisted top-level user request.

Fields:

- `id: Mapped[str]`: primary key.
- `cwd: Mapped[str]`: request working directory.
- `sandbox_id: Mapped[str | None]`: optional sandbox id.
- `request_prompt: Mapped[str]`: original request prompt.
- `created_at: Mapped[datetime]`: creation timestamp.
- `updated_at: Mapped[datetime]`: update timestamp.
- `runs: Mapped[list[TaskCenterRunRecord]]`: cascade relationship to runs.

Main functions:

- `__repr__() -> str`: debug representation.

### `TaskCenterRunRecord`

Source: `backend/src/db/models/task_center.py`  
Kind: SQLAlchemy model  
Responsibility: persisted execution attempt for a request.

Fields:

- `id: Mapped[str]`: primary key.
- `request_id: Mapped[str]`: foreign key to request.
- `root_task_id: Mapped[str | None]`: persisted root task id.
- `status: Mapped[str]`: run status such as `running`, `done`, or `failed`.
- `started_at: Mapped[datetime]`: run start timestamp.
- `finished_at: Mapped[datetime | None]`: run completion timestamp.
- `request: Mapped[TaskCenterRequestRecord]`: back-populated request relationship.
- `tasks: Mapped[list[TaskCenterTaskRecord]]`: cascade relationship to persisted tasks.
- `harness_graphs: Mapped[list[TaskCenterHarnessGraphRecord]]`: cascade relationship to persisted harness graphs.

Main functions:

- `__repr__() -> str`: debug representation.

### `TaskCenterTaskRecord`

Source: `backend/src/db/models/task_center.py`  
Kind: SQLAlchemy model  
Responsibility: persisted snapshot of one `Task`.

Fields:

- `id: Mapped[str]`: persisted task id.
- `run_id: Mapped[str]`: foreign key to run.
- `role: Mapped[str]`: task role.
- `task_input: Mapped[str]`: input text.
- `status: Mapped[str]`: serialized `Status`.
- `summaries: Mapped[list[dict]]`: JSON serialized summaries.
- `needs: Mapped[list[str]]`: JSON persisted dependency ids.
- `task_center_harness_graph_id: Mapped[str | None]`: persisted graph id.
- `fix_target_id: Mapped[str | None]`: persisted verifier target for fix-executor.
- `spawn_reason: Mapped[str | None]`: persisted spawn reason.
- `created_at: Mapped[datetime]`: creation timestamp.
- `updated_at: Mapped[datetime]`: update timestamp.
- `run: Mapped[TaskCenterRunRecord]`: back-populated run relationship.
- `agent_run: Mapped[AgentRunRecord | None]`: optional one-to-one agent run link.

Main functions:

- `__repr__() -> str`: debug representation.

### `TaskCenterHarnessGraphRecord`

Source: `backend/src/db/models/task_center.py`  
Kind: SQLAlchemy model  
Responsibility: persisted snapshot of one `HarnessGraph`.

Fields:

- `id: Mapped[str]`: persisted graph id.
- `run_id: Mapped[str]`: foreign key to run.
- `root_task_id: Mapped[str]`: persisted root task.
- `planner_task_id: Mapped[str]`: persisted planner.
- `evaluator_task_id: Mapped[str | None]`: persisted evaluator.
- `executor_task_ids: Mapped[list[str]]`: JSON child ids.
- `dag_nodes: Mapped[list[str]]`: JSON executor/verifier DAG ids.
- `plan_shape: Mapped[str | None]`: `full`, `partial`, or null.
- `what_to_do_next: Mapped[str]`: partial continuation directive.
- `prior_graph_id: Mapped[str | None]`: previous partial-plan graph.
- `created_at: Mapped[datetime]`: creation timestamp.
- `updated_at: Mapped[datetime]`: update timestamp.
- `run: Mapped[TaskCenterRunRecord]`: back-populated run relationship.

Main functions:

- `__repr__() -> str`: debug representation.

### `TaskCenterStore`

Source: `backend/src/db/stores/task_center_store.py`  
Kind: `SyncStoreMixin` subclass  
Responsibility: CRUD and upsert API for TaskCenter persistence.

Fields:

- Inherited session factory from `SyncStoreMixin`: uses `self._sf()` for synchronous DB sessions.

Main functions:

- `create_request(...) -> TaskCenterRequestRecord`: insert request row.
- `get_request(request_id) -> TaskCenterRequestRecord | None`: fetch request row.
- `list_requests(cwd=None, limit=20) -> list[dict]`: read serialized request data.
- `create_run(...) -> TaskCenterRunRecord`: insert running run row.
- `set_run_root(run_id, root_task_id) -> None`: attach root task id.
- `finish_run(run_id, status) -> None`: set status and `finished_at`.
- `get_run(run_id) -> TaskCenterRunRecord | None`: fetch run row.
- `list_runs_for_request(request_id, limit=50) -> list[dict]`: read serialized run data for one request.
- `upsert_task(...) -> None`: create/update serialized task snapshot.
- `upsert_harness_graph(...) -> None`: create/update serialized `HarnessGraph` snapshot.
- `list_tasks_for_run(run_id) -> list[dict]`: read persisted task snapshots.
- `list_harness_graphs_for_run(run_id) -> list[dict]`: read persisted harness graph snapshots.

## Supporting Module Functions

These are not classes, but they explain where several class methods delegate
real behavior.

| Function | Source | Specific behavior |
| --- | --- | --- |
| `compile_dag(tasks, task_inputs)` | `backend/src/task_center/graph/dag.py` | Validate planner task ids/deps and return dependency map. |
| `plan_sinks(deps)` | `backend/src/task_center/graph/dag.py` | Compute DAG sink task ids for evaluator dependencies. |
| `validate_task_ids_available(graph, task_ids)` | `backend/src/task_center/graph/dag.py` | Reject submitted ids already present in `TaskGraph`. |
| `dependency_blocked_descendants(graph, task_id)` | `backend/src/task_center/graph/queries.py` | Find downstream tasks blocked by dependency failure. |
| `is_harness_graph_ready_for_evaluation(graph, graph_id)` | `backend/src/task_center/graph/readiness.py` | Check whether a graph evaluator can be promoted. |
| `build_executor_launch_context(graph, task)` | `backend/src/task_center/harness_agents/executor/context.py` | Assemble executor prompt context from graph state. |
| `build_verifier_launch_context(graph, task)` | `backend/src/task_center/harness_agents/verifier/context.py` | Assemble verifier prompt context from graph state. |
| `build_planner_launch_context(graph)` | `backend/src/task_center/harness_agents/planner/context.py` | Assemble planner prompt context from graph state. |
| `build_evaluator_launch_context(graph, task)` | `backend/src/task_center/harness_agents/evaluator/context.py` | Assemble evaluator prompt context from graph state. |
| `record_accept(tc, caller_id, terminal_tool, proposed_input, verdict, reason)` | `backend/src/task_center/runtime/pre_hooks.py` | Store advisor terminal approval. |
| `get_accept(tc, caller_id)` | `backend/src/task_center/runtime/pre_hooks.py` | Fetch latest advisor terminal approval for a caller. |
| `check_advisor_accept(tc, caller_id, terminal_tool, proposed_input)` | `backend/src/task_center/runtime/pre_hooks.py` | Enforce exact accepted terminal and payload. |
| `build_production_spawn(runtime_config, extra_tool_metadata=None)` | `backend/src/task_center/runtime/spawn.py` | Build production `SpawnFunc` adapter for `TaskCenter.run_query`. |

## Ownership Summary

- `TaskGraph`: stores task/graph dictionaries and validates status transitions.
- `TaskCenter`: owns request dispatch, persistence writes, and terminal-tool routing.
- `Orchestrator`: owns graph-scoped plan materialization and graph closure facade methods.
- Lifecycle modules: perform role-specific state mutations behind `TaskCenter` route methods.
- Launch contexts: render role-specific prompts from task/graph state.
- `TaskCenterStore`: persists request/run/task/harness-graph snapshots.
