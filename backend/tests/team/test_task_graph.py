"""Pure unit tests for TaskGraph — no DB, no async.

These tests pin down the single-owner mutation contract. Every TaskGraph
method returns a GraphMutation; ``apply`` commits it to the in-memory dict.
No SQLAlchemy fixtures, no session factories.
"""

from __future__ import annotations

import pytest

from agents.registry import get_definition
from team.core.errors import GraphInvariantViolation
from team.core.models import Task, TaskDefinition, TaskStatus
from team.definitions import register_all as register_team_builtins
from team.runtime.task_graph import (
    DepRewire,
    FailureReasonPatch,
    GraphMutation,
    StatusChange,
    TaskGraph,
    TaskInsert,
)
from .helpers import make_task, structured_spec


if get_definition("developer") is None:
    register_team_builtins()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _graph(*tasks: Task) -> TaskGraph:
    return TaskGraph({t.id: t for t in tasks})


def _running(task_id: str, **kw) -> Task:
    return make_task(task_id, status=TaskStatus.RUNNING, **kw)


def _pending(task_id: str, **kw) -> Task:
    return make_task(task_id, status=TaskStatus.PENDING, **kw)


def _ready(task_id: str, **kw) -> Task:
    return make_task(task_id, status=TaskStatus.READY, **kw)


def _done(task_id: str, **kw) -> Task:
    return make_task(task_id, status=TaskStatus.DONE, **kw)


def _expanded(task_id: str, **kw) -> Task:
    return make_task(task_id, status=TaskStatus.EXPANDED, **kw)


def _counter_ids(start: int = 1):
    """Deterministic id factory for replanner / new-task injection."""
    n = {"i": start}

    def _next() -> str:
        n["i"] += 1
        return f"gen-{n['i']}"

    return _next


# ===========================================================================
# apply — status_changes, inserts, rewires, failure_reason_patches
# ===========================================================================


class TestApply:
    def test_applies_status_change_and_stamps_finished_at_for_terminal(self):
        task = _running("t1")
        graph = _graph(task)

        mutation = GraphMutation(
            status_changes=(StatusChange("t1", TaskStatus.DONE),),
        )
        graph.apply(mutation)

        assert task.status is TaskStatus.DONE
        assert task.finished_at is not None

    def test_applies_non_terminal_status_change_without_finished_at(self):
        task = _pending("t1")
        graph = _graph(task)

        graph.apply(GraphMutation(
            status_changes=(StatusChange("t1", TaskStatus.READY),),
        ))

        assert task.status is TaskStatus.READY
        assert task.finished_at is None

    def test_applies_insert(self):
        graph = _graph()
        new_task = _pending("new-1")

        graph.apply(GraphMutation(inserts=(TaskInsert(new_task),)))

        assert graph.get("new-1") is new_task

    def test_applies_rewire_on_pending_dependent(self):
        origin = _running("origin")
        dependent = _pending("dep-1", deps=["origin"])
        replanner = _ready("replanner")
        graph = _graph(origin, dependent, replanner)

        graph.apply(GraphMutation(rewires=(
            DepRewire(
                old_dep_id="origin",
                new_dep_ids=("replanner",),
                affected_task_ids=("dep-1",),
            ),
        )))

        assert dependent.deps == ["replanner"]

    def test_applies_failure_reason_patch(self):
        origin = make_task("origin", status=TaskStatus.REQUEST_REPLAN)
        graph = _graph(origin)

        graph.apply(GraphMutation(
            failure_reason_patches=(
                FailureReasonPatch("origin", "replanned_by:replanner-id"),
            ),
        ))

        assert origin.failure_reason == "replanned_by:replanner-id"

    def test_apply_is_idempotent(self):
        task = _running("t1")
        graph = _graph(task)
        mutation = GraphMutation(
            status_changes=(StatusChange("t1", TaskStatus.DONE),),
        )

        graph.apply(mutation)
        finished_first = task.finished_at
        graph.apply(mutation)

        assert task.status is TaskStatus.DONE
        # Second apply must not overwrite finished_at once terminal
        assert task.finished_at == finished_first

    def test_apply_prefixes_request_replan_reason(self):
        """The ``replan_requested:`` prefix is applied by the graph so
        in-memory and DB state render identically (matches
        ``tasks_sql.set_status``)."""
        task = _running("t1")
        graph = _graph(task)

        graph.apply(GraphMutation(
            status_changes=(StatusChange("t1", TaskStatus.REQUEST_REPLAN, reason="boom"),),
        ))

        assert task.status is TaskStatus.REQUEST_REPLAN
        assert task.failure_reason == "replan_requested: boom"


