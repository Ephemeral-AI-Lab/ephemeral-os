"""TaskCenter — request-scoped orchestrator for the GAN-style task graph.

Each user query routes through a fresh ``TaskCenter.run_query``. The class owns:

- :class:`TaskGraph` — the in-memory task + harness-graph store
- the five mode-tool entry points (called from ``tools.mode_tool``)
- a wakeup event that the submission methods set after every state change
- a dispatcher loop that spawns one agent coroutine per ready task
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from task_center.dag import compile_dag
from task_center.errors import TaskCenterError
from task_center.graph import TaskGraph
from task_center.harness_graph import TaskCenterHarnessGraph
from task_center.planner_launch import PlannerLaunchContext
from task_center.task import (
    HarnessGraphId,
    Status,
    Task,
    TaskId,
    TaskSummary,
)

if TYPE_CHECKING:
    from db.stores.task_center_store import TaskCenterStore

logger = logging.getLogger(__name__)


SpawnFunc = Callable[[TaskId, "TaskCenter", str | None], Awaitable[None]]


_TERMINAL_STATUSES: frozenset[Status] = frozenset({Status.DONE, Status.FAILED})


class TaskCenter:
    """Request-scoped orchestrator created by the server runtime."""

    def __init__(
        self,
        runtime_config: Any = None,
        *,
        spawn_func: SpawnFunc | None = None,
        id_prefix: str = "t",
        on_event: "Callable[[Any], Awaitable[None]] | None" = None,
        request_id: str | None = None,
        run_id: str | None = None,
        task_center_store: "TaskCenterStore | None" = None,
    ) -> None:
        self._graph: TaskGraph = TaskGraph()
        self._runtime_config = runtime_config
        self._spawn_func: SpawnFunc | None = spawn_func
        self._wakeup: asyncio.Event = asyncio.Event()
        self._counter = itertools.count(1)
        self._graph_counter = itertools.count(1)
        self._id_prefix = id_prefix
        self._on_event: "Callable[[Any], Awaitable[None]] | None" = on_event
        self.request_id = request_id
        self.run_id = run_id
        self._task_center_store = task_center_store

    def set_event_callback(self, on_event: "Callable[[Any], Awaitable[None]] | None") -> None:
        self._on_event = on_event

    async def _emit_event(self, event: Any) -> None:
        if self._on_event is not None:
            await self._on_event(event)

    @property
    def graph(self) -> TaskGraph:
        return self._graph

    def _new_id(self) -> TaskId:
        return f"{self._id_prefix}{next(self._counter)}"

    def _new_graph_id(self) -> HarnessGraphId:
        return f"g{next(self._graph_counter)}"

    def persisted_task_id(self, task_id: TaskId) -> str:
        if self.run_id is None:
            return task_id
        return f"{self.run_id}:{task_id}"

    def persisted_graph_id(self, graph_id: HarnessGraphId) -> str:
        if self.run_id is None:
            return graph_id
        return f"{self.run_id}:{graph_id}"

    def _persist_task(self, task: Task) -> None:
        if self._task_center_store is None or self.run_id is None:
            return
        persisted_id = self.persisted_task_id(task.id)
        self._task_center_store.upsert_task(
            task_id=persisted_id,
            run_id=self.run_id,
            role=task.role,
            task_input=task.input,
            status=task.status.value,
            summaries=[
                {
                    "kind": s.kind,
                    "text": s.text,
                    "source_task_id": self.persisted_task_id(s.source_task_id),
                    "created_at": s.created_at,
                }
                for s in task.summaries
            ],
            needs=[self.persisted_task_id(n) for n in sorted(task.needs)],
            task_center_harness_graph_id=(
                self.persisted_graph_id(task.task_center_harness_graph_id)
                if task.task_center_harness_graph_id is not None
                else None
            ),
        )

    def _persist_harness_graph(self, graph: TaskCenterHarnessGraph) -> None:
        if self._task_center_store is None or self.run_id is None:
            return
        self._task_center_store.upsert_harness_graph(
            graph_id=self.persisted_graph_id(graph.id),
            run_id=self.run_id,
            parent_task_id=self.persisted_task_id(graph.parent_task_id),
            planner_task_id=self.persisted_task_id(graph.planner_task_id),
            evaluator_task_id=(
                self.persisted_task_id(graph.evaluator_task_id)
                if graph.evaluator_task_id is not None
                else None
            ),
            executor_task_ids=[
                self.persisted_task_id(eid) for eid in graph.executor_task_ids
            ],
        )

    def _persist_all(self) -> None:
        for task in self._graph.tasks.values():
            self._persist_task(task)
        for graph in self._graph.harness_graphs.values():
            self._persist_harness_graph(graph)

    def _finish_persisted_run(self, status: str) -> None:
        if self._task_center_store is None or self.run_id is None:
            return
        self._task_center_store.finish_run(self.run_id, status)

    # ------------------------------------------------------------------ #
    # Root creation                                                      #
    # ------------------------------------------------------------------ #

    def _create_root_executor(self, prompt: str) -> Task:
        task = Task(
            id=self._new_id(),
            role="executor",
            input=prompt,
            status=Status.READY,
            task_center_harness_graph_id=None,
        )
        self._graph.add(task)
        if self._task_center_store is not None and self.run_id is not None:
            self._task_center_store.set_run_root(self.run_id, self.persisted_task_id(task.id))
        self._persist_task(task)
        return task

    # ------------------------------------------------------------------ #
    # Graph helpers                                                      #
    # ------------------------------------------------------------------ #

    def parent_goal(self, task_id: TaskId) -> str | None:
        task = self._graph.get(task_id)
        if task.task_center_harness_graph_id is None:
            return None
        graph = self._graph.get_harness_graph(task.task_center_harness_graph_id)
        return self._graph.get(graph.parent_task_id).input

    def planner_handoff(self, task_id: TaskId) -> list[TaskSummary]:
        task = self._graph.get(task_id)
        if task.task_center_harness_graph_id is None:
            return []
        graph = self._graph.get_harness_graph(task.task_center_harness_graph_id)
        planner = self._graph.get(graph.planner_task_id)
        return [s for s in planner.summaries if s.kind == "handoff"]

    def completed_dependencies(self, task_id: TaskId) -> list[Task]:
        task = self._graph.get(task_id)
        return [
            self._graph.get(dep_id)
            for dep_id in sorted(task.needs)
            if self._graph.get(dep_id).status is Status.DONE
        ]

    def failed_dependencies(self, task_id: TaskId) -> list[Task]:
        task = self._graph.get(task_id)
        return [
            self._graph.get(dep_id)
            for dep_id in sorted(task.needs)
            if self._graph.get(dep_id).status is Status.FAILED
        ]

    def dependency_blocked_descendants(self, task_id: TaskId) -> list[Task]:
        """Return non-terminal executor tasks whose dependency path now contains ``task_id``.

        Evaluators are excluded — they dispatch via harness graph readiness and
        must see FAILED sibling executors instead of being short-circuited.
        """
        out: list[Task] = []
        seen: set[TaskId] = set()
        frontier: list[TaskId] = [task_id]
        while frontier:
            current = frontier.pop()
            for candidate in self._graph.tasks.values():
                if candidate.id in seen or candidate.id == task_id:
                    continue
                if candidate.role != "executor":
                    continue
                if current in candidate.needs and candidate.status not in _TERMINAL_STATUSES:
                    seen.add(candidate.id)
                    out.append(candidate)
                    frontier.append(candidate.id)
        return out

    def is_harness_graph_ready_for_evaluation(self, graph_id: HarnessGraphId) -> bool:
        graph = self._graph.get_harness_graph(graph_id)
        if graph.evaluator_task_id is None:
            return False
        for tid in graph.executor_task_ids:
            if self._graph.get(tid).status not in _TERMINAL_STATUSES:
                return False
        return True

    def _build_planner_launch_context(
        self, caller: Task, task_detail: str
    ) -> PlannerLaunchContext:
        upstream: list[TaskSummary] = []
        if caller.task_center_harness_graph_id is not None:
            outer = self._graph.get_harness_graph(caller.task_center_harness_graph_id)
            outer_planner = self._graph.get(outer.planner_task_id)
            upstream = [s for s in outer_planner.summaries if s.kind == "handoff"]

        if caller.role == "evaluator":
            assert caller.task_center_harness_graph_id is not None
            graph = self._graph.get_harness_graph(caller.task_center_harness_graph_id)
            requested_goal = self._graph.get(graph.parent_task_id).input
            prior_handoff = list(self.planner_handoff(caller.id))
            completed: list[TaskSummary] = []
            failed: list[TaskSummary] = []
            blocked: list[TaskSummary] = []
            for tid in graph.executor_task_ids:
                child = self._graph.get(tid)
                for s in child.summaries:
                    if s.kind == "success":
                        completed.append(s)
                    elif s.kind == "failure":
                        failed.append(s)
                    elif s.kind == "dependency_blocked":
                        blocked.append(s)
            return PlannerLaunchContext(
                task_detail=task_detail,
                caller_task_id=caller.id,
                caller_role="evaluator",
                requested_goal=requested_goal,
                upstream_handoff_summaries=upstream,
                prior_planner_handoff=prior_handoff,
                completed_child_summaries=completed,
                failed_child_summaries=failed,
                dependency_blocked_summaries=blocked,
            )
        return PlannerLaunchContext(
            task_detail=task_detail,
            caller_task_id=caller.id,
            caller_role="executor",
            requested_goal=caller.input,
            upstream_handoff_summaries=upstream,
        )

    # ------------------------------------------------------------------ #
    # Mode-tool entry points                                             #
    # ------------------------------------------------------------------ #

    def submit_task_success(self, task_id: TaskId, summary: str) -> None:
        task = self._graph.get(task_id)
        if task.role not in ("executor", "evaluator"):
            raise TaskCenterError(
                f"submit_task_success: task {task_id!r} role {task.role!r} not allowed"
            )
        task.summaries.append(
            TaskSummary(kind="success", text=summary, source_task_id=task_id)
        )
        self._mark_terminal(task, Status.DONE)
        if task.role == "executor":
            self._notify_child_terminal_changed(task.task_center_harness_graph_id)
        else:
            assert task.task_center_harness_graph_id is not None
            self._close_harness_graph_success(task.task_center_harness_graph_id, task_id)
        self._persist_all()
        self._wakeup.set()

    def submit_task_failure(self, task_id: TaskId, summary: str) -> None:
        task = self._graph.get(task_id)
        if task.role != "executor":
            raise TaskCenterError(
                f"submit_task_failure: task {task_id!r} role {task.role!r} is not executor"
            )
        task.summaries.append(
            TaskSummary(kind="failure", text=summary, source_task_id=task_id)
        )
        self._mark_terminal(task, Status.FAILED)
        for descendant in self.dependency_blocked_descendants(task_id):
            descendant.summaries.append(
                TaskSummary(
                    kind="dependency_blocked",
                    text=f"Blocked because dependency {task_id!r} failed.",
                    source_task_id=task_id,
                )
            )
            self._mark_terminal(descendant, Status.FAILED)
        self._notify_child_terminal_changed(task.task_center_harness_graph_id)
        self._persist_all()
        self._wakeup.set()

    def submit_evaluation_failure(self, task_id: TaskId, summary: str) -> None:
        task = self._graph.get(task_id)
        if task.role != "evaluator":
            raise TaskCenterError(
                f"submit_evaluation_failure: task {task_id!r} role {task.role!r} is not evaluator"
            )
        task.summaries.append(
            TaskSummary(kind="evaluation_failure", text=summary, source_task_id=task_id)
        )
        self._mark_terminal(task, Status.FAILED)
        assert task.task_center_harness_graph_id is not None
        self._close_harness_graph_failed(task.task_center_harness_graph_id, task_id)
        self._persist_all()
        self._wakeup.set()

    def launch_plan_handoff(self, task_id: TaskId, task_detail: str) -> None:
        caller = self._graph.get(task_id)
        if caller.role not in ("executor", "evaluator"):
            raise TaskCenterError(
                f"launch_plan_handoff: task {task_id!r} role {caller.role!r} is not executor/evaluator"
            )
        caller.summaries.append(
            TaskSummary(kind="handoff", text=task_detail, source_task_id=task_id)
        )
        self._graph.transition(caller.id, Status.HANDOFF)

        graph_id = self._new_graph_id()
        planner_id = self._new_id()
        context = self._build_planner_launch_context(caller, task_detail)
        planner = Task(
            id=planner_id,
            role="planner",
            input=context.to_planner_input(),
            status=Status.READY,
            task_center_harness_graph_id=graph_id,
        )
        graph = TaskCenterHarnessGraph(
            id=graph_id,
            run_id=self.run_id or "",
            parent_task_id=caller.id,
            planner_task_id=planner_id,
        )
        self._graph.add(planner)
        self._graph.add_harness_graph(graph)
        self._persist_all()
        self._wakeup.set()

    def submit_plan_handoff(
        self,
        planner_id: TaskId,
        tasks: list[dict[str, Any]],
        task_inputs: dict[str, str],
        handoff_summary: str,
    ) -> None:
        planner = self._graph.get(planner_id)
        if planner.role != "planner":
            raise TaskCenterError(
                f"submit_plan_handoff: task {planner_id!r} role {planner.role!r} is not planner"
            )
        deps = compile_dag(tasks, task_inputs)
        assert planner.task_center_harness_graph_id is not None
        graph = self._graph.get_harness_graph(planner.task_center_harness_graph_id)

        planner.summaries.append(
            TaskSummary(kind="handoff", text=handoff_summary, source_task_id=planner_id)
        )
        self._graph.transition(planner.id, Status.HANDOFF)

        depended_upon: set[str] = set()
        for entry in tasks:
            depended_upon |= deps[entry["id"]]
        sinks = frozenset(tid for tid in deps if tid not in depended_upon)

        for entry in tasks:
            tid = entry["id"]
            child_status = Status.READY if not deps[tid] else Status.PENDING
            child = Task(
                id=tid,
                role="executor",
                input=task_inputs[tid],
                status=child_status,
                task_center_harness_graph_id=graph.id,
                needs=deps[tid],
            )
            self._graph.add(child)
            graph.executor_task_ids.append(tid)

        evaluator_id = f"{planner_id}-eval"
        evaluator = Task(
            id=evaluator_id,
            role="evaluator",
            input=(
                "Validate the parent task's goal against direct child summaries. "
                "Choose submit_task_success, submit_evaluation_failure, or "
                "launch_plan_handoff."
            ),
            status=Status.PENDING,
            task_center_harness_graph_id=graph.id,
            needs=sinks,
        )
        self._graph.add(evaluator)
        graph.evaluator_task_id = evaluator_id

        self._persist_all()
        self._wakeup.set()

    # ------------------------------------------------------------------ #
    # Closure                                                            #
    # ------------------------------------------------------------------ #

    def _close_harness_graph_success(
        self, graph_id: HarnessGraphId, source_task_id: TaskId
    ) -> None:
        graph = self._graph.get_harness_graph(graph_id)
        planner = self._graph.get(graph.planner_task_id)
        self._mark_terminal(planner, Status.DONE)
        parent = self._graph.get(graph.parent_task_id)
        parent.summaries.append(
            TaskSummary(kind="child_success", text="", source_task_id=source_task_id)
        )
        self._mark_terminal(parent, Status.DONE)
        self._propagate_parent_terminal(parent, success=True)

    def _close_harness_graph_failed(
        self, graph_id: HarnessGraphId, source_task_id: TaskId
    ) -> None:
        graph = self._graph.get_harness_graph(graph_id)
        planner = self._graph.get(graph.planner_task_id)
        self._mark_terminal(planner, Status.FAILED)
        parent = self._graph.get(graph.parent_task_id)
        parent.summaries.append(
            TaskSummary(kind="child_failure", text="", source_task_id=source_task_id)
        )
        self._mark_terminal(parent, Status.FAILED)
        self._propagate_parent_terminal(parent, success=False)

    def _propagate_parent_terminal(self, parent: Task, *, success: bool) -> None:
        if parent.task_center_harness_graph_id is None:
            return  # parent is the root; already marked terminal above.
        if parent.role == "evaluator":
            if success:
                self._close_harness_graph_success(
                    parent.task_center_harness_graph_id, parent.id
                )
            else:
                self._close_harness_graph_failed(
                    parent.task_center_harness_graph_id, parent.id
                )
        else:
            self._notify_child_terminal_changed(parent.task_center_harness_graph_id)

    def _notify_child_terminal_changed(
        self, graph_id: HarnessGraphId | None
    ) -> None:
        # The dispatcher polls is_harness_graph_ready_for_evaluation each tick,
        # so it picks up the evaluator promotion. Just wake the loop here.
        del graph_id
        self._wakeup.set()

    def _mark_terminal(self, task: Task, terminal: Status) -> None:
        if task.status is terminal:
            return
        self._graph.transition(task.id, terminal)

    # ------------------------------------------------------------------ #
    # Dispatcher                                                         #
    # ------------------------------------------------------------------ #

    async def run_query(self, prompt: str, *, sandbox_id: str | None = None) -> Task:
        if self._spawn_func is None:
            raise TaskCenterError(
                "TaskCenter.run_query requires a spawn_func — pass one to "
                "the constructor."
            )

        self._graph = TaskGraph()
        root = self._create_root_executor(prompt)
        running: dict[TaskId, asyncio.Task[None]] = {}

        def _promote_ready_evaluators() -> None:
            for graph in self._graph.harness_graphs.values():
                if graph.evaluator_task_id is None:
                    continue
                evaluator = self._graph.get(graph.evaluator_task_id)
                if evaluator.status is not Status.PENDING:
                    continue
                if self.is_harness_graph_ready_for_evaluation(graph.id):
                    self._graph.transition(evaluator.id, Status.READY)
                    self._persist_task(evaluator)

        def _spawn_for_ready() -> None:
            _promote_ready_evaluators()
            for task in self._graph.ready_tasks():
                if task.id in running:
                    continue
                if task.status is Status.PENDING:
                    self._graph.transition(task.id, Status.READY)
                    self._persist_task(task)
                self._graph.transition(task.id, Status.RUNNING)
                self._persist_task(task)
                coro = self._run_one(task.id, sandbox_id)
                running[task.id] = asyncio.create_task(coro)

        final_status = "cancelled"
        try:
            _spawn_for_ready()
            while self._graph.get(root.id).status not in _TERMINAL_STATUSES:
                wakeup_task = asyncio.create_task(self._wakeup.wait())
                await asyncio.wait(
                    [wakeup_task, *list(running.values())],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not wakeup_task.done():
                    wakeup_task.cancel()
                self._wakeup.clear()
                for tid, t in list(running.items()):
                    if t.done():
                        running.pop(tid)
                _spawn_for_ready()
            final_status = self._graph.get(root.id).status.value
        finally:
            for t in running.values():
                if not t.done():
                    t.cancel()
            self._persist_all()
            self._finish_persisted_run(final_status)

        return self._graph.get(root.id)

    async def _run_one(
        self,
        task_id: TaskId,
        sandbox_id: str | None,
    ) -> None:
        assert self._spawn_func is not None
        try:
            await self._spawn_func(task_id, self, sandbox_id)
        except Exception:
            logger.exception("agent for task %r crashed", task_id)
            task = self._graph.get(task_id)
            if task.status is Status.RUNNING:
                self._handle_silent_termination(task, "agent crashed")
            return
        task = self._graph.get(task_id)
        if task.status is Status.RUNNING:
            self._handle_silent_termination(
                task, "agent exited without a terminal tool call"
            )

    def _handle_silent_termination(self, task: Task, reason: str) -> None:
        """Treat a silent agent exit as a role-appropriate terminal."""
        if task.role == "executor":
            self.submit_task_failure(task.id, reason)
        elif task.role == "planner":
            assert task.task_center_harness_graph_id is not None
            task.summaries.append(
                TaskSummary(
                    kind="failure", text=reason, source_task_id=task.id
                )
            )
            self._mark_terminal(task, Status.FAILED)
            self._close_harness_graph_failed(
                task.task_center_harness_graph_id, task.id
            )
            self._persist_all()
            self._wakeup.set()
        else:
            self.submit_evaluation_failure(task.id, reason)


