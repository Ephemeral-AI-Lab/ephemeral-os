"""Orchestrator — graph-scoped facade for the four-role / recursive design.

Every :class:`HarnessGraph` has exactly one orchestrator. The orchestrator is
a transient frozen-dataclass view bound to a ``graph_id`` and a
:class:`TaskCenter` reference; it has no state of its own.

There are two ways to obtain an orchestrator:

1. :meth:`Orchestrator.spawn` — opens a *new* graph + planner, returns the
   orchestrator for it. Side-effecting.
2. ``Orchestrator(graph_id, tc)`` — pure view of an *existing* graph.

Stage 1 of the four-role / recursive-orchestrator restructure introduces the
class with ``spawn`` and the read accessors. Mutating methods
(``materialize_full_plan``, ``materialize_partial_plan``,
``create_harness_fix_executor``, ``close_success``, ``close_partial_success``,
``close_failure``, ``build_continuation_note``) are stubbed here and filled in
by Stages 3, 5, 6, and 7.

Backward compatibility: the prior location of :class:`TaskCenter` was this
module. Stage 1 keeps a re-export so existing imports
(``from task_center.runtime.orchestrator import TaskCenter``) keep working.
"""

from __future__ import annotations

from dataclasses import dataclass

from task_center.model import HarnessGraph, HarnessGraphId, Task, TaskId
from task_center.runtime.task_center import SpawnFunc, TaskCenter


@dataclass(frozen=True)
class Orchestrator:
    """Graph-scoped facade for one :class:`HarnessGraph`."""

    graph_id: HarnessGraphId
    tc: TaskCenter

    # ------------------------------------------------------------------ #
    # Construction                                                       #
    # ------------------------------------------------------------------ #

    @classmethod
    def spawn(
        cls,
        tc: TaskCenter,
        *,
        root_task_id: TaskId,
        request_plan_note: str,
        prior_graph_id: HarnessGraphId | None = None,
    ) -> "Orchestrator":
        """Open a new HarnessGraph + spawn its planner READY.

        Side effects:
        - Reserves the planner's id via ``tc._new_id`` so the graph record
          can carry it from the start.
        - Creates the HarnessGraph via ``tc._open_graph``.
        - Creates the planner Task via ``tc._create_planner`` with the
          reserved id and the new graph id.
        - Returns an :class:`Orchestrator` view bound to the new graph.

        The caller is responsible for ``tc._persist_all`` and ``tc._wakeup``;
        Stage 1 keeps the existing lifecycle modules in charge of that side
        of the protocol so we do not double-wake.
        """
        planner_id = tc._new_id()
        graph = tc._open_graph(
            root_task_id=root_task_id,
            planner_id=planner_id,
            request_plan_note=request_plan_note,
            prior_graph_id=prior_graph_id,
        )
        tc._create_planner(
            input=request_plan_note,
            harness_graph_id=graph.id,
            id=planner_id,
        )
        return cls(graph_id=graph.id, tc=tc)

    # ------------------------------------------------------------------ #
    # Read accessors                                                     #
    # ------------------------------------------------------------------ #

    @property
    def graph(self) -> HarnessGraph:
        return self.tc.graph.get_harness_graph(self.graph_id)

    @property
    def root_task(self) -> Task:
        return self.tc.graph.get(self.graph.root_task_id)

    @property
    def planner(self) -> Task:
        return self.tc.graph.get(self.graph.planner)

    @property
    def evaluator(self) -> Task | None:
        eid = self.graph.evaluator
        if eid is None:
            return None
        return self.tc.graph.get(eid)

    @property
    def dag_nodes(self) -> list[Task]:
        return [self.tc.graph.get(nid) for nid in self.graph.dag_nodes]

    # ------------------------------------------------------------------ #
    # Mutating methods (Stage 1: stubs filled by later stages)           #
    # ------------------------------------------------------------------ #

    def materialize_full_plan(
        self,
        task_dep_graphs: list[dict],
        task_details: dict[str, str],
        evaluation_specification: str,
    ):
        raise NotImplementedError(
            "materialize_full_plan lands in Stage 3 (plan terminal split). "
            "Until then, planner_lifecycle.submit_plan_handoff handles full plans."
        )

    def materialize_partial_plan(
        self,
        task_dep_graphs: list[dict],
        task_details: dict[str, str],
        what_to_do_next: str,
        evaluation_specification: str,
    ):
        raise NotImplementedError(
            "materialize_partial_plan lands in Stage 3 (plan terminal split)."
        )

    def create_harness_fix_executor(
        self,
        verifier_id: TaskId,
        failure_summary: str,
    ) -> None:
        raise NotImplementedError(
            "create_harness_fix_executor lands in Stage 6 (fix-executor)."
        )

    def close_success(self, summary: str) -> None:
        raise NotImplementedError(
            "Orchestrator.close_success lands in Stage 5/7 (closure rewire). "
            "Until then, evaluator_lifecycle.close_harness_graph_success handles closure."
        )

    def close_partial_success(self, summary: str) -> None:
        raise NotImplementedError(
            "Orchestrator.close_partial_success lands in Stage 5 (partial chain)."
        )

    def close_failure(self, summary: str) -> None:
        raise NotImplementedError(
            "Orchestrator.close_failure lands in Stage 5/7 (closure rewire)."
        )

    def build_continuation_note(self) -> str:
        raise NotImplementedError(
            "Orchestrator.build_continuation_note lands in Stage 5 (partial chain)."
        )


__all__ = ["Orchestrator", "SpawnFunc", "TaskCenter"]