# ===========================================================================
# promote_on_done — dependent promotion rules
# ===========================================================================


class TestPromoteOnDone:
    def test_dependent_with_single_dep_promotes_to_ready(self):
        task = _running("main")
        dep = _pending("dep-1", deps=["main"])
        graph = _graph(task, dep)

        mutation = graph.promote_on_done("main")

        statuses = {sc.task_id: sc.new_status for sc in mutation.status_changes}
        assert statuses["main"] is TaskStatus.DONE
        assert statuses["dep-1"] is TaskStatus.READY

    def test_dependent_with_multi_deps_stays_pending_if_any_unsatisfied(self):
        task = _running("A")
        other = _pending("B")  # not yet done
        dep = _pending("dep-1", deps=["A", "B"])
        graph = _graph(task, other, dep)

        mutation = graph.promote_on_done("A")

        statuses = {sc.task_id: sc.new_status for sc in mutation.status_changes}
        assert statuses["A"] is TaskStatus.DONE
        assert "dep-1" not in statuses

    def test_dependent_with_multi_deps_promotes_when_all_done(self):
        a = _running("A")
        b = _done("B")  # already done
        dep = _pending("dep-1", deps=["A", "B"])
        graph = _graph(a, b, dep)

        mutation = graph.promote_on_done("A")

        statuses = {sc.task_id: sc.new_status for sc in mutation.status_changes}
        assert statuses["dep-1"] is TaskStatus.READY

    def test_already_done_task_is_noop(self):
        """A task already in DONE → no status change; no spurious promotion."""
        done = _done("main")
        dep = _pending("dep-1", deps=["main"])
        graph = _graph(done, dep)

        mutation = graph.promote_on_done("main")

        # Already-terminal source: no status change; dependents were promoted
        # on the original transition.
        assert mutation.is_empty()

    def test_non_pending_dependent_not_promoted(self):
        """Only PENDING dependents get flipped to READY. Ready / running /
        terminal dependents are untouched (invariant: they shouldn't have been
        depending on a non-done task anyway)."""
        task = _running("main")
        dep = _running("dep-1", deps=["main"])
        graph = _graph(task, dep)

        mutation = graph.promote_on_done("main")

        statuses = {sc.task_id: sc.new_status for sc in mutation.status_changes}
        assert "dep-1" not in statuses


# ===========================================================================
# mark_expanded / fail / cancel — simple status transitions
# ===========================================================================


class TestSimpleStatusTransitions:
    def test_mark_expanded_emits_status_change(self):
        task = _running("t1")
        graph = _graph(task)

        mutation = graph.mark_expanded("t1")

        assert mutation.status_changes == (
            StatusChange("t1", TaskStatus.EXPANDED),
        )

    def test_fail_on_running_emits_failed_with_reason(self):
        task = _running("t1")
        graph = _graph(task)

        mutation = graph.fail("t1", "boom")

        assert mutation.status_changes == (
            StatusChange("t1", TaskStatus.FAILED, reason="boom"),
        )

    def test_fail_on_already_terminal_is_noop(self):
        task = _done("t1")
        graph = _graph(task)

        mutation = graph.fail("t1", "ignored")

        assert mutation.is_empty()

    def test_cancel_on_live_task_emits_cancelled(self):
        task = _running("t1")
        graph = _graph(task)

        mutation = graph.cancel("t1", "shutdown")

        assert mutation.status_changes == (
            StatusChange("t1", TaskStatus.CANCELLED, reason="shutdown"),
        )

    def test_cancel_on_terminal_is_noop(self):
        task = _done("t1")
        graph = _graph(task)

        mutation = graph.cancel("t1", "ignored")

        assert mutation.is_empty()


