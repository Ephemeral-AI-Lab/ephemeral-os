"""Phase 04 ``/api/db/task-center-runs/{id}/attempt`` route tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.base import Base
import db.models  # noqa: F401  - populates Base.metadata
from db.models.task_center import TaskCenterRequestRecord, TaskCenterRunRecord
from db.stores.agent_run_store import AgentRunStore
from db.stores.mission_store import MissionStore
from db.stores.attempt_store import AttemptStore
from db.stores.task_center_store import TaskCenterStore
from db.stores.episode_store import EpisodeStore
from server.routers.persistence import create_persistence_router
from task_center.mission.mission import MissionStatus
from task_center.attempt import AttemptStatus
from task_center.episode.episode import (
    EpisodeCreationReason,
    EpisodeStatus,
)
from task_center.task import HarnessTaskRole, HarnessTaskStatus


@pytest.fixture
def stores():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with sf() as s:
        s.add(
            TaskCenterRequestRecord(
                id="req1",
                cwd="/tmp",
                sandbox_id=None,
                request_prompt="prompt",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        s.add(
            TaskCenterRunRecord(
                id="run1",
                request_id="req1",
                status="running",
                started_at=datetime.now(UTC),
            )
        )
        s.commit()
    task_center = TaskCenterStore()
    task_center.initialize(sf)
    agent_run = AgentRunStore()
    agent_run.initialize(sf)
    mission_store = MissionStore()
    mission_store.initialize(sf)
    episode_store = EpisodeStore()
    episode_store.initialize(sf)
    attempt_store = AttemptStore()
    attempt_store.initialize(sf)
    yield (task_center, agent_run, mission_store, episode_store, attempt_store)
    engine.dispose()


def _client(stores) -> TestClient:
    app = FastAPI()
    app.include_router(create_persistence_router(*stores))
    return TestClient(app)


def test_graph_route_walks_request_segment_graph_schema(stores):
    task_center, _, mission_store, episode_store, attempt_store = stores
    request = mission_store.insert(
        task_center_run_id="run1",
        requested_by_task_id="executor-1",
        goal="solve",
    )
    episode = episode_store.insert(
        mission_id=request.id,
        sequence_no=1,
        creation_reason=EpisodeCreationReason.INITIAL,
        goal="solve",
        attempt_budget=2,
    )
    mission_store.append_episode_id(request.id, episode.id)
    attempt = attempt_store.insert(episode_id=episode.id, attempt_sequence_no=1)
    episode_store.append_attempt_id(episode.id, attempt.id)
    task_center.upsert_task(
        task_id="task-1",
        task_center_run_id="run1",
        role=HarnessTaskRole.GENERATOR.value,
        agent_name="executor",
        task_input="do work",
        status=HarnessTaskStatus.RUNNING.value,
        summaries=[],
        needs=[],
        task_center_attempt_id=attempt.id,
    )

    response = _client(stores).get("/api/db/task-center-runs/run1/attempt")
    assert response.status_code == 200
    body = response.json()

    assert "missions" in body
    assert "attempts_index" in body
    [r] = body["missions"]
    assert r["id"] == request.id
    assert r["status"] == MissionStatus.OPEN.value
    [s] = r["episodes"]
    assert s["id"] == episode.id
    assert s["status"] == EpisodeStatus.OPEN.value
    [g] = s["attempts"]
    assert g["id"] == attempt.id
    assert g["status"] == AttemptStatus.RUNNING.value
    assert {"task-1"} == {t["id"] for t in g["tasks"]}
    [idx] = body["attempts_index"]
    assert idx == {
        "attempt_id": attempt.id,
        "mission_id": request.id,
        "episode_id": episode.id,
    }


def test_graph_route_orders_by_sequence_no(stores):
    _, _, mission_store, episode_store, attempt_store = stores
    request = mission_store.insert(
        task_center_run_id="run1",
        requested_by_task_id="executor-1",
        goal="solve",
    )
    segment1 = episode_store.insert(
        mission_id=request.id,
        sequence_no=1,
        creation_reason=EpisodeCreationReason.INITIAL,
        goal="seg1 goal",
        attempt_budget=2,
    )
    mission_store.append_episode_id(request.id, segment1.id)
    episode_store.set_continuation_goal(segment1.id, "go on")
    episode_store.set_status(
        segment1.id, status=EpisodeStatus.SUCCEEDED, closed_at=datetime.now(UTC)
    )
    segment2 = episode_store.insert(
        mission_id=request.id,
        sequence_no=2,
        creation_reason=EpisodeCreationReason.PARTIAL_CONTINUATION,
        goal="go on",
        attempt_budget=2,
    )
    mission_store.append_episode_id(request.id, segment2.id)
    g1 = attempt_store.insert(episode_id=segment1.id, attempt_sequence_no=1)
    episode_store.append_attempt_id(segment1.id, g1.id)
    g2 = attempt_store.insert(episode_id=segment2.id, attempt_sequence_no=1)
    episode_store.append_attempt_id(segment2.id, g2.id)

    response = _client(stores).get("/api/db/task-center-runs/run1/attempt")
    body = response.json()
    [r] = body["missions"]
    seqs = [s["sequence_no"] for s in r["episodes"]]
    assert seqs == [1, 2]


def test_graph_route_returns_503_when_stores_unready(stores):
    """Persistence stores must report 503 when not initialised."""
    task_center, agent_run, _, _, _ = stores
    # Use uninitialised attempt stores to simulate not-ready state.
    mission_store = MissionStore()
    episode_store = EpisodeStore()
    attempt_store = AttemptStore()
    app = FastAPI()
    app.include_router(
        create_persistence_router(
            task_center, agent_run, mission_store, episode_store, attempt_store
        )
    )
    client = TestClient(app)
    response = client.get("/api/db/task-center-runs/run1/attempt")
    assert response.status_code == 503


def test_graph_route_includes_retry_attempts_in_segment(stores):
    _, _, mission_store, episode_store, attempt_store = stores
    request = mission_store.insert(
        task_center_run_id="run1",
        requested_by_task_id="executor-1",
        goal="solve",
    )
    episode = episode_store.insert(
        mission_id=request.id,
        sequence_no=1,
        creation_reason=EpisodeCreationReason.INITIAL,
        goal="solve",
        attempt_budget=3,
    )
    mission_store.append_episode_id(request.id, episode.id)
    g1 = attempt_store.insert(episode_id=episode.id, attempt_sequence_no=1)
    episode_store.append_attempt_id(episode.id, g1.id)
    g2 = attempt_store.insert(episode_id=episode.id, attempt_sequence_no=2)
    episode_store.append_attempt_id(episode.id, g2.id)

    response = _client(stores).get("/api/db/task-center-runs/run1/attempt")
    body = response.json()
    [r] = body["missions"]
    [s] = r["episodes"]
    seqs = [g["attempt_sequence_no"] for g in s["attempts"]]
    assert seqs == [1, 2]
