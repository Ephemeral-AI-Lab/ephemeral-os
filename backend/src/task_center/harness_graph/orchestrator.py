"""HarnessGraphOrchestrator state machine."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from db.stores.harness_graph_store import HarnessGraphStore
from task_center.exceptions import GraphInvariantViolation
from task_center.harness_graph.graph import (
    HarnessGraph,
    HarnessGraphFailReason,
    HarnessGraphStage,
    HarnessGraphStatus,
)
from task_center.harness_graph.runtime import (
    HarnessAgentLaunch,
    HarnessGraphRuntime,
)
from task_center.harness_graph.task import (
    EvaluatorSubmission,
    GeneratorSubmission,
    HarnessTaskRole,
    HarnessTaskStatus,
    PlannedGeneratorTask,
    PlannerFailureSubmission,
    PlannerSubmission,
)
from task_center.harness_graph.task_graph import (
    all_generators_done,
    all_generators_quiescent,
    any_generator_failed_or_blocked,
    blocked_descendant_ids,
    dependency_task_ids,
    ordered_generator_tasks,
    ready_pending_generator_ids,
)
from task_center.harness_graph.task_ids import (
    evaluator_task_id,
    generator_task_id,
    planner_task_id,
)
from task_center.harness_graph.validation import (
    assert_evaluator_task_for_submission,
    assert_generator_task_for_submission,
    assert_graph_not_closed,
    assert_graph_stage,
    assert_task_belongs_to_graph,
    assert_valid_graph_close,
)


class HarnessGraphOrchestrator:
    """Runs one planner -> generator DAG -> evaluator harness graph."""

    def __init__(
        self,
        *,
        harness_graph: HarnessGraph,
        graph_store: HarnessGraphStore,
        on_graph_closed: Callable[[str], None],
        runtime: HarnessGraphRuntime | None = None,
    ) -> None:
        self._harness_graph = harness_graph
        self._graph_store = graph_store
        self._on_graph_closed = on_graph_closed
        self._runtime = runtime
        self._generator_agent_names: dict[str, str] = {}

    @property
    def harness_graph_id(self) -> str:
        return self._harness_graph.id

    def start(self) -> None:
        runtime = self._require_runtime()
        graph = self._assert_stage(HarnessGraphStage.PLANNING)
        if graph.status != HarnessGraphStatus.RUNNING:
            raise GraphInvariantViolation(
                f"HarnessGraph {graph.id!r} is not running"
            )
        if graph.planner_task_id is not None:
            raise GraphInvariantViolation(
                f"HarnessGraph {graph.id!r} already has a planner task"
            )

        task_id = planner_task_id(graph.id)
        task_input = runtime.task_input_for_graph(graph)
        task_center_run_id = runtime.task_center_run_id_for_graph(graph)
        runtime.task_store.upsert_task(
            task_id=task_id,
            task_center_run_id=task_center_run_id,
            role=HarnessTaskRole.PLANNER.value,
            task_input=task_input,
            status=HarnessTaskStatus.RUNNING.value,
            summaries=[],
            needs=[],
            task_center_harness_graph_id=graph.id,
            spawn_reason="harness_graph_planner",
        )
        self._graph_store.set_planner_task_id(graph.id, task_id)
        runtime.agent_launcher.launch(
            HarnessAgentLaunch(
                task_id=task_id,
                task_center_run_id=task_center_run_id,
                harness_graph_id=graph.id,
                role=HarnessTaskRole.PLANNER,
                agent_name=HarnessTaskRole.PLANNER.value,
                task_input=task_input,
                needs=(),
            )
        )
        self._dispatch_ready_work()

    def apply_plan_submission(self, submission: PlannerSubmission) -> None:
        self._assert_submission_graph(submission.graph_id)
        graph = self._assert_stage(HarnessGraphStage.PLANNING)
        if graph.planner_task_id != submission.planner_task_id:
            raise GraphInvariantViolation(
                f"Planner submission task {submission.planner_task_id!r} does "
                f"not match graph planner {graph.planner_task_id!r}"
            )
        if submission.kind == "full" and submission.continuation_goal is not None:
            raise GraphInvariantViolation("Full plans cannot set continuation_goal")
        if submission.kind == "partial" and submission.continuation_goal is None:
            raise GraphInvariantViolation("Partial plans require continuation_goal")

        runtime = self._require_runtime()
        planner_task = runtime.task_store.get_task(submission.planner_task_id)
        if planner_task is None:
            raise GraphInvariantViolation(
                f"Planner task {submission.planner_task_id!r} not found"
            )
        assert_task_belongs_to_graph(planner_task, graph)
        if planner_task["role"] != HarnessTaskRole.PLANNER.value:
            raise GraphInvariantViolation(
                f"Task {submission.planner_task_id!r} is not a planner task"
            )

        runtime.task_store.set_task_status(
            submission.planner_task_id,
            status=HarnessTaskStatus.DONE.value,
            summary={
                "kind": submission.kind,
                "summary": submission.summary,
            },
        )
        self._persist_plan_contract(submission)
        generator_ids = self._persist_generator_tasks(submission.tasks)
        self._graph_store.set_generator_task_ids(graph.id, list(generator_ids))
        self._graph_store.set_stage(graph.id, HarnessGraphStage.GENERATING)
        self._dispatch_ready_work()

    def apply_planner_failure(
        self, submission: PlannerFailureSubmission
    ) -> None:
        self._assert_submission_graph(submission.graph_id)
        graph = self._assert_stage(HarnessGraphStage.PLANNING)
        if graph.planner_task_id != submission.planner_task_id:
            raise GraphInvariantViolation(
                f"Planner failure task {submission.planner_task_id!r} does not "
                f"match graph planner {graph.planner_task_id!r}"
            )
        runtime = self._require_runtime()
        planner_task = runtime.task_store.get_task(submission.planner_task_id)
        if planner_task is None:
            raise GraphInvariantViolation(
                f"Planner task {submission.planner_task_id!r} not found"
            )
        assert_task_belongs_to_graph(planner_task, graph)
        runtime.task_store.set_task_status(
            submission.planner_task_id,
            status=HarnessTaskStatus.FAILED.value,
            summary={
                "fail_reason": submission.fail_reason,
                "summary": submission.summary,
            },
        )
        self._close_graph(
            status=HarnessGraphStatus.FAILED,
            fail_reason=HarnessGraphFailReason.PLANNER_FAILED,
        )

    def apply_generator_submission(
        self, submission: GeneratorSubmission
    ) -> None:
        self._assert_submission_graph(submission.graph_id)
        self._mark_generator(submission)
        if submission.outcome == "failure":
            self._block_failed_generator_descendants(submission.task_id)
        self._dispatch_ready_work()

    def apply_evaluator_submission(
        self, submission: EvaluatorSubmission
    ) -> None:
        self._assert_submission_graph(submission.graph_id)
        self._mark_evaluator(submission)
        self._dispatch_ready_work()

    def _persist_plan_contract(self, submission: PlannerSubmission) -> None:
        self._graph_store.set_plan_contract(
            submission.graph_id,
            task_specification=submission.task_specification,
            evaluation_criteria=list(submission.evaluation_criteria),
            continuation_goal=submission.continuation_goal,
        )

    def _persist_generator_tasks(
        self, tasks: tuple[PlannedGeneratorTask, ...]
    ) -> tuple[str, ...]:
        runtime = self._require_runtime()
        graph = self._fresh_graph()
        ordered = ordered_generator_tasks(tasks)
        task_center_run_id = runtime.task_center_run_id_for_graph(graph)
        task_ids: list[str] = []
        for task in ordered:
            task_id = generator_task_id(graph.id, task.local_id)
            needs = dependency_task_ids(
                harness_graph_id=graph.id,
                local_deps=task.deps,
            )
            runtime.task_store.upsert_task(
                task_id=task_id,
                task_center_run_id=task_center_run_id,
                role=HarnessTaskRole.GENERATOR.value,
                task_input=task.task_spec,
                status=HarnessTaskStatus.PENDING.value,
                summaries=[],
                needs=list(needs),
                task_center_harness_graph_id=graph.id,
                spawn_reason="harness_graph_generator",
            )
            self._generator_agent_names[task_id] = task.agent_name
            task_ids.append(task_id)
        return tuple(task_ids)

    def _mark_generator(self, submission: GeneratorSubmission) -> None:
        runtime = self._require_runtime()
        graph = self._assert_stage(HarnessGraphStage.GENERATING)
        task = runtime.task_store.get_task(submission.task_id)
        if task is None:
            raise GraphInvariantViolation(
                f"Generator task {submission.task_id!r} not found"
            )
        assert_generator_task_for_submission(task, graph)
        if task["status"] != HarnessTaskStatus.RUNNING.value:
            raise GraphInvariantViolation(
                f"Generator task {submission.task_id!r} is not running"
            )
        status = (
            HarnessTaskStatus.DONE
            if submission.outcome == "success"
            else HarnessTaskStatus.FAILED
        )
        runtime.task_store.set_task_status(
            submission.task_id,
            status=status.value,
            summary={
                "outcome": submission.outcome,
                "summary": submission.summary,
                "payload": submission.payload,
            },
        )

    def _mark_evaluator(self, submission: EvaluatorSubmission) -> None:
        runtime = self._require_runtime()
        graph = self._assert_stage(HarnessGraphStage.EVALUATING)
        if graph.evaluator_task_id != submission.task_id:
            raise GraphInvariantViolation(
                f"Evaluator submission task {submission.task_id!r} does not "
                f"match graph evaluator {graph.evaluator_task_id!r}"
            )
        task = runtime.task_store.get_task(submission.task_id)
        if task is None:
            raise GraphInvariantViolation(
                f"Evaluator task {submission.task_id!r} not found"
            )
        assert_evaluator_task_for_submission(task, graph)
        if task["status"] != HarnessTaskStatus.RUNNING.value:
            raise GraphInvariantViolation(
                f"Evaluator task {submission.task_id!r} is not running"
            )
        status = (
            HarnessTaskStatus.DONE
            if submission.outcome == "success"
            else HarnessTaskStatus.FAILED
        )
        runtime.task_store.set_task_status(
            submission.task_id,
            status=status.value,
            summary={
                "outcome": submission.outcome,
                "summary": submission.summary,
                "payload": submission.payload,
            },
        )

    def _block_failed_generator_descendants(self, failed_task_id: str) -> None:
        runtime = self._require_runtime()
        graph = self._fresh_graph()
        task_records = runtime.task_store.list_generator_tasks_for_harness_graph(
            graph.id
        )
        for task_id in blocked_descendant_ids(
            failed_task_id=failed_task_id,
            task_records=task_records,
        ):
            runtime.task_store.set_task_status(
                task_id,
                status=HarnessTaskStatus.BLOCKED.value,
                summary={"blocked_by": failed_task_id},
            )

    def _dispatch_ready_work(self) -> None:
        graph = self._fresh_graph()
        if graph.is_closed:
            return
        if graph.stage == HarnessGraphStage.PLANNING:
            return
        if graph.stage == HarnessGraphStage.GENERATING:
            self._dispatch_generating(graph)
            return
        if graph.stage == HarnessGraphStage.EVALUATING:
            self._dispatch_evaluating(graph)

    def _dispatch_generating(self, graph: HarnessGraph) -> None:
        runtime = self._require_runtime()
        task_records = runtime.task_store.list_generator_tasks_for_harness_graph(
            graph.id
        )
        ready_ids = ready_pending_generator_ids(task_records)
        if ready_ids:
            for task_id in ready_ids:
                task = runtime.task_store.set_task_status(
                    task_id, status=HarnessTaskStatus.RUNNING.value
                )
                runtime.agent_launcher.launch(
                    HarnessAgentLaunch(
                        task_id=task_id,
                        task_center_run_id=task["task_center_run_id"],
                        harness_graph_id=graph.id,
                        role=HarnessTaskRole.GENERATOR,
                        agent_name=self._generator_agent_names.get(
                            task_id, HarnessTaskRole.GENERATOR.value
                        ),
                        task_input=task["task_input"],
                        needs=tuple(task["needs"]),
                    )
                )
            return

        if not all_generators_quiescent(task_records):
            return

        if any_generator_failed_or_blocked(task_records):
            self._close_graph(
                status=HarnessGraphStatus.FAILED,
                fail_reason=HarnessGraphFailReason.GENERATOR_FAILED,
            )
            return

        if all_generators_done(task_records):
            self._spawn_evaluator(graph)

    def _dispatch_evaluating(self, graph: HarnessGraph) -> None:
        if graph.evaluator_task_id is None:
            raise GraphInvariantViolation(
                f"HarnessGraph {graph.id!r} is evaluating with no evaluator task"
            )
        runtime = self._require_runtime()
        evaluator_task = runtime.task_store.get_task(graph.evaluator_task_id)
        if evaluator_task is None:
            raise GraphInvariantViolation(
                f"Evaluator task {graph.evaluator_task_id!r} not found"
            )
        status = HarnessTaskStatus(evaluator_task["status"])
        if status == HarnessTaskStatus.DONE:
            self._close_graph(status=HarnessGraphStatus.PASSED, fail_reason=None)
        elif status == HarnessTaskStatus.FAILED:
            self._close_graph(
                status=HarnessGraphStatus.FAILED,
                fail_reason=HarnessGraphFailReason.EVALUATOR_FAILED,
            )

    def _spawn_evaluator(self, graph: HarnessGraph) -> None:
        if graph.evaluator_task_id is not None:
            return
        runtime = self._require_runtime()
        task_id = evaluator_task_id(graph.id)
        task_center_run_id = runtime.task_center_run_id_for_graph(graph)
        task_input = self._evaluator_task_input(graph)
        runtime.task_store.upsert_task(
            task_id=task_id,
            task_center_run_id=task_center_run_id,
            role=HarnessTaskRole.EVALUATOR.value,
            task_input=task_input,
            status=HarnessTaskStatus.RUNNING.value,
            summaries=[],
            needs=list(graph.generator_task_ids),
            task_center_harness_graph_id=graph.id,
            spawn_reason="harness_graph_evaluator",
        )
        self._graph_store.set_evaluator_task_id(graph.id, task_id)
        self._graph_store.set_stage(graph.id, HarnessGraphStage.EVALUATING)
        runtime.agent_launcher.launch(
            HarnessAgentLaunch(
                task_id=task_id,
                task_center_run_id=task_center_run_id,
                harness_graph_id=graph.id,
                role=HarnessTaskRole.EVALUATOR,
                agent_name=HarnessTaskRole.EVALUATOR.value,
                task_input=task_input,
                needs=tuple(graph.generator_task_ids),
            )
        )

    def _close_graph(
        self,
        *,
        status: HarnessGraphStatus,
        fail_reason: HarnessGraphFailReason | None,
    ) -> None:
        assert_valid_graph_close(status=status, fail_reason=fail_reason)
        graph = self._fresh_graph()
        assert_graph_not_closed(graph)
        if graph.status != HarnessGraphStatus.RUNNING:
            raise GraphInvariantViolation(
                f"HarnessGraph {graph.id!r} is not running"
            )
        self._graph_store.close(
            graph.id,
            status=status,
            fail_reason=fail_reason,
            closed_at=datetime.now(UTC),
        )
        if self._runtime is not None:
            self._runtime.orchestrator_registry.deregister(graph.id)
        self._on_graph_closed(graph.id)

    def _fresh_graph(self) -> HarnessGraph:
        graph = self._graph_store.get(self._harness_graph.id)
        if graph is None:
            raise GraphInvariantViolation(
                f"HarnessGraph {self._harness_graph.id!r} not found"
            )
        self._harness_graph = graph
        return graph

    def _assert_stage(self, expected: HarnessGraphStage) -> HarnessGraph:
        graph = self._fresh_graph()
        assert_graph_not_closed(graph)
        assert_graph_stage(graph, expected)
        return graph

    def _require_runtime(self) -> HarnessGraphRuntime:
        if self._runtime is None:
            raise GraphInvariantViolation(
                "HarnessGraphOrchestrator requires runtime dependencies"
            )
        return self._runtime

    def _assert_submission_graph(self, graph_id: str) -> None:
        if graph_id != self._harness_graph.id:
            raise GraphInvariantViolation(
                f"Submission graph {graph_id!r} does not match orchestrator "
                f"graph {self._harness_graph.id!r}"
            )

    def _evaluator_task_input(self, graph: HarnessGraph) -> str:
        criteria = "\n".join(f"- {item}" for item in graph.evaluation_criteria)
        return (
            "Task specification:\n"
            f"{graph.task_specification or ''}\n\n"
            "Evaluation criteria:\n"
            f"{criteria}"
        )
