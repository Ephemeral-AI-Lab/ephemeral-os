"""Tests for the ``atlas_lookup`` planner tool (Phase 2 Step 11).

Freshness is resolved via the ledger (fast path) or content-hash
comparison (cold path), not git. These tests cover both paths plus the
error framing and canonicalisation contracts.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from code_intelligence.editing.ledger import Ledger
from db.base import Base
from team.artifacts.store import InMemoryArtifactStore
from team.atlas import AtlasChunk, AtlasStore
from team.atlas.model import ProjectAtlasChunkRecord, ProjectAtlasRecord  # noqa: F401
from team.context.project import ProjectContext
from team.models import BudgetConfig, BudgetState
from team.runtime.registry import register, unregister
from tools.atlas.lookup import atlas_lookup as _atlas_lookup_tool
from tools.core.base import ToolExecutionContext
from tools.core.runtime import ExecutionMetadata


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def atlas_store() -> AtlasStore:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    store = AtlasStore()
    store.initialize(factory)
    return store


def _fake_team_run(tid: str, *, project_key: str = "P1") -> SimpleNamespace:
    budgets = BudgetConfig()
    state = BudgetState()
    return SimpleNamespace(
        id=tid,
        budgets=budgets,
        artifacts=InMemoryArtifactStore(budgets, state),
        project_context=ProjectContext(
            goal="g",
            user_request="u",
            project_key=project_key,
            repo_root="/repo",
        ),
    )


def _ctx(
    tid: str | None,
    *,
    store: AtlasStore | None = None,
    ledger: Ledger | None = None,
) -> ToolExecutionContext:
    meta = ExecutionMetadata(team_run_id=tid or "")
    if store is not None:
        meta.extras["atlas_store"] = store
    if ledger is not None:
        meta.ci_service = SimpleNamespace(ledger=ledger)
    return ToolExecutionContext(cwd=Path("."), metadata=meta)


def _brief(paths: list[str], tag: str = "brief") -> dict[str, Any]:
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


async def _call(**kwargs) -> tuple[Any, list[dict[str, Any]]]:
    context = kwargs.pop("context")
    args = _atlas_lookup_tool.input_model(**kwargs)
    result = await _atlas_lookup_tool.execute(args, context)
    lookups = result.metadata.get("lookups", []) if not result.is_error else []
    return result, lookups


def _seed_chunk(
    store: AtlasStore,
    subsystem: str,
    paths: list[str],
    *,
    content_hashes: dict[str, str] | None = None,
    updated_at: datetime | None = None,
) -> None:
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[
            AtlasChunk(
                subsystem=subsystem,
                brief=_brief(paths),
                content_hashes=content_hashes or {},
            )
        ],
    )


# ---------------------------------------------------------------------------
# Core decisions — ledger-backed freshness.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_subsystem_routes_to_scout(atlas_store: AtlasStore) -> None:
    tr = _fake_team_run("T1")
    register(tr)
    try:
        result, lookups = await _call(
            subsystems=["src/ghost"],
            context=_ctx("T1", store=atlas_store),
        )
        assert not result.is_error
        assert len(lookups) == 1
        assert lookups[0]["action"] == "scout"
        assert lookups[0]["staged_artifact_ref"] is None
    finally:
        unregister("T1")


@pytest.mark.asyncio
async def test_fresh_chunk_via_empty_ledger_is_used(atlas_store: AtlasStore) -> None:
    _seed_chunk(atlas_store, "src/a", ["src/a"])
    ledger = Ledger()  # empty — no edits anywhere
    tr = _fake_team_run("T1")
    register(tr)
    try:
        result, lookups = await _call(
            subsystems=["src/a"],
            context=_ctx("T1", store=atlas_store, ledger=ledger),
        )
        assert not result.is_error
        assert lookups[0]["action"] == "use"
        ref = lookups[0]["staged_artifact_ref"]
        assert ref is not None
        body = tr.artifacts.load(ref)
        assert body["target_paths"] == ["src/a"]
    finally:
        unregister("T1")


@pytest.mark.asyncio
async def test_ledger_edit_in_scope_routes_to_refresh(atlas_store: AtlasStore) -> None:
    _seed_chunk(atlas_store, "src/a", ["src/a"])
    ledger = Ledger()
    time.sleep(0.01)  # make sure edit is strictly after chunk write
    ledger.record("src/a/handler.py", agent_id="worker-1")
    tr = _fake_team_run("T1")
    register(tr)
    try:
        result, lookups = await _call(
            subsystems=["src/a"],
            context=_ctx("T1", store=atlas_store, ledger=ledger),
        )
        assert not result.is_error
        assert lookups[0]["action"] == "refresh"
        assert "ledger" in (lookups[0]["staleness_reason"] or "")
        # Still staged so the planner can skim while the refresher runs.
        assert lookups[0]["staged_artifact_ref"] is not None
    finally:
        unregister("T1")


@pytest.mark.asyncio
async def test_ledger_edit_out_of_scope_stays_fresh(atlas_store: AtlasStore) -> None:
    _seed_chunk(atlas_store, "src/a", ["src/a"])
    ledger = Ledger()
    time.sleep(0.01)
    ledger.record("src/b/unrelated.py", agent_id="worker-1")
    tr = _fake_team_run("T1")
    register(tr)
    try:
        result, lookups = await _call(
            subsystems=["src/a"],
            context=_ctx("T1", store=atlas_store, ledger=ledger),
        )
        assert not result.is_error
        assert lookups[0]["action"] == "use"
    finally:
        unregister("T1")


# ---------------------------------------------------------------------------
# Cold start — ledger absent, content-hash fallback
# ---------------------------------------------------------------------------


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@pytest.mark.asyncio
async def test_cold_start_matching_hashes_use(
    atlas_store: AtlasStore, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    f = src / "m.py"
    f.write_text("x = 1\n")
    _seed_chunk(
        atlas_store,
        subsystem=str(src),
        paths=[str(src)],
        content_hashes={str(f): _sha("x = 1\n")},
    )
    tr = _fake_team_run("T1")
    register(tr)
    try:
        result, lookups = await _call(
            subsystems=[str(src)],
            context=_ctx("T1", store=atlas_store),  # no ledger
        )
        assert not result.is_error
        assert lookups[0]["action"] == "use"
    finally:
        unregister("T1")


@pytest.mark.asyncio
async def test_cold_start_mutated_file_refreshes(
    atlas_store: AtlasStore, tmp_path: Path
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    f = src / "m.py"
    f.write_text("x = 1\n")
    _seed_chunk(
        atlas_store,
        subsystem=str(src),
        paths=[str(src)],
        content_hashes={str(f): _sha("x = 1\n")},
    )
    f.write_text("x = 2\n")  # drift

    tr = _fake_team_run("T1")
    register(tr)
    try:
        result, lookups = await _call(
            subsystems=[str(src)],
            context=_ctx("T1", store=atlas_store),
        )
        assert not result.is_error
        assert lookups[0]["action"] == "refresh"
        assert "content hashes diverged" in (lookups[0]["staleness_reason"] or "")
    finally:
        unregister("T1")


@pytest.mark.asyncio
async def test_cold_start_no_hashes_is_conservative(atlas_store: AtlasStore) -> None:
    _seed_chunk(atlas_store, "src/a", ["src/a"])  # no hashes

    tr = _fake_team_run("T1")
    register(tr)
    try:
        result, lookups = await _call(
            subsystems=["src/a"],
            context=_ctx("T1", store=atlas_store),  # no ledger
        )
        assert not result.is_error
        assert lookups[0]["action"] == "refresh"
        assert "cannot prove freshness" in (lookups[0]["staleness_reason"] or "")
    finally:
        unregister("T1")


# ---------------------------------------------------------------------------
# Mixed subsystems, atlas-disabled, and error framing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_subsystems_independent_decisions(
    atlas_store: AtlasStore,
) -> None:
    _seed_chunk(atlas_store, "src/a", ["src/a"])
    _seed_chunk(atlas_store, "src/b", ["src/b"])
    ledger = Ledger()
    time.sleep(0.01)
    ledger.record("src/b/touched.py", agent_id="worker-1")

    tr = _fake_team_run("T1")
    register(tr)
    try:
        result, lookups = await _call(
            subsystems=["src/a", "src/b", "src/ghost"],
            context=_ctx("T1", store=atlas_store, ledger=ledger),
        )
        assert not result.is_error
        decisions = {e["subsystem"]: e["action"] for e in lookups}
        assert decisions["src/a"] == "use"
        assert decisions["src/b"] == "refresh"
        assert decisions["src/ghost"] == "scout"
    finally:
        unregister("T1")


@pytest.mark.asyncio
async def test_atlas_disabled_when_project_key_missing(
    atlas_store: AtlasStore,
) -> None:
    tr = _fake_team_run("T1", project_key="")
    register(tr)
    try:
        result, lookups = await _call(
            subsystems=["src/a", "src/b"],
            context=_ctx("T1", store=atlas_store),
        )
        assert not result.is_error
        assert "atlas disabled" in result.output
        assert all(e["action"] == "scout" for e in lookups)
    finally:
        unregister("T1")


@pytest.mark.asyncio
async def test_empty_subsystems_rejected(atlas_store: AtlasStore) -> None:
    tr = _fake_team_run("T1")
    register(tr)
    try:
        result, _ = await _call(
            subsystems=[],
            context=_ctx("T1", store=atlas_store),
        )
        assert result.is_error
        assert "no subsystems" in result.output
    finally:
        unregister("T1")


@pytest.mark.asyncio
async def test_missing_team_run_id() -> None:
    result, _ = await _call(
        subsystems=["src/a"],
        context=_ctx(None),
    )
    assert result.is_error
    assert "team_run_id" in result.output


@pytest.mark.asyncio
async def test_unknown_team_run_id() -> None:
    result, _ = await _call(
        subsystems=["src/a"],
        context=_ctx("ghost"),
    )
    assert result.is_error
    assert "not registered" in result.output


@pytest.mark.asyncio
async def test_canonicalises_raw_paths(atlas_store: AtlasStore) -> None:
    _seed_chunk(atlas_store, "src/a", ["src/a"])
    ledger = Ledger()  # empty — everything fresh
    tr = _fake_team_run("T1")
    register(tr)
    try:
        # "src/a/" (trailing slash) and "./src/a" both canonicalise to "src/a".
        result, lookups = await _call(
            subsystems=["src/a/", "./src/a"],
            context=_ctx("T1", store=atlas_store, ledger=ledger),
        )
        assert not result.is_error
        assert len(lookups) == 2
        assert all(e["action"] == "use" for e in lookups)
    finally:
        unregister("T1")