# ===========================================================================
# compute_cancel_cascade / cancel_cascade
# ===========================================================================


class TestCancelCascade:
    def test_cascades_through_children(self):
        root = _running("root", parent_id=None)
        child = _running("child", parent_id="root")
        grand = _running("grand", parent_id="child")
        graph = _graph(root, child, grand)

        cascaded = graph.compute_cancel_cascade("root")

        assert cascaded == {"child", "grand"}

    def test_cascades_through_dependents(self):
        root = _running("root", parent_id=None)
        dep = _pending("dep", deps=["root"])
        transitive = _pending("transitive", deps=["dep"])
        graph = _graph(root, dep, transitive)

        cascaded = graph.compute_cancel_cascade("root")

        assert cascaded == {"dep", "transitive"}

    def test_cascade_skips_already_terminal(self):
        root = _running("root")
        already_done = _done("done-child", parent_id="root")
        live_child = _running("live-child", parent_id="root")
        graph = _graph(root, already_done, live_child)

        cascaded = graph.compute_cancel_cascade("root")

        assert cascaded == {"live-child"}

    def test_cascade_excludes_root(self):
        root = _running("root")
        child = _running("child", parent_id="root")
        graph = _graph(root, child)

        cascaded = graph.compute_cancel_cascade("root")

        assert "root" not in cascaded

    def test_cancel_cascade_returns_mutation_and_ids(self):
        root = _running("root")
        child = _running("child", parent_id="root")
        graph = _graph(root, child)

        mutation, cancelled = graph.cancel_cascade("root")

        assert cancelled == ("child",)
        statuses = {sc.task_id: sc.new_status for sc in mutation.status_changes}
        assert statuses["child"] is TaskStatus.CANCELLED


# ===========================================================================
# find_promotable_parent — detached-children policy
# ===========================================================================


class TestFindPromotableParent:
    def test_returns_parent_when_all_children_resolved(self):
        parent = _expanded("p", parent_id=None)
        c1 = _done("c1", parent_id="p")
        c2 = _done("c2", parent_id="p")
        graph = _graph(parent, c1, c2)

        assert graph.find_promotable_parent("c1") == "p"

    def test_returns_none_when_live_sibling_exists(self):
        parent = _expanded("p", parent_id=None)
        c1 = _done("c1", parent_id="p")
        c2 = _running("c2", parent_id="p")
        graph = _graph(parent, c1, c2)

        assert graph.find_promotable_parent("c1") is None

    def test_returns_none_when_parent_not_expanded(self):
        parent = _running("p", parent_id=None)
        c1 = _done("c1", parent_id="p")
        graph = _graph(parent, c1)

        assert graph.find_promotable_parent("c1") is None

    def test_detached_children_do_not_block_promotion(self):
        parent = _expanded("p", parent_id=None)
        done_child = _done("c1", parent_id="p")
        failed_child = make_task("c2", status=TaskStatus.FAILED, parent_id="p")
        cancelled = make_task("c3", status=TaskStatus.CANCELLED, parent_id="p")
        replan_child = make_task("c4", status=TaskStatus.REQUEST_REPLAN, parent_id="p")
        graph = _graph(parent, done_child, failed_child, cancelled, replan_child)

        assert graph.find_promotable_parent("c1") == "p"

    def test_returns_none_for_root_task(self):
        root = _expanded("root", parent_id=None)
        graph = _graph(root)

        assert graph.find_promotable_parent("root") is None


# ===========================================================================
# plan_request_replan — spawn replanner + rewire dependents + invariant
# ===========================================================================


