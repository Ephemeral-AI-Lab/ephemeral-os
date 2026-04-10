"""Tests for Scout-driven Atlas persistence."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.base import Base
from team.atlas import AtlasStore
from team.atlas.model import ProjectAtlasChunkRecord, ProjectAtlasRecord  # noqa: F401
from team.atlas.persistence import persist_brief_to_atlas
from team.context.project import ProjectContext
from team.runtime.team_run import TeamRun


def _atlas_store() -> AtlasStore:
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    store = AtlasStore()
    store.initialize(factory)
    return store


def _brief(paths: list[str]) -> dict[str, object]:
    return {
        "target_paths": paths,
        "canonical_scope": "|".join(sorted(paths)),
        "summary": f"brief for {paths}",
        "files": [],
        "entry_points": [],
        "open_questions": [],
        "scope_coverage": 1.0,
        "gaps": "",
        "suggested_subdivisions": [],
    }


def test_persist_brief_to_atlas_writes_chunk() -> None:
    store = _atlas_store()
    team_run = SimpleNamespace(
        id="T1",
        project_context=ProjectContext(
            goal="g",
            user_request="u",
            project_key="P1",
            repo_root=str(Path("/repo")),
        ),
    )

    persisted = persist_brief_to_atlas(
        team_run=team_run,
        brief=_brief(["src/auth"]),
        store=store,
        reason="unit-test",
    )

    assert persisted is True
    chunk = store.get_chunk("P1", "src/auth")
    assert chunk is not None
    assert chunk.brief["summary"] == "brief for ['src/auth']"


def test_team_run_note_direct_scout_brief_delegates_to_persistence(monkeypatch) -> None:
    seen: list[dict[str, object]] = []

    def _fake_persist(**kwargs):
        seen.append(kwargs)
        return True

    monkeypatch.setattr("team.runtime.team_run.persist_brief_to_atlas", _fake_persist)
    run = TeamRun(session_id="S1", user_request="hello", repo_root="/repo")
    brief = _brief(["src/auth"])

    run.note_direct_scout_brief(
        brief,
        ci_service="ci-service",
        reason="run_subagent:scout-complete",
    )

    assert seen == [
        {
            "team_run": run,
            "brief": brief,
            "ci_service": "ci-service",
            "reason": "run_subagent:scout-complete",
        }
    ]
