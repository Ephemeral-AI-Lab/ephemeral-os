"""Orchestrator — graph-scoped facade for the planner/verifier design.

Every :class:`HarnessGraph` has exactly one orchestrator. The orchestrator is
a transient frozen-dataclass view bound to a ``graph_id`` and a
:class:`TaskCenter` reference; it has no state of its own.

Two ways to obtain an orchestrator:

1. :meth:`Orchestrator.spawn` — opens a *new* graph + planner, returns the
   orchestrator for it. Side-effecting.
2. ``Orchestrator(graph_id, tc)`` — pure view of an *existing* graph.

This module also re-exports :class:`TaskCenter` so legacy callers using
``from task_center.runtime.orchestrator import TaskCenter`` keep working.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, get_args

from task_center.model import (
    GeneratorRole,
    HarnessGraph,
    HarnessGraphId,
    Status,
    Task,
    TaskId,
)
from task_center.runtime.task_center import SpawnFunc, TaskCenter


# ---------------------------------------------------------------------------- #
# Materialization failure — structured rejection of submit_full_plan /         #
# submit_partial_plan. Returned (not raised) so the runtime dispatcher can     #
# forward the failure to the agent as a tool-result failure for retry.        #
# ---------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MaterializationFailure:
    """Why a planner DAG was rejected.

    ``code`` is one of:
      - ``empty_dag`` — DAG must contain at least one generator
      - ``duplicate_ids`` — duplicate node ids in the DAG
      - ``missing_details`` — task_details keys must match DAG ids exactly
      - ``unknown_role`` — role is not a generator role
      - ``unknown_dep`` — node references an unknown dep id
      - ``cycle`` — DAG contains a cycle
      - ``id_collision`` — a DAG id already exists in the task graph
      - ``terminal_verifier`` — the DAG must end in exactly one verifier
      - ``terminal_verifier_deps`` — the final verifier must depend on all tasks
    """

    code: str
    message: str


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
        """Open a new HarnessGraph + spawn its planner READY."""
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
    def terminal_verifier(self) -> Task | None:
        from task_center.runtime.closure import terminal_verifier_id

        verifier_id = terminal_verifier_id(self.tc, self.graph_id)
        if verifier_id is None:
            return None
        return self.tc.graph.get(verifier_id)

    @property
    def dag_nodes(self) -> list[Task]:
        return [self.tc.graph.get(nid) for nid in self.graph.dag_nodes]

    # ------------------------------------------------------------------ #
    # Stage 3 — DAG materialization                                      #
    # ------------------------------------------------------------------ #

    def materialize_full_plan(
        self,
        task_dep_graphs: list[dict[str, Any]],
        task_details: dict[str, str],
    ) -> MaterializationFailure | None:
        """Validate a full-plan DAG and create its generator children.

        Returns ``None`` on success and a :class:`MaterializationFailure`
        on validation failure (no graph mutation occurs in that case).
        """
        err = _validate_plan_dag(task_dep_graphs, task_details)
        if err is not None:
            return err
        err = self._validate_task_ids_available(set(task_details))
        if err is not None:
            return err
        self._materialize_dag(task_dep_graphs, task_details)
        graph = self.graph
        graph.plan_shape = "full"
        self.tc._persist_all()
        self.tc._wakeup.set()
        return None

    def materialize_partial_plan(
        self,
        task_dep_graphs: list[dict[str, Any]],
        task_details: dict[str, str],
        what_to_do_next: str,
    ) -> MaterializationFailure | None:
        """Validate a partial-plan DAG and create its children.

        Same as :meth:`materialize_full_plan` plus stores ``what_to_do_next``
        on the harness graph and marks ``plan_shape='partial'`` for the
        Stage 5 continuation chain to pick up.
        """
        err = _validate_plan_dag(task_dep_graphs, task_details)
        if err is not None:
            return err
        err = self._validate_task_ids_available(set(task_details))
        if err is not None:
            return err
        self._materialize_dag(task_dep_graphs, task_details)
        graph = self.graph
        graph.plan_shape = "partial"
        graph.what_to_do_next = what_to_do_next
        self.tc._persist_all()
        self.tc._wakeup.set()
        return None

    def _materialize_dag(
        self,
        task_dep_graphs: list[dict[str, Any]],
        task_details: dict[str, str],
    ) -> None:
        """Common body for both materialization paths.

        Assumes the DAG has already been validated by ``_validate_plan_dag``.
        Creates one Task per generator entry. The planner transitions to HANDOFF.
        """
        graph = self.graph
        planner = self.tc.graph.get(graph.planner)
        self.tc.graph.transition(planner.id, Status.HANDOFF)

        # Per-node creation in topological order so ``needs`` resolves
        # against already-existing Task ids.
        deps_map: dict[str, frozenset[TaskId]] = {
            entry["id"]: frozenset(entry.get("deps", []))
            for entry in task_dep_graphs
        }

        for nid in _topo_sort(task_dep_graphs):
            entry = next(e for e in task_dep_graphs if e["id"] == nid)
            role = entry.get("role", "executor")
            child_status = Status.READY if not deps_map[nid] else Status.PENDING
            primitive = (
                self.tc._create_executor
                if role == "executor"
                else self.tc._create_verifier
            )
            child = primitive(
                input=task_details[nid],
                harness_graph_id=graph.id,
                needs=deps_map[nid],
                status=child_status,
                id=nid,
            )
            graph.dag_nodes.append(child.id)
            # Legacy ``executor_task_ids`` mirrors only the executor
            # subset; verifier ids live on ``dag_nodes`` exclusively.
            if role == "executor":
                graph.executor_task_ids.append(child.id)

    def _validate_task_ids_available(
        self, ids: set[TaskId]
    ) -> MaterializationFailure | None:
        collisions = sorted(ids & set(self.tc.graph.tasks))
        if collisions:
            return MaterializationFailure(
                code="id_collision",
                message=f"task ids already exist in graph: {collisions!r}",
            )
        return None

    # ------------------------------------------------------------------ #
    # Stage 5 — Partial-plan continuation chain                          #
    # ------------------------------------------------------------------ #

    def close_partial_success(
        self, summary: str, *, source_task_id: TaskId
    ) -> "Orchestrator":
        """Close a partial-plan graph successfully and spawn the continuation.

        Pre-condition: the final verifier's terminal handler has already
        marked the verifier DONE. This method:

        - marks the planner DONE,
        - appends a ``segment_success`` summary to the graph's root task
          (which **stays in HANDOFF** — the chain is not yet terminal),
        - spawns the continuation graph rooted at the same root task,
          carrying ``prior_graph_id=self.graph_id``.

        Returns the new continuation orchestrator.
        """
        from task_center.model import TaskSummary  # local: avoid module cycle

        graph = self.graph
        root_task = self.root_task
        planner = self.planner

        self.tc._mark_terminal(planner, Status.DONE)
        root_task.summaries.append(
            TaskSummary(
                kind="segment_success",
                text=summary,
                source_task_id=source_task_id,
            )
        )
        # root_task stays in HANDOFF — explicitly NOT transitioned. The
        # continuation graph that follows will eventually close the chain
        # via ``close_harness_graph_success`` (Stage 5 reuses the existing
        # full-plan closure for that final hop).
        continuation = Orchestrator.spawn(
            self.tc,
            root_task_id=graph.root_task_id,
            request_plan_note=self.build_continuation_note(),
            prior_graph_id=self.graph_id,
        )
        self.tc._persist_all()
        self.tc._wakeup.set()
        return continuation

    def build_continuation_note(self) -> str:
        """Walk the chain via ``prior_graph_id`` and assemble the note.

        Format mirrors design doc §8.4::

            ROOT_GOAL: {root_task.input}
            PRIOR SEGMENTS:
              [each prior graph's what_to_do_next + verifier success summary]
            CURRENT REQUEST:
              {graph.what_to_do_next}
        """
        graph = self.graph
        root_task = self.root_task

        chain: list[HarnessGraph] = []
        current_id = graph.prior_graph_id
        while current_id is not None:
            prior = self.tc.graph.get_harness_graph(current_id)
            chain.append(prior)
            current_id = prior.prior_graph_id
        chain.reverse()  # oldest first

        parts = [f"ROOT_GOAL: {root_task.input}"]
        if chain:
            parts.append("PRIOR SEGMENTS:")
            for i, prior in enumerate(chain, start=1):
                verifier_summary = ""
                from task_center.runtime.closure import terminal_verifier_id

                prior_verifier_id = terminal_verifier_id(self.tc, prior.id)
                if prior_verifier_id is not None:
                    verifier = self.tc.graph.get(prior_verifier_id)
                    if verifier.summaries:
                        verifier_summary = verifier.summaries[-1].text
                directive = prior.what_to_do_next or "(no follow-up directive)"
                parts.append(
                    f"  Segment {i}: {directive}\n"
                    f"    Verifier summary: {verifier_summary}"
                )
        parts.append(
            f"CURRENT REQUEST: {graph.what_to_do_next or '(no directive)'}"
        )
        return "\n".join(parts)

    # ------------------------------------------------------------------ #
    # Stage 6 — Fix-executor                                             #
    # ------------------------------------------------------------------ #

    def create_harness_fix_executor(
        self,
        verifier_id: TaskId,
        failure_summary: str,
    ) -> Task:
        """Spawn a bounded recovery executor for a failed verifier.

        The fix-executor's input is synthesized deterministically from:
          - the verifier's ``failure_summary`` (just-now reason),
          - the verifier's task input (the verification spec it ran), and
          - the verifier's dep summaries (what it had to work with).

        The fix-executor is created READY in the verifier's harness graph
        with no ``needs`` (it does not depend on any DAG node — it operates
        side-channel) and is tagged ``spawn_reason='fix_verification'`` +
        ``fix_target_id=verifier_id`` so the runtime can route success/
        failure back to the verifier (Stage 6: F2 — verifier always has the
        last word, so success means re-run, not bypass).
        """
        verifier = self.tc.graph.get(verifier_id)
        if verifier.role != "verifier":
            raise ValueError(
                f"create_harness_fix_executor: target {verifier_id!r} is "
                f"role {verifier.role!r}, not verifier"
            )
        # Aggregate the verifier's dep summaries; the dep ids are the
        # verifier's ``needs``. Each dep contributes its latest summary
        # (most likely the success summary that the verifier read).
        dep_lines: list[str] = []
        for dep_id in sorted(verifier.needs):
            dep = self.tc.graph.get(dep_id)
            latest = dep.summaries[-1].text if dep.summaries else "(no summary)"
            dep_lines.append(f"- {dep_id}: {latest}")
        dep_block = "\n".join(dep_lines) if dep_lines else "(no upstream deps)"

        fix_input = (
            "## FIX MODE — verifier recovery\n"
            "A verifier downstream of your dependencies failed. Repair the "
            "minimal scope of work that triggered the failure so the verifier "
            "can re-run successfully. Constraints (mirrored from the "
            "bounded verifier-fix policy):\n"
            "- ≤5 file edits; no new files; no test-file edits\n"
            "- narrow change categories (typo, missing import, wrong constant, "
            "syntax fix)\n"
            "- do not call request_plan from this task; recovery is bounded\n"
            "- if the repair is broader than that, submit_task_failure\n\n"
            "## VERIFIER FAILURE SUMMARY\n"
            f"{failure_summary}\n\n"
            "## VERIFIER TASK INPUT (spec the verifier was checking)\n"
            f"{verifier.input}\n\n"
            "## VERIFIER DEP SUMMARIES\n"
            f"{dep_block}"
        )
        fix_executor = self.tc._create_executor(
            input=fix_input,
            harness_graph_id=self.graph_id,
            needs=frozenset(),
            status=Status.READY,
            spawn_reason="fix_verification",
            fix_target_id=verifier_id,
        )
        return fix_executor

    # ------------------------------------------------------------------ #
    # Closure facade on the Orchestrator                                  #
    # ------------------------------------------------------------------ #

    def close_success(self) -> None:
        """Full-plan closure: planner DONE, root_task DONE, propagate up.

        The final verifier's success summary was attached by its terminal
        handler before this facade was called; closure is a state-only step.
        """
        from task_center.runtime import closure

        verifier = self.terminal_verifier
        if verifier is None:
            raise RuntimeError(
                f"Orchestrator.close_success: graph {self.graph_id!r} "
                "has no terminal verifier"
            )
        closure.close_harness_graph_success(self.tc, self.graph_id, verifier.id)

    def close_failure(self) -> None:
        """Full-plan / partial-plan closure when the final verifier fails."""
        from task_center.runtime import closure

        verifier = self.terminal_verifier
        if verifier is None:
            raise RuntimeError(
                f"Orchestrator.close_failure: graph {self.graph_id!r} "
                "has no terminal verifier"
            )
        closure.close_harness_graph_failed(self.tc, self.graph_id, verifier.id)


# ---------------------------------------------------------------------------- #
# Validation helpers                                                            #
# ---------------------------------------------------------------------------- #


def _validate_plan_dag(
    task_dep_graphs: list[dict[str, Any]],
    task_details: dict[str, str],
) -> MaterializationFailure | None:
    """Stage 3 validation matrix per design doc §9.3."""
    ids = [entry.get("id") for entry in task_dep_graphs]

    if not ids:
        return MaterializationFailure(
            code="empty_dag",
            message="DAG must contain at least one generator",
        )
    if len(set(ids)) != len(ids):
        return MaterializationFailure(
            code="duplicate_ids",
            message="duplicate node ids in DAG",
        )
    if set(ids) != set(task_details.keys()):
        return MaterializationFailure(
            code="missing_details",
            message="task_details keys must match DAG ids exactly",
        )

    id_set = set(ids)
    allowed_roles = set(get_args(GeneratorRole))
    for entry in task_dep_graphs:
        role = entry.get("role", "executor")
        if role not in allowed_roles:
            return MaterializationFailure(
                code="unknown_role",
                message=(
                    f"node {entry['id']!r} role={role!r} is not a generator "
                    f"role (allowed: {sorted(allowed_roles)})"
                ),
            )
        deps = entry.get("deps", [])
        if not set(deps).issubset(id_set):
            unknown = sorted(set(deps) - id_set)
            return MaterializationFailure(
                code="unknown_dep",
                message=(
                    f"node {entry['id']!r} references unknown dep ids "
                    f"{unknown!r}"
                ),
            )

    if _has_cycle(task_dep_graphs):
        return MaterializationFailure(
            code="cycle",
            message="DAG contains a cycle",
        )

    sinks = _compute_sinks(task_dep_graphs)
    if len(sinks) != 1:
        return MaterializationFailure(
            code="terminal_verifier",
            message=(
                "DAG must have exactly one sink, and that sink must be the "
                f"final verifier; got sinks {sorted(sinks)!r}"
            ),
        )
    final_id = sinks[0]
    final_entry = next(e for e in task_dep_graphs if e["id"] == final_id)
    if final_entry.get("role", "executor") != "verifier":
        return MaterializationFailure(
            code="terminal_verifier",
            message=(
                f"DAG sink {final_id!r} must have role='verifier' so the "
                "planning unit ends with verification"
            ),
        )

    expected_deps = id_set - {final_id}
    actual_deps = set(final_entry.get("deps", []))
    if actual_deps != expected_deps:
        missing = sorted(expected_deps - actual_deps)
        extra = sorted(actual_deps - expected_deps)
        detail = []
        if missing:
            detail.append(f"missing deps {missing!r}")
        if extra:
            detail.append(f"unexpected deps {extra!r}")
        return MaterializationFailure(
            code="terminal_verifier_deps",
            message=(
                f"final verifier {final_id!r} must directly depend on every "
                f"other DAG node ({'; '.join(detail)})"
            ),
        )

    return None


def _has_cycle(task_dep_graphs: list[dict[str, Any]]) -> bool:
    white, gray, black = 0, 1, 2
    color: dict[str, int] = {entry["id"]: white for entry in task_dep_graphs}
    deps_map: dict[str, list[str]] = {
        entry["id"]: list(entry.get("deps", [])) for entry in task_dep_graphs
    }

    def visit(nid: str) -> bool:
        color[nid] = gray
        for dep in deps_map.get(nid, []):
            if dep not in color:
                # Filtered out by unknown_dep validation already; treat as
                # absent here.
                continue
            if color[dep] == gray:
                return True
            if color[dep] == white and visit(dep):
                return True
        color[nid] = black
        return False

    return any(visit(nid) for nid in list(color) if color[nid] == white)


def _compute_sinks(task_dep_graphs: list[dict[str, Any]]) -> list[str]:
    """Sinks are nodes that no other node depends on."""
    depended_upon: set[str] = set()
    for entry in task_dep_graphs:
        depended_upon.update(entry.get("deps", []))
    return [
        entry["id"]
        for entry in task_dep_graphs
        if entry["id"] not in depended_upon
    ]


def _topo_sort(task_dep_graphs: list[dict[str, Any]]) -> list[str]:
    """Kahn's algorithm — assumes acyclic input (guaranteed by validation)."""
    deps_map: dict[str, set[str]] = {
        entry["id"]: set(entry.get("deps", [])) for entry in task_dep_graphs
    }
    out: list[str] = []
    ready = [nid for nid, deps in deps_map.items() if not deps]
    remaining = {nid: set(deps) for nid, deps in deps_map.items()}
    while ready:
        ready.sort()  # stable order for tests
        nid = ready.pop(0)
        out.append(nid)
        for other_nid, other_deps in remaining.items():
            if nid in other_deps:
                other_deps.discard(nid)
                if not other_deps and other_nid not in out and other_nid not in ready:
                    ready.append(other_nid)
    return out


__all__ = [
    "MaterializationFailure",
    "Orchestrator",
    "SpawnFunc",
    "TaskCenter",
]