class TestPlanRequestReplan:
    def test_spawns_replanner_and_rewires_pending_dependents(self):
        origin = _running("origin")
        downstream = _pending("downstream", deps=["origin"])
        graph = _graph(origin, downstream)

        result = graph.plan_request_replan(
            task_id="origin",
            reason="broken",
            replanner_agent="team_replanner",
            replanner_id_factory=_counter_ids(),
        )

        assert result.is_new is True
        assert result.replanner_task.agent == "team_replanner"
        assert result.replanner_task.fired_by_task_id == "origin"

        status_map = {sc.task_id: sc for sc in result.mutation.status_changes}
        assert status_map["origin"].new_status is TaskStatus.REQUEST_REPLAN

        # Exactly one insert (the replanner), one rewire (dependents → replanner)
        assert len(result.mutation.inserts) == 1
        assert result.mutation.inserts[0].task is result.replanner_task
        assert len(result.mutation.rewires) == 1
        rewire = result.mutation.rewires[0]
        assert rewire.old_dep_id == "origin"
        assert rewire.new_dep_ids == (result.replanner_task.id,)
        assert rewire.affected_task_ids == ("downstream",)

    def test_invariant_violation_when_dependent_is_not_pending(self):
        """The replace_dependency invariant: any task depending on the
        failing origin must be PENDING. Running/ready/done dependents indicate
        a data-race or upstream bug and must be surfaced."""
        origin = _running("origin")
        running_dependent = _running("hot", deps=["origin"])
        graph = _graph(origin, running_dependent)

        with pytest.raises(GraphInvariantViolation):
            graph.plan_request_replan(
                task_id="origin",
                reason="broken",
                replanner_agent="team_replanner",
                replanner_id_factory=_counter_ids(),
            )

    def test_cancelled_dependent_is_ignored_during_replan_rewire(self):
        origin = _running("origin")
        cancelled_dependent = make_task(
            "validator", status=TaskStatus.CANCELLED, deps=["origin"]
        )
        graph = _graph(origin, cancelled_dependent)

        result = graph.plan_request_replan(
            task_id="origin",
            reason="broken",
            replanner_agent="team_replanner",
            replanner_id_factory=_counter_ids(),
        )

        assert result.is_new is True
        assert result.mutation.rewires == ()

    def test_idempotent_reuse_when_live_replanner_exists(self):
        origin = make_task("origin", status=TaskStatus.REQUEST_REPLAN)
        existing = Task(
            id="existing-replanner",
            team_run_id="run-1",
            spec=structured_spec("recover"),
            agent="team_replanner",
            status=TaskStatus.READY,
            parent_id="parent",
            root_id="root",
            depth=1,
            fired_by_task_id="origin",
        )
        graph = _graph(origin, existing)

        result = graph.plan_request_replan(
            task_id="origin",
            reason="again",
            replanner_agent="team_replanner",
            replanner_id_factory=_counter_ids(),
        )

        assert result.is_new is False
        assert result.replanner_task is existing
        assert result.mutation.is_empty()

    def test_fired_by_points_to_root_origin_for_nested_replan(self):
        """When the failing task is itself a replanner, ``fired_by_task_id``
        on the new replanner should still point to the original root origin."""
        root_origin = make_task("root-origin", status=TaskStatus.REQUEST_REPLAN)
        intermediate_replanner = Task(
            id="intermediate",
            team_run_id="run-1",
            spec=structured_spec("intermediate"),
            agent="team_replanner",
            status=TaskStatus.RUNNING,
            parent_id="parent",
            root_id="root",
            depth=1,
            fired_by_task_id="root-origin",
        )
        graph = _graph(root_origin, intermediate_replanner)

        result = graph.plan_request_replan(
            task_id="intermediate",
            reason="replanner itself broken",
            replanner_agent="team_replanner",
            replanner_id_factory=_counter_ids(),
        )

        assert result.replanner_task.fired_by_task_id == "root-origin"

    def test_reuse_filters_by_replanner_role(self):
        """fired_by_task_id can also identify historical non-replanner tasks;
        reuse must only consider live replanners."""
        origin = make_task("origin", status=TaskStatus.REQUEST_REPLAN)
        non_replanner_fired = Task(
            id="other-fired",
            team_run_id="run-1",
            spec=structured_spec("unrelated"),
            agent="developer",
            status=TaskStatus.READY,
            parent_id="parent",
            root_id="root",
            depth=1,
            fired_by_task_id="origin",
        )
        graph = _graph(origin, non_replanner_fired)

        result = graph.plan_request_replan(
            task_id="origin",
            reason="boom",
            replanner_agent="team_replanner",
            replanner_id_factory=_counter_ids(),
        )

        assert result.is_new is True


