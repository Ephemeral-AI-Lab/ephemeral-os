"""Tests for team.context.canonicalize, team.context.briefings, and
the Dispatcher ``_promote_to_ready`` dep-artifact snapshot (Step 2a/2b)."""

from __future__ import annotations

import asyncio

import pytest

from team.artifacts.store import InMemoryArtifactStore
from team.context.briefings import render_briefings
from team.context.canonicalize import canonicalize_scope
from team.models import (
    AgentResult,
    Briefing,
    BudgetConfig,
    BudgetState,
    DependencyArtifact,
    WorkItem,
    WorkItemStatus,
)
from team.runtime.dispatcher import Dispatcher


# ---------- canonicalize_scope ------------------------------------------------


def test_canonicalize_scope_sorted_and_deduped():
    assert canonicalize_scope(["b", "a", "a", "b"]) == "a|b"


def test_canonicalize_scope_strips_trailing_slash_and_dot_slash():
    assert canonicalize_scope(["./src/", "src"]) == "src"


def test_canonicalize_scope_order_independent():
    assert canonicalize_scope(["x/y", "a"]) == canonicalize_scope(["a", "x/y"])


def test_canonicalize_scope_drops_empty_and_whitespace():
    assert canonicalize_scope(["  ", "", "a"]) == "a"


def test_canonicalize_scope_empty_input():
    assert canonicalize_scope([]) == ""


# ---------- render_briefings --------------------------------------------------


def _store_with(**items) -> InMemoryArtifactStore:
    store = InMemoryArtifactStore(BudgetConfig(), BudgetState())
    for k, v in items.items():
        store.save(k, v)
    return store


def _wi(**overrides) -> WorkItem:
    defaults = dict(
        id="W1",
        team_run_id="T1",
        agent_name="worker",
        status=WorkItemStatus.READY,
    )
    defaults.update(overrides)
    return WorkItem(**defaults)


def test_render_empty_when_no_briefings():
    wi = _wi()
    assert render_briefings(wi, _store_with()) == ""


def test_render_dep_artifact_section():
    store = _store_with(art1={"summary": "hello", "target_paths": ["src/a"]})
    wi = _wi(
        dep_artifacts=[
            DependencyArtifact(source_wi_id="D1", artifact_ref="art1", display_name="scout_a"),
        ]
    )
    out = render_briefings(wi, store)
    assert "From deps" in out
    assert "scout_a" in out
    assert "src/a" in out  # canonical scope rendered
    assert "hello" in out


def test_render_inline_briefing_section():
    wi = _wi(
        briefings=[Briefing(name="hint", source="inline", inline="remember X", description="why")]
    )
    out = render_briefings(wi, _store_with())
    assert "From parent" in out
    assert "hint" in out
    assert "why" in out
    assert "remember X" in out


def test_render_artifact_briefing_loads_store():
    store = _store_with(A1="artifact body")
    wi = _wi(briefings=[Briefing(name="doc", source="artifact", ref="A1")])
    out = render_briefings(wi, store)
    assert "artifact body" in out


def test_render_deduplicates_by_canonical_scope_across_tiers():
    body = {"summary": "s", "target_paths": ["src/auth"]}
    store = _store_with(shared_art=body, dep_art=body)
    wi = _wi(
        dep_artifacts=[
            DependencyArtifact(source_wi_id="D1", artifact_ref="dep_art", display_name="dep_scout")
        ]
    )
    # shared takes priority over dep — dep_scout's body should be deduped out.
    from team.context.project import ProjectContext

    pc = ProjectContext(goal="g", user_request="u")
    pc.shared_briefings = {
        "src/auth": Briefing(name="shared_scout", source="artifact", ref="shared_art")
    }
    out = render_briefings(wi, store, project_context=pc)
    assert out.count("shared_scout") == 1
    assert "dep_scout" not in out  # deduped
    assert "Shared context" in out


def test_render_skips_invalidated_scout_dep_artifact():
    store = _store_with(
        dep_art={
            "summary": "old scout",
            "target_paths": ["src/auth"],
            "canonical_scope": "src/auth",
            "scope_coverage": 1.0,
            "gaps": "",
            "suggested_subdivisions": [],
            "snapshot_time": 100.0,
        }
    )
    wi = _wi(
        dep_artifacts=[
            DependencyArtifact(source_wi_id="D1", artifact_ref="dep_art", display_name="dep_scout")
        ]
    )
    from team.context.project import ProjectContext

    pc = ProjectContext(goal="g", user_request="u")
    pc.invalidated_scout_scopes["src/auth"] = 150.0

    assert render_briefings(wi, store, project_context=pc) == ""


