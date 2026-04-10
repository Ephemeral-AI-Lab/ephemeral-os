"""Unit tests for atlas dirty-scope scheduling."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from team.atlas.scheduler import AtlasMaintenanceScheduler
from team.context.project import ProjectContext


class _StubStore:
    def __init__(self, subsystems: list[str], *, has_chunks: bool = True) -> None:
        self._subsystems = subsystems
        self._has_chunks = has_chunks

    def is_initialised(self) -> bool:
        return True

    def has_chunks(self, project_key: str) -> bool:
        return bool(project_key) and self._has_chunks

    def list_subsystems(self, project_key: str) -> list[str]:
        assert project_key == "P1"
        return list(self._subsystems)


class _PersistStore(_StubStore):
    def __init__(self) -> None:
        super().__init__([], has_chunks=True)
        self.writes = []

    def upsert_chunks(self, *, project_key: str, repo_root: str, chunks: list[object]) -> int:
        self.writes.append((project_key, repo_root, chunks))
        return len(chunks)


def _fake_team_run() -> SimpleNamespace:
    return SimpleNamespace(
        id="T1",
        project_context=ProjectContext(
            goal="g",
            user_request="u",
            project_key="P1",
            repo_root="/repo",
        ),
    )


async def _unused_runner(*args, **kwargs):  # pragma: no cover - not exercised here
    raise AssertionError("runner should not be used in dirty-path tests")


def _unused_context_builder(*args, **kwargs):  # pragma: no cover - not exercised here
    raise AssertionError("context builder should not be used in dirty-path tests")


def _make_scheduler(subsystems: list[str]) -> AtlasMaintenanceScheduler:
    return AtlasMaintenanceScheduler(
        team_run=_fake_team_run(),
        runner=_unused_runner,
        build_query_context=_unused_context_builder,
        build_posthook_context=_unused_context_builder,
        agent_lookup=lambda name: None,
        store=_StubStore(subsystems),
    )


def _drain_job(scheduler: AtlasMaintenanceScheduler):
    priority, _, job = scheduler._queue.get_nowait()
    scheduler._queued_keys.discard(job.key)
    return priority, job


def test_mark_dirty_path_prefers_most_specific_matching_subsystem() -> None:
    scheduler = _make_scheduler(
        [
            "tests",
            "tests/test_main.py",
            "pydantic",
            "pydantic/main.py",
        ]
    )

    scheduler.mark_dirty_path("/repo/tests/test_main.py")
    assert scheduler._dirty_subsystems == {"tests/test_main.py"}

    scheduler._dirty_subsystems.clear()
    scheduler.mark_dirty_path("/repo/pydantic/main.py")
    assert scheduler._dirty_subsystems == {"pydantic/main.py"}


def test_mark_dirty_path_keeps_equal_specific_owner_slices() -> None:
    scheduler = _make_scheduler(
        [
            "tests",
            "tests/test_main.py",
            "tests/test_main.py|pydantic/main.py",
        ]
    )

    scheduler.mark_dirty_path("/repo/tests/test_main.py")
    assert scheduler._dirty_subsystems == {
        "tests/test_main.py",
        "tests/test_main.py|pydantic/main.py",
    }


def test_mark_dirty_path_falls_back_to_parent_when_no_child_chunk_exists() -> None:
    scheduler = _make_scheduler(["tests"])

    scheduler.mark_dirty_path("/repo/tests/test_main.py")
    assert scheduler._dirty_subsystems == {"tests"}


def test_note_lookup_cold_start_queues_bootstrap_builder_job() -> None:
    scheduler = AtlasMaintenanceScheduler(
        team_run=_fake_team_run(),
        runner=_unused_runner,
        build_query_context=_unused_context_builder,
        build_posthook_context=_unused_context_builder,
        agent_lookup=lambda name: None,
        store=_StubStore([], has_chunks=False),
    )

    scheduler.note_lookup([{"action": "refresh", "subsystem": "tests"}], source="lookup")

    priority, job = _drain_job(scheduler)
    assert priority == 10
    assert job.agent_name == "atlas_builder"
    assert job.key == "P1:__bootstrap__"
    assert job.reason == "lookup:cold-start"


def test_note_lookup_defers_refresh_when_bootstrap_is_already_queued() -> None:
    store = _StubStore(["tests"], has_chunks=True)
    scheduler = AtlasMaintenanceScheduler(
        team_run=_fake_team_run(),
        runner=_unused_runner,
        build_query_context=_unused_context_builder,
        build_posthook_context=_unused_context_builder,
        agent_lookup=lambda name: None,
        store=store,
    )
    scheduler._enqueue_builder(reason="manual-bootstrap", priority=60)

    scheduler.note_lookup([{"action": "refresh", "subsystem": "tests"}], source="lookup")

    assert scheduler._dirty_subsystems == {"tests"}
    assert scheduler._queue.qsize() == 1


def test_note_lookup_refresh_only_ignores_scout_jobs() -> None:
    scheduler = AtlasMaintenanceScheduler(
        team_run=_fake_team_run(),
        runner=_unused_runner,
        build_query_context=_unused_context_builder,
        build_posthook_context=_unused_context_builder,
        agent_lookup=lambda name: None,
        store=_StubStore(["tests"], has_chunks=True),
        policy="refresh_only",
    )

    scheduler.note_lookup([{"action": "scout", "subsystem": "tests"}], source="lookup")

    assert scheduler._queue.qsize() == 0


def test_deferred_persist_policy_skips_lookup_and_dirty_queueing() -> None:
    scheduler = AtlasMaintenanceScheduler(
        team_run=_fake_team_run(),
        runner=_unused_runner,
        build_query_context=_unused_context_builder,
        build_posthook_context=_unused_context_builder,
        agent_lookup=lambda name: None,
        store=_StubStore(["tests"], has_chunks=True),
        policy="deferred_persist",
    )

    scheduler.note_lookup([{"action": "refresh", "subsystem": "tests"}], source="lookup")
    scheduler.mark_dirty_path("/repo/tests/test_main.py")

    assert scheduler._queue.qsize() == 0
    assert scheduler._dirty_subsystems == set()


def test_deferred_persist_policy_accepts_direct_scout_brief_persistence() -> None:
    store = _PersistStore()
    scheduler = AtlasMaintenanceScheduler(
        team_run=_fake_team_run(),
        runner=_unused_runner,
        build_query_context=_unused_context_builder,
        build_posthook_context=_unused_context_builder,
        agent_lookup=lambda name: None,
        store=store,
        policy="deferred_persist",
    )

    persisted = scheduler.persist_direct_scout_brief(
        {
            "target_paths": ["tests"],
            "canonical_scope": "tests",
            "summary": "scout",
            "files": [],
            "scope_coverage": 1.0,
            "gaps": "",
            "suggested_subdivisions": [],
        }
    )

    assert persisted is True
    assert len(store.writes) == 1