# ===========================================================================
# apply_replan — cancel + cascade + insert new children under replanner
# ===========================================================================


class TestApplyReplan:
    def _fixtures(self):
        """A replanner graph: origin (request_replan), replanner (running),
        sibling (ready) to be cancelled, nested (ready under sibling)."""
        origin = make_task("origin", status=TaskStatus.REQUEST_REPLAN, parent_id="root")
        replanner = Task(
            id="replanner",
            team_run_id="run-1",
            spec=structured_spec("recover"),
            agent="team_replanner",
            status=TaskStatus.RUNNING,
            parent_id="root",
            root_id="root",
            depth=1,
            fired_by_task_id="origin",
        )
        sibling = make_task("sibling", status=TaskStatus.READY, parent_id="root")
        nested = make_task("nested", status=TaskStatus.READY, parent_id="sibling")
        return origin, replanner, sibling, nested

    def test_cancels_target_and_cascades_descendants(self):
        origin, replanner, sibling, nested = self._fixtures()
        graph = _graph(origin, replanner, sibling, nested)

        result = graph.apply_replan(
            replan_task_id="replanner",
            add_tasks=[],
            cancel_ids=["sibling"],
            new_task_id_factory=_counter_ids(),
        )

        statuses = {sc.task_id: sc.new_status for sc in result.mutation.status_changes}
        assert statuses["sibling"] is TaskStatus.CANCELLED
        assert statuses["nested"] is TaskStatus.CANCELLED
        assert set(result.cancelled_ids) == {"sibling", "nested"}

    def test_inserts_new_tasks_as_children_of_replanner(self):
        origin, replanner, sibling, nested = self._fixtures()
        graph = _graph(origin, replanner, sibling, nested)

        add_tasks = [
            TaskDefinition(
                id="repair-1",
                spec=structured_spec("repair the sibling"),
                agent="developer",
                scope_paths=["src/a.py"],
                parent_id="replanner",
            ),
        ]
        result = graph.apply_replan(
            replan_task_id="replanner",
            add_tasks=add_tasks,
            cancel_ids=["sibling"],
            new_task_id_factory=_counter_ids(),
        )

        assert len(result.mutation.inserts) == 1
        inserted = result.mutation.inserts[0].task
        assert inserted.parent_id == "replanner"
        assert inserted.agent == "developer"
        assert inserted.depth == replanner.depth
        assert result.replanner_child_count == 1

    def test_tracks_running_ids_for_cancellation_hint(self):
        """Running siblings need to be reported so the coordinator can also
        cancel their live worker tasks."""
        origin = make_task("origin", status=TaskStatus.REQUEST_REPLAN, parent_id="root")
        replanner = Task(
            id="replanner",
            team_run_id="run-1",
            spec=structured_spec("recover"),
            agent="team_replanner",
            status=TaskStatus.RUNNING,
            parent_id="root",
            root_id="root",
            depth=1,
            fired_by_task_id="origin",
        )
        hot = make_task("hot", status=TaskStatus.RUNNING, parent_id="root")
        graph = _graph(origin, replanner, hot)

        result = graph.apply_replan(
            replan_task_id="replanner",
            add_tasks=[],
            cancel_ids=["hot"],
            new_task_id_factory=_counter_ids(),
        )

        assert "hot" in result.cancelled_running_ids


# ===========================================================================
# finalize_replanned_origin — patch failure_reason to record replanner link
# ===========================================================================


