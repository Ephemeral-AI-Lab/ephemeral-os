"""Registration + lifecycle tests for atlas_builder / atlas_refresher / submit_atlas_agent.

These tests don't spin up an LLM — they assert the builtin registration
wires posthooks, toolkits, and subagent capabilities correctly, and that
the underlying ``AtlasStore`` survives overlapping writes safely.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.registry import get_definition
from db.base import Base
from team.artifacts.store import InMemoryArtifactStore
from team.atlas import AtlasStore
from team.atlas.model import ProjectAtlasChunkRecord, ProjectAtlasRecord  # noqa: F401
from team.builtins import (
    ATLAS_BUILDER,
    ATLAS_REFRESHER,
    SUBMIT_ATLAS_AGENT,
    register_all,
)
from team.context.project import ProjectContext
from team.models import BudgetConfig, BudgetState
from team.runtime.registry import register, unregister
from tools.core.base import ToolExecutionContext
from tools.core.runtime import ExecutionMetadata
from tools.posthook.submit_atlas import SubmitAtlasInput, SubmitAtlasTool


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _register_builtins() -> None:
    register_all()


@pytest.fixture
def atlas_store() -> AtlasStore:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    store = AtlasStore()
    store.initialize(factory)
    return store


def _brief(paths: list[str], tag: str = "brief") -> dict:
    return {
        "target_paths": paths,
        "canonical_scope": "|".join(sorted(paths)),
        "summary": f"{tag} for {paths}",
        "files": [],
        "entry_points": [],
        "open_questions": [],
        "scope_coverage": 1.0,
        "gaps": "",
        "suggested_subdivisions": [],
    }


def _fake_team_run(tid: str) -> SimpleNamespace:
    budgets = BudgetConfig()
    state = BudgetState()
    return SimpleNamespace(
        id=tid,
        budgets=budgets,
        artifacts=InMemoryArtifactStore(budgets, state),
        project_context=ProjectContext(
            goal="g", user_request="u", project_key="P1", repo_root="/repo"
        ),
    )


def _ctx(tid: str, store: AtlasStore) -> ToolExecutionContext:
    meta = ExecutionMetadata(team_run_id=tid)
    meta.extras["atlas_store"] = store
    meta["posthook_metadata_key"] = "submitted_atlas"
    return ToolExecutionContext(cwd=Path("."), metadata=meta)


# ---------------------------------------------------------------------------
# Registration contracts
# ---------------------------------------------------------------------------


def test_atlas_builder_is_registered_with_submit_atlas_posthook() -> None:
    defn = get_definition(ATLAS_BUILDER)
    assert defn is not None
    assert defn.agent_type == "agent"
    assert defn.can_spawn_subagents is True  # needs to call run_subagent
    assert "subagent" in defn.toolkits
    assert "code_intelligence" in defn.toolkits
    assert defn.posthook is not None
    assert defn.posthook.agent_name == SUBMIT_ATLAS_AGENT
    assert defn.posthook.metadata_key == "submitted_atlas"


def test_atlas_refresher_is_registered_with_submit_atlas_posthook() -> None:
    defn = get_definition(ATLAS_REFRESHER)
    assert defn is not None
    assert defn.agent_type == "agent"
    assert defn.can_spawn_subagents is True
    assert "subagent" in defn.toolkits
    assert defn.posthook is not None
    assert defn.posthook.agent_name == SUBMIT_ATLAS_AGENT
    assert defn.posthook.metadata_key == "submitted_atlas"


def test_submit_atlas_agent_is_a_minimal_serializer() -> None:
    defn = get_definition(SUBMIT_ATLAS_AGENT)
    assert defn is not None
    assert defn.agent_type == "subagent"
    assert defn.can_spawn_subagents is False  # subagent ⇒ no recursion
    assert defn.include_skills is False
    assert defn.skills == []
    assert defn.toolkits == []
    assert defn.extra_tools == ["submit_atlas"]


# ---------------------------------------------------------------------------
# Lifecycle: refresher rewrites only stale chunks, builder+refresher races.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresher_rewrites_only_stale_chunks_keeps_fresh_untouched(
    atlas_store: AtlasStore,
) -> None:
    """Directly exercise submit_atlas as a stand-in for atlas_refresher.

    A full agent-loop integration test belongs in ``test_team_run_e2e`` —
    here we just prove the write semantics the refresher relies on.
    """
    tool = SubmitAtlasTool()
    tr = _fake_team_run("T1")
    register(tr)
    try:
        # Simulated atlas_builder pass: two chunks.
        await tool.execute(
            SubmitAtlasInput(
                chunks=[
                    {"subsystem": "api", "brief": _brief(["src/api"], "initial")},
                    {"subsystem": "db", "brief": _brief(["src/db"], "initial")},
                ]
            ),
            _ctx("T1", atlas_store),
        )
        # Simulated atlas_refresher pass: only `api` is stale.
        await tool.execute(
            SubmitAtlasInput(
                chunks=[{"subsystem": "api", "brief": _brief(["src/api"], "refreshed")}]
            ),
            _ctx("T1", atlas_store),
        )

        api = atlas_store.get_chunk("P1", "api")
        db_chunk = atlas_store.get_chunk("P1", "db")
        assert api is not None and "refreshed" in api.brief["summary"]
        assert db_chunk is not None and "initial" in db_chunk.brief["summary"]
    finally:
        unregister("T1")


@pytest.mark.asyncio
async def test_concurrent_builder_and_refresher_converge_to_last_writer(
    atlas_store: AtlasStore,
) -> None:
    """Two overlapping posthook calls never corrupt the atlas."""
    tool = SubmitAtlasTool()
    tr = _fake_team_run("T1")
    register(tr)
    try:
        async def builder_pass() -> None:
            await tool.execute(
                SubmitAtlasInput(
                    chunks=[
                        {"subsystem": "api", "brief": _brief(["src/api"], "builder-A")},
                        {"subsystem": "db", "brief": _brief(["src/db"], "builder-B")},
                    ]
                ),
                _ctx("T1", atlas_store),
            )

        async def refresher_pass() -> None:
            await tool.execute(
                SubmitAtlasInput(
                    chunks=[
                        {
                            "subsystem": "api",
                            "brief": _brief(["src/api"], "refresher"),
                        }
                    ]
                ),
                _ctx("T1", atlas_store),
            )

        await asyncio.gather(builder_pass(), refresher_pass())

        # After convergence, every subsystem exists and the overlap on
        # ``api`` reflects exactly ONE of the two writers (never a mix).
        api = atlas_store.get_chunk("P1", "api")
        db_chunk = atlas_store.get_chunk("P1", "db")
        assert api is not None
        assert db_chunk is not None and "builder-B" in db_chunk.brief["summary"]
        assert api.brief["summary"] in (
            "builder-A for ['src/api']",
            "refresher for ['src/api']",
        )
    finally:
        unregister("T1")
