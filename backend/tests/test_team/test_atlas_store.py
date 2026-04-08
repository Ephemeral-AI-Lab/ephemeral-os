"""Tests for :mod:`team.atlas` — store, freshness, and project identity."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from code_intelligence.editing.ledger import Ledger
from db.base import Base
from team.atlas import (
    AtlasChunk,
    AtlasStore,
    changes_since_chunk,
    is_chunk_fresh,
    is_subsystem_stale,
    project_key_for,
)
from team.atlas.model import ProjectAtlasChunkRecord, ProjectAtlasRecord  # noqa: F401


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def store(session_factory) -> AtlasStore:
    s = AtlasStore()
    s.initialize(session_factory)
    return s


def _scout_brief(paths: list[str], *, coverage: float = 1.0) -> dict:
    return {
        "target_paths": paths,
        "canonical_scope": "|".join(sorted(paths)),
        "summary": f"brief for {paths}",
        "files": [{"path": f"{paths[0]}/m.py", "role": "module", "key_symbols": []}],
        "entry_points": [],
        "open_questions": [],
        "scope_coverage": coverage,
        "gaps": "",
        "suggested_subdivisions": [],
    }


# ---------------------------------------------------------------------------
# AtlasStore
# ---------------------------------------------------------------------------


def test_upsert_creates_header_and_chunks(store: AtlasStore) -> None:
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[
            AtlasChunk(subsystem="src/a", brief=_scout_brief(["src/a"])),
            AtlasChunk(subsystem="src/b", brief=_scout_brief(["src/b"])),
        ],
    )

    header = store.get_atlas("P1")
    assert header is not None
    assert header.project_key == "P1"
    assert header.repo_root == "/repo"
    assert header.subsystems == ["src/a", "src/b"]


def test_get_chunk_returns_none_when_missing(store: AtlasStore) -> None:
    assert store.get_chunk("P1", "src/ghost") is None


def test_get_chunk_roundtrip(store: AtlasStore) -> None:
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[AtlasChunk(subsystem="src/a", brief=_scout_brief(["src/a"]))],
    )
    chunk = store.get_chunk("P1", "src/a")
    assert chunk is not None
    assert chunk.subsystem == "src/a"
    assert chunk.brief["target_paths"] == ["src/a"]
    assert chunk.repo_root == "/repo"


def test_upsert_updates_existing_chunks_and_header(store: AtlasStore) -> None:
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[AtlasChunk(subsystem="src/a", brief=_scout_brief(["src/a"]))],
    )
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[
            AtlasChunk(
                subsystem="src/a",
                brief={**_scout_brief(["src/a"]), "summary": "updated"},
            )
        ],
    )

    chunk = store.get_chunk("P1", "src/a")
    assert chunk is not None
    assert chunk.brief["summary"] == "updated"


def test_upsert_preserves_other_chunks_on_partial_write(store: AtlasStore) -> None:
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[
            AtlasChunk(subsystem="src/a", brief=_scout_brief(["src/a"])),
            AtlasChunk(subsystem="src/b", brief=_scout_brief(["src/b"])),
        ],
    )
    # Refresher rewrites only src/b.
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[
            AtlasChunk(
                subsystem="src/b",
                brief={**_scout_brief(["src/b"]), "summary": "refreshed"},
            )
        ],
    )
    a = store.get_chunk("P1", "src/a")
    b = store.get_chunk("P1", "src/b")
    assert a is not None and a.brief["summary"] == "brief for ['src/a']"
    assert b is not None and b.brief["summary"] == "refreshed"


def test_list_chunks_is_sorted(store: AtlasStore) -> None:
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[
            AtlasChunk(subsystem="zulu", brief=_scout_brief(["zulu"])),
            AtlasChunk(subsystem="alpha", brief=_scout_brief(["alpha"])),
            AtlasChunk(subsystem="mike", brief=_scout_brief(["mike"])),
        ],
    )
    chunks = store.list_chunks("P1")
    assert [c.subsystem for c in chunks] == ["alpha", "mike", "zulu"]


def test_get_atlas_returns_none_for_unknown_project(store: AtlasStore) -> None:
    assert store.get_atlas("ghost") is None


def test_upsert_requires_project_key(store: AtlasStore) -> None:
    with pytest.raises(ValueError, match="project_key"):
        store.upsert_chunks(project_key="", repo_root="/r", chunks=[])


def test_upsert_requires_subsystem(store: AtlasStore) -> None:
    with pytest.raises(ValueError, match="subsystem"):
        store.upsert_chunks(
            project_key="P1",
            repo_root="/r",
            chunks=[AtlasChunk(subsystem="", brief={"target_paths": ["x"]})],
        )


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_project_key_is_stable_for_same_path() -> None:
    k1 = project_key_for("/tmp/foo")
    k2 = project_key_for("/tmp/foo/")
    k3 = project_key_for("/tmp/foo/../foo")
    assert k1 == k2 == k3
    assert len(k1) == 32


def test_project_key_differs_by_path() -> None:
    assert project_key_for("/tmp/foo") != project_key_for("/tmp/bar")


def test_project_key_empty_on_empty_input() -> None:
    assert project_key_for("") == ""
    assert project_key_for(None) == ""


# ---------------------------------------------------------------------------
# Scope matcher
# ---------------------------------------------------------------------------


def test_is_subsystem_stale_positive_when_scope_touched() -> None:
    chunk = AtlasChunk(
        subsystem="src/a",
        brief={"target_paths": ["src/a"], "canonical_scope": "src/a"},
    )
    assert is_subsystem_stale(chunk, {"src/a/m.py"}) is True


def test_is_subsystem_stale_false_when_scope_untouched() -> None:
    chunk = AtlasChunk(
        subsystem="src/a",
        brief={"target_paths": ["src/a"], "canonical_scope": "src/a"},
    )
    assert is_subsystem_stale(chunk, {"src/b/m.py"}) is False


def test_is_subsystem_stale_false_when_no_changes() -> None:
    chunk = AtlasChunk(
        subsystem="src/a",
        brief={"target_paths": ["src/a"], "canonical_scope": "src/a"},
    )
    assert is_subsystem_stale(chunk, set()) is False


def test_is_subsystem_stale_matches_exact_file() -> None:
    chunk = AtlasChunk(
        subsystem="src/a.py",
        brief={"target_paths": ["src/a.py"], "canonical_scope": "src/a.py"},
    )
    assert is_subsystem_stale(chunk, {"src/a.py"}) is True


def test_is_subsystem_stale_unknown_scope_is_stale() -> None:
    chunk = AtlasChunk(subsystem="???", brief={"target_paths": []})
    assert is_subsystem_stale(chunk, {"anything"}) is True


# ---------------------------------------------------------------------------
# Ledger-backed freshness (git-independent)
# ---------------------------------------------------------------------------


def _chunk_at(
    ts: float,
    *,
    paths: list[str],
    hashes: dict[str, str] | None = None,
    repo_root: str = "",
) -> AtlasChunk:
    from datetime import datetime, timezone

    return AtlasChunk(
        subsystem=paths[0],
        brief={"target_paths": paths, "canonical_scope": "|".join(sorted(paths))},
        updated_at=datetime.fromtimestamp(ts, tz=timezone.utc),
        content_hashes=hashes or {},
        repo_root=repo_root,
    )


def test_changes_since_chunk_empty_ledger_returns_empty() -> None:
    chunk = _chunk_at(time.time() - 10, paths=["src/a"])
    assert changes_since_chunk(chunk, Ledger()) == set()


def test_changes_since_chunk_returns_files_after_chunk_timestamp() -> None:
    ledger = Ledger()
    baseline = time.time()
    chunk = _chunk_at(baseline, paths=["src/a"])
    # These must be strictly after the chunk timestamp.
    time.sleep(0.01)
    ledger.record("src/a/m.py", agent_id="scout-1")
    ledger.record("src/b/other.py", agent_id="scout-1")

    changed = changes_since_chunk(chunk, ledger)
    assert changed == {"src/a/m.py", "src/b/other.py"}


def test_changes_since_chunk_excludes_pre_chunk_entries() -> None:
    ledger = Ledger()
    ledger.record("old/file.py", agent_id="scout-1")
    time.sleep(0.01)
    chunk = _chunk_at(time.time(), paths=["old"])
    time.sleep(0.01)
    ledger.record("old/new.py", agent_id="scout-2")

    changed = changes_since_chunk(chunk, ledger)
    assert changed == {"old/new.py"}


def test_changes_since_chunk_no_updated_at_is_conservative() -> None:
    # A chunk with no updated_at cannot be proven fresh from a ledger window.
    chunk = AtlasChunk(subsystem="src/a", brief={"target_paths": ["src/a"]})
    ledger = Ledger()
    ledger.record("src/a/m.py", agent_id="x")
    # Returning the empty set would be a lie; we treat missing updated_at
    # as "no ledger visibility" — the caller falls back to hashes.
    assert changes_since_chunk(chunk, ledger) == set()


def test_is_chunk_fresh_ledger_proves_clean() -> None:
    ledger = Ledger()
    chunk = _chunk_at(time.time(), paths=["src/a"])
    # No ledger entries → nothing touched → fresh.
    assert is_chunk_fresh(chunk, ledger=ledger) is True


def test_is_chunk_fresh_ledger_detects_scope_touch() -> None:
    ledger = Ledger()
    chunk = _chunk_at(time.time(), paths=["src/a"])
    time.sleep(0.01)
    ledger.record("src/a/m.py", agent_id="scout-1")
    assert is_chunk_fresh(chunk, ledger=ledger) is False


def test_is_chunk_fresh_ledger_detects_absolute_path_touch_under_repo_root(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    file_path = repo_root / "src" / "a" / "m.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("x = 1\n", encoding="utf-8")

    ledger = Ledger()
    chunk = _chunk_at(
        time.time(),
        paths=["src/a"],
        repo_root=str(repo_root),
    )
    time.sleep(0.01)
    ledger.record(str(file_path), agent_id="worker-1")

    assert is_chunk_fresh(chunk, ledger=ledger) is False


def test_is_chunk_fresh_ignores_out_of_scope_ledger_entries() -> None:
    ledger = Ledger()
    chunk = _chunk_at(time.time(), paths=["src/a"])
    time.sleep(0.01)
    ledger.record("src/b/m.py", agent_id="scout-1")
    assert is_chunk_fresh(chunk, ledger=ledger) is True


def test_is_chunk_fresh_cold_start_uses_content_hashes(tmp_path: Path) -> None:
    # Simulate cold start: no ledger, but stored content_hashes should
    # be compared against the working tree.
    src = tmp_path / "src"
    src.mkdir()
    f = src / "m.py"
    f.write_text("x = 1\n")

    import hashlib

    def h(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    chunk = AtlasChunk(
        subsystem=str(src),
        brief={"target_paths": [str(src)]},
        content_hashes={str(f): h("x = 1\n")},
    )
    assert is_chunk_fresh(chunk, ledger=None) is True

    # Mutate file → stale.
    f.write_text("x = 2\n")
    assert is_chunk_fresh(chunk, ledger=None) is False


def test_is_chunk_fresh_cold_start_missing_file_is_stale(tmp_path: Path) -> None:
    chunk = AtlasChunk(
        subsystem="x",
        brief={"target_paths": [str(tmp_path)]},
        content_hashes={str(tmp_path / "gone.py"): "deadbeefdeadbeef"},
    )
    assert is_chunk_fresh(chunk, ledger=None) is False


def test_is_chunk_fresh_without_ledger_or_hashes_is_conservative() -> None:
    chunk = AtlasChunk(subsystem="x", brief={"target_paths": ["src"]})
    # No ledger visibility and no hashes → cannot prove fresh.
    assert is_chunk_fresh(chunk, ledger=None) is False


# ---------------------------------------------------------------------------
# AtlasStore — content_hashes persistence
# ---------------------------------------------------------------------------


def test_upsert_persists_content_hashes(store: AtlasStore) -> None:
    hashes = {"src/a/m.py": "abcdef0123456789"}
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[
            AtlasChunk(
                subsystem="src/a",
                brief=_scout_brief(["src/a"]),
                content_hashes=hashes,
            )
        ],
    )
    chunk = store.get_chunk("P1", "src/a")
    assert chunk is not None
    assert chunk.content_hashes == hashes


def test_upsert_defaults_content_hashes_to_empty_dict(store: AtlasStore) -> None:
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[AtlasChunk(subsystem="src/a", brief=_scout_brief(["src/a"]))],
    )
    chunk = store.get_chunk("P1", "src/a")
    assert chunk is not None
    assert chunk.content_hashes == {}
