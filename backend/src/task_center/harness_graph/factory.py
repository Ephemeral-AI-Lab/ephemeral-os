"""Composition helper for HarnessGraphOrchestrator factories."""

from __future__ import annotations

from collections.abc import Callable

from db.stores.harness_graph_store import HarnessGraphStore
from task_center.harness_graph.graph import HarnessGraph
from task_center.harness_graph.orchestrator import HarnessGraphOrchestrator
from task_center.harness_graph.runtime import HarnessGraphRuntime


def make_harness_graph_orchestrator_factory(
    *,
    graph_store: HarnessGraphStore,
    runtime: HarnessGraphRuntime,
) -> Callable[[HarnessGraph, Callable[[str], None]], HarnessGraphOrchestrator]:
    def factory(
        graph: HarnessGraph,
        on_graph_closed: Callable[[str], None],
    ) -> HarnessGraphOrchestrator:
        orchestrator = HarnessGraphOrchestrator(
            harness_graph=graph,
            graph_store=graph_store,
            on_graph_closed=on_graph_closed,
            runtime=runtime,
        )
        return orchestrator

    return factory
