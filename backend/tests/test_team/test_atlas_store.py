"""Tests for :mod:`code_intelligence.atlas` — store, freshness, and project identity."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from code_intelligence.editing.ledger import Ledger
from code_intelligence.atlas import (
    AtlasChunk,
    AtlasStore,
    changes_since_chunk,
    is_chunk_fresh,
    is_subsystem_stale,
    project_key_for,
)
from code_intelligence.atlas.model import ProjectAtlasChunkRecord, ProjectAtlasRecord  # noqa: F401
from db.base import Base


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


def test_get_chunk_returns_none_when_missing(store: AtlasStore) -> None:
    assert store.get_chunk("P1", "src/ghost") is None


def test_get_chunk_roundtrip(store: AtlasStore) -> None:
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[
            AtlasChunk(
                subsystem="src/a",
                brief=_scout_brief(["src/a"]),
                scope_paths=["src/a"],
                source_run_id="T1",
            )
        ],
    )
    chunk = store.get_chunk("P1", "src/a")
    assert chunk is not None
    assert chunk.subsystem == "src/a"
    assert chunk.brief["target_paths"] == ["src/a"]
    assert chunk.repo_root == "/repo"
    assert chunk.scope_paths == ["src/a"]
    assert chunk.source_run_id == "T1"
    assert chunk.observed_at > 0
def test_upsert_persists_minimal_metadata(store: AtlasStore) -> None:
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[
            AtlasChunk(
                subsystem="src/a",
                brief=_scout_brief(["src/a"]),
                scope_paths=["src/a"],
                observed_at=123.0,
                source_run_id="RUN-1",
            )
        ],
    )

    chunk = store.get_chunk("P1", "src/a")
    assert chunk is not None
    assert chunk.observed_at == 123.0
    assert chunk.source_run_id == "RUN-1"


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


# ---------------------------------------------------------------------------
# P0 fixes — version-guarded upserts, snapshot_time cutoff, added-file detection
# ---------------------------------------------------------------------------


def test_upsert_is_version_guarded_stale_writer_is_noop(store: AtlasStore) -> None:
    """A slow writer with a lower brief_version must NOT overwrite a fresh one."""
    # Fresh writer commits first.
    fresh = AtlasChunk(
        subsystem="src/a",
        brief={**_scout_brief(["src/a"]), "summary": "fresh"},
        brief_version=1000,
    )
    applied = store.upsert_chunks(project_key="P1", repo_root="/repo", chunks=[fresh])
    assert applied == 1

    # Stale writer (lower version) arrives late — must be ignored.
    stale = AtlasChunk(
        subsystem="src/a",
        brief={**_scout_brief(["src/a"]), "summary": "STALE"},
        brief_version=500,
    )
    applied = store.upsert_chunks(project_key="P1", repo_root="/repo", chunks=[stale])
    assert applied == 0

    chunk = store.get_chunk("P1", "src/a")
    assert chunk is not None
    assert chunk.brief["summary"] == "fresh"
    assert chunk.brief_version == 1000


def test_upsert_newer_version_overwrites(store: AtlasStore) -> None:
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[
            AtlasChunk(
                subsystem="src/a",
                brief=_scout_brief(["src/a"]),
                brief_version=1,
            )
        ],
    )
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[
            AtlasChunk(
                subsystem="src/a",
                brief={**_scout_brief(["src/a"]), "summary": "v2"},
                brief_version=2,
            )
        ],
    )
    chunk = store.get_chunk("P1", "src/a")
    assert chunk is not None and chunk.brief["summary"] == "v2"
    assert chunk.brief_version == 2


def test_upsert_persists_snapshot_time(store: AtlasStore) -> None:
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[
            AtlasChunk(
                subsystem="src/a",
                brief=_scout_brief(["src/a"]),
                snapshot_time=1234567.5,
            )
        ],
    )
    chunk = store.get_chunk("P1", "src/a")
    assert chunk is not None
    assert chunk.snapshot_time == 1234567.5


def test_header_insert_race_recovers_without_aborting(store: AtlasStore) -> None:
    existing = ProjectAtlasRecord(project_key="P1", repo_root="/old")

    class _Nested:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FakeDB:
        def __init__(self) -> None:
            self.get_calls = 0

        def get(self, model, key):
            assert model is ProjectAtlasRecord
            assert key == "P1"
            self.get_calls += 1
            return None if self.get_calls == 1 else existing

        def begin_nested(self):
            return _Nested()

        def add(self, row) -> None:
            assert isinstance(row, ProjectAtlasRecord)

        def flush(self) -> None:
            raise IntegrityError("insert", {}, Exception("duplicate key"))

    store._upsert_header(_FakeDB(), "P1", "/repo")
    assert existing.repo_root == "/repo"


def test_snapshot_time_is_used_as_ledger_cutoff() -> None:
    """Edits between snapshot_time and updated_at must mark the chunk stale."""
    from datetime import datetime, timezone

    ledger = Ledger()
    snapshot = time.time()
    # Scout "read" files at `snapshot`. Then an edit lands BEFORE the
    # row is committed — this is the race the fix targets.
    time.sleep(0.01)
    ledger.record("src/a/m.py", agent_id="racer")
    time.sleep(0.01)
    committed = time.time()

    chunk = AtlasChunk(
        subsystem="src/a",
        brief={"target_paths": ["src/a"]},
        updated_at=datetime.fromtimestamp(committed, tz=timezone.utc),
        snapshot_time=snapshot,
    )
    # Using snapshot_time as cutoff → ledger entry is visible → stale.
    assert is_chunk_fresh(chunk, ledger=ledger) is False


def test_cold_path_detects_newly_added_files(tmp_path: Path) -> None:
    """Files added to scope AFTER the chunk was written must mark it stale."""
    import hashlib

    src = tmp_path / "src"
    src.mkdir()
    existing = src / "a.py"
    existing.write_text("x = 1\n")

    def h(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    chunk = AtlasChunk(
        subsystem="src",
        brief={"target_paths": [str(src)]},
        content_hashes={str(existing): h("x = 1\n")},
        repo_root=str(tmp_path),
    )
    # Pristine → fresh.
    assert is_chunk_fresh(chunk, ledger=None) is True

    # New file appears in scope → stale, even though the tracked file
    # is still hash-identical.
    (src / "b.py").write_text("y = 2\n")
    assert is_chunk_fresh(chunk, ledger=None) is False


def test_ttl_gate_marks_old_chunks_stale() -> None:
    from datetime import datetime, timedelta, timezone

    chunk = AtlasChunk(
        subsystem="src/a",
        brief={"target_paths": ["src/a"]},
        updated_at=datetime.now(timezone.utc) - timedelta(hours=48),
        content_hashes={},  # would otherwise hit conservative-False path
    )
    # TTL 24h → 48h-old chunk is stale regardless of other signals.
    assert is_chunk_fresh(chunk, ledger=None, max_age_seconds=24 * 3600) is False


def test_get_chunks_batch_preserves_order_and_omits_missing(store: AtlasStore) -> None:
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[
            AtlasChunk(subsystem="src/a", brief=_scout_brief(["src/a"])),
            AtlasChunk(subsystem="src/b", brief=_scout_brief(["src/b"])),
        ],
    )
    results = store.get_chunks("P1", ["src/b", "src/ghost", "src/a"])
    assert [c.subsystem for c in results] == ["src/b", "src/a"]
    assert all(c.repo_root == "/repo" for c in results)


def test_binary_file_hashing_and_mutation_detected(tmp_path: Path) -> None:
    """Binary files must hash (not silently drop), and mutations must register.

    Regression for the original review point #8: ``read_text`` dropped
    non-UTF-8 files from ``content_hashes`` entirely, so replacing a
    binary under scope was invisible. Hashing raw bytes fixes it.
    """
    from code_intelligence.atlas.freshness import hash_file

    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\xff\xfe\x00\x01nonutf")
    h1 = hash_file(binary)
    assert h1 is not None and h1 != ""

    time.sleep(0.01)
    binary.write_bytes(b"\x00\x01\x02completely different")
    h2 = hash_file(binary)
    assert h2 is not None and h2 != h1


def test_hash_file_returns_none_for_missing(tmp_path: Path) -> None:
    from code_intelligence.atlas.freshness import hash_file

    assert hash_file(tmp_path / "nope.py") is None


def test_binary_mutation_marks_chunk_stale(tmp_path: Path) -> None:
    """End-to-end: a binary under scope that changes bytes → chunk is stale."""
    from code_intelligence.atlas.freshness import hash_paths_under

    scope = tmp_path / "pkg"
    scope.mkdir()
    binary = scope / "asset.bin"
    binary.write_bytes(b"\xff\x00\x01")

    hashes = hash_paths_under([str(scope)], tmp_path)
    assert hashes  # binary is tracked, not silently dropped
    chunk = AtlasChunk(
        subsystem="pkg",
        brief={"target_paths": [str(scope)]},
        content_hashes=hashes,
        repo_root=str(tmp_path),
    )
    assert is_chunk_fresh(chunk, ledger=None) is True

    binary.write_bytes(b"\x02\x03\x04different")
    assert is_chunk_fresh(chunk, ledger=None) is False


def test_ledger_path_normalization_handles_symlinked_root(tmp_path: Path) -> None:
    """macOS /tmp → /private/tmp style: ledger stores raw, repo_root resolves.

    Uses a hand-built repo_root pair where ``raw`` and ``resolved``
    differ, so the two-root prefix check in ``_normalise_ledger_path``
    is exercised even on filesystems without real symlinks.
    """
    from datetime import datetime, timezone
    from code_intelligence.atlas.freshness import changes_since_chunk, is_subsystem_stale

    # Simulate: ledger recorded under an unresolved root; chunk stores
    # the resolved form. Use monkey-patching via a real symlink when
    # possible, otherwise fabricate the condition manually.
    real_root = tmp_path / "real"
    real_root.mkdir()
    link_root = tmp_path / "link"
    try:
        link_root.symlink_to(real_root)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")

    ledger = Ledger()
    chunk = AtlasChunk(
        subsystem="src/a",
        brief={"target_paths": ["src/a"]},
        updated_at=datetime.fromtimestamp(time.time(), tz=timezone.utc),
        snapshot_time=time.time(),
        repo_root=str(link_root),  # UNRESOLVED form
    )
    time.sleep(0.01)
    # Edit tool records the RESOLVED path (what /tmp→/private/tmp gives).
    ledger.record(str(real_root / "src" / "a" / "m.py"), agent_id="worker-1")

    changed = changes_since_chunk(chunk, ledger)
    # Path should have been stripped to "src/a/m.py" via the two-root
    # prefix check — either raw_root or resolved_root matches.
    assert any(p.endswith("src/a/m.py") and not p.startswith("/") for p in changed), (
        f"expected repo-relative path in {changed}"
    )
    assert is_subsystem_stale(chunk, changed) is True


def test_brief_version_monotonic_under_rapid_calls() -> None:
    """Two AtlasChunks created back-to-back must have strictly increasing versions."""
    from code_intelligence.atlas.store import _fresh_version

    versions = [_fresh_version() for _ in range(1000)]
    assert len(set(versions)) == 1000, "brief_version must be collision-free"
    assert versions == sorted(versions), "brief_version must be monotonic"


def test_upsert_defaults_content_hashes_to_empty_dict(store: AtlasStore) -> None:
    store.upsert_chunks(
        project_key="P1",
        repo_root="/repo",
        chunks=[AtlasChunk(subsystem="src/a", brief=_scout_brief(["src/a"]))],
    )
    chunk = store.get_chunk("P1", "src/a")
    assert chunk is not None
    assert chunk.content_hashes == {}