class TestFinalizeReplannedOrigin:
    def test_patches_origin_failure_reason(self):
        origin = make_task("origin", status=TaskStatus.REQUEST_REPLAN)
        replanner = Task(
            id="replanner",
            team_run_id="run-1",
            spec=structured_spec("recover"),
            agent="team_replanner",
            status=TaskStatus.DONE,
            parent_id="parent",
            root_id="root",
            depth=1,
            fired_by_task_id="origin",
        )
        graph = _graph(origin, replanner)

        mutation = graph.finalize_replanned_origin("replanner")

        assert mutation.failure_reason_patches == (
            FailureReasonPatch("origin", "replanned_by:replanner"),
        )

    def test_noop_when_replanner_has_no_fired_by(self):
        replanner = Task(
            id="replanner",
            team_run_id="run-1",
            spec=structured_spec("orphan"),
            agent="team_replanner",
            status=TaskStatus.DONE,
            parent_id="parent",
            root_id="root",
            depth=1,
            fired_by_task_id=None,
        )
        graph = _graph(replanner)

        mutation = graph.finalize_replanned_origin("replanner")

        assert mutation.is_empty()

    def test_noop_when_origin_not_request_replan(self):
        """Idempotent re-entry: once the origin has moved past REQUEST_REPLAN
        (e.g., already finalized), the patch is a no-op."""
        origin = _done("origin")
        replanner = Task(
            id="replanner",
            team_run_id="run-1",
            spec=structured_spec("recover"),
            agent="team_replanner",
            status=TaskStatus.DONE,
            parent_id="parent",
            root_id="root",
            depth=1,
            fired_by_task_id="origin",
        )
        graph = _graph(origin, replanner)

        mutation = graph.finalize_replanned_origin("replanner")

        assert mutation.is_empty()


# ===========================================================================
# insert_plan_children — initial status depends on whether deps are DONE
# ===========================================================================


class TestInsertPlanChildren:
    def test_child_with_all_deps_done_starts_ready(self):
        parent = _running("p", parent_id=None)
        dep = _done("dep-done")
        graph = _graph(parent, dep)

        mutation = graph.insert_plan_children(
            parent_id="p",
            specs=[
                TaskDefinition(
                    id="new-1",
                    spec=structured_spec("work"),
                    agent="developer",
                    deps=["dep-done"],
                    scope_paths=["src/a.py"],
                ),
            ],
        )

        assert len(mutation.inserts) == 1
        inserted = mutation.inserts[0].task
        assert inserted.status is TaskStatus.READY

    def test_child_with_any_unsatisfied_dep_starts_pending(self):
        parent = _running("p", parent_id=None)
        not_done = _pending("dep-pending")
        graph = _graph(parent, not_done)

        mutation = graph.insert_plan_children(
            parent_id="p",
            specs=[
                TaskDefinition(
                    id="new-1",
                    spec=structured_spec("work"),
                    agent="developer",
                    deps=["dep-pending"],
                    scope_paths=["src/a.py"],
                ),
            ],
        )

        assert mutation.inserts[0].task.status is TaskStatus.PENDING

    def test_child_without_deps_starts_ready(self):
        parent = _running("p", parent_id=None)
        graph = _graph(parent)

        mutation = graph.insert_plan_children(
            parent_id="p",
            specs=[
                TaskDefinition(
                    id="new-1",
                    spec=structured_spec("root work"),
                    agent="developer",
                    scope_paths=["src/a.py"],
                ),
            ],
        )

        assert mutation.inserts[0].task.status is TaskStatus.READY


# ===========================================================================
# terminal_child_ids
# ===========================================================================


class TestTerminalChildIds:
    def test_returns_children_in_terminal_states(self):
        parent = _expanded("p", parent_id=None)
        a = _done("a", parent_id="p")
        b = make_task("b", status=TaskStatus.FAILED, parent_id="p")
        c = _running("c", parent_id="p")
        d = _ready("d", parent_id=None)  # orphan root — excluded
        graph = _graph(parent, a, b, c, d)

        ids = set(graph.terminal_child_ids())

        assert ids == {"a", "b"}