def test_render_keeps_fresh_scout_artifact_after_scope_invalidation():
    store = _store_with(
        fresh_art={
            "summary": "fresh scout",
            "target_paths": ["src/auth"],
            "canonical_scope": "src/auth",
            "scope_coverage": 1.0,
            "gaps": "",
            "suggested_subdivisions": [],
            "snapshot_time": 200.0,
        }
    )
    wi = _wi(briefings=[Briefing(name="fresh", source="artifact", ref="fresh_art")])
    from team.context.project import ProjectContext

    pc = ProjectContext(goal="g", user_request="u")
    pc.invalidated_scout_scopes["src/auth"] = 150.0

    out = render_briefings(wi, store, project_context=pc)
    assert "fresh scout" in out
    assert "fresh" in out


def test_render_dedupe_names_collisions():
    store = _store_with(a={"target_paths": ["x"]}, b={"target_paths": ["y"]})
    wi = _wi(
        dep_artifacts=[
            DependencyArtifact(source_wi_id="D1", artifact_ref="a", display_name="scout"),
            DependencyArtifact(source_wi_id="D2", artifact_ref="b", display_name="scout"),
        ]
    )
    out = render_briefings(wi, store)
    assert "scout" in out and "scout_2" in out


def test_render_truncates_long_body():
    store = _store_with(big="z" * 100)
    wi = _wi(briefings=[Briefing(name="b", source="artifact", ref="big")])
    out = render_briefings(wi, store, budgets=BudgetConfig(max_briefing_bytes=20))
    assert "truncated" in out


def test_render_handles_missing_artifact():
    store = _store_with()
    wi = _wi(briefings=[Briefing(name="m", source="artifact", ref="does_not_exist")])
    out = render_briefings(wi, store)
    assert "missing artifact" in out


# ---------- Dispatcher ``_promote_to_ready`` ---------------------------------


def _dispatcher() -> Dispatcher:
    return Dispatcher(
        team_run_id="T1",
        budgets=BudgetConfig(),
        budget_state=BudgetState(),
        artifact_store=InMemoryArtifactStore(BudgetConfig(), BudgetState()),
    )


def _new_wi(id_, **overrides) -> WorkItem:
    base = dict(
        id=id_,
        team_run_id="T1",
        agent_name="a",
        status=WorkItemStatus.PENDING,
    )
    base.update(overrides)
    return WorkItem(**base)


def test_dispatcher_add_work_item_snapshots_when_deps_already_done():
    async def _run():
        d = _dispatcher()
        parent = _new_wi("P", status=WorkItemStatus.DONE, artifact_ref="P", local_id="p1")
        d.graph["P"] = parent
        d.artifact_store.save("P", {"target_paths": ["src/a"]})
        child = _new_wi("C", deps=["P"])
        await d.add_work_item(child)
        assert child.status == WorkItemStatus.READY
        assert len(child.dep_artifacts) == 1
        assert child.dep_artifacts[0].source_wi_id == "P"
        assert child.dep_artifacts[0].display_name == "p1"

    asyncio.run(_run())


def test_dispatcher_complete_snapshots_successor():
    async def _run():
        d = _dispatcher()
        await d.add_work_item(_new_wi("P", status=WorkItemStatus.PENDING, local_id="p1"))
        await d.add_work_item(_new_wi("C", deps=["P"]))
        await d.mark_running("P", "run1")
        await d.complete("P", AgentResult(artifact={"target_paths": ["src/x"]}, summary="ok"))
        child = d.graph["C"]
        assert child.status == WorkItemStatus.READY
        assert len(child.dep_artifacts) == 1
        assert child.dep_artifacts[0].source_wi_id == "P"

    asyncio.run(_run())


def test_dispatcher_promote_rejects_early_promotion():
    d = _dispatcher()
    parent = _new_wi("P", status=WorkItemStatus.PENDING)
    child = _new_wi("C", deps=["P"])
    d.graph["P"] = parent
    d.graph["C"] = child
    with pytest.raises(RuntimeError, match="not DONE"):
        d._promote_to_ready(child)
