"""HarnessGraphOrchestrator skeleton.

Phase 01 ships the contract surface only; Phase 02 implements
planner -> generator -> evaluator wiring.
"""

from __future__ import annotations

from collections.abc import Callable

from db.stores.harness_graph_store import HarnessGraphStore
from task_center.domain.harness_graph import (
    HarnessGraph,
    HarnessGraphFailReason,
    HarnessGraphStatus,
)


class HarnessGraphOrchestrator:
    """One-graph-run orchestrator.

    Phase 02 will fill ``start`` / ``handle_*`` / ``close`` with planner /
    generator / evaluator behaviour. The constructor signature is fixed in
    Phase 01 because Phase 02 calls it from
    ``TaskSegmentManager._orchestrator_factory``.
    """

    def __init__(
        self,
        *,
        harness_graph: HarnessGraph,
        graph_store: HarnessGraphStore,
        on_graph_closed: Callable[[str], None],
    ) -> None:
        self._harness_graph = harness_graph
        self._graph_store = graph_store
        self._on_graph_closed = on_graph_closed

    @property
    def harness_graph_id(self) -> str:
        return self._harness_graph.id

    def start(self) -> None:
        raise NotImplementedError("Phase 02")

    def handle_planner_terminal(self, plan_submission: object) -> None:
        raise NotImplementedError("Phase 02")

    def handle_generator_terminal(
        self, *, task_id: str, status: str
    ) -> None:
        raise NotImplementedError("Phase 02")

    def handle_evaluator_terminal(self, terminal: object) -> None:
        raise NotImplementedError("Phase 02")

    def close(
        self,
        *,
        status: HarnessGraphStatus,
        fail_reason: HarnessGraphFailReason | None,
        continuation_goal: str | None,
    ) -> None:
        raise NotImplementedError("Phase 02")
