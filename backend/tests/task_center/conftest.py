"""Shared fixtures for task_center tests: in-memory SQLite DB + stores."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.base import Base
import db.models  # noqa: F401  - populates Base.metadata
from db.models.task_center import TaskCenterRequestRecord, TaskCenterRunRecord
from db.stores.complex_task_request_store import ComplexTaskRequestStore
from db.stores.harness_graph_store import HarnessGraphStore
from db.stores.task_segment_store import TaskSegmentStore


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    # Seed parent task_center_run for FK satisfaction.
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
    yield sf
    engine.dispose()


@pytest.fixture
def request_store(session_factory) -> ComplexTaskRequestStore:
    store = ComplexTaskRequestStore()
    store.initialize(session_factory)
    return store


@pytest.fixture
def segment_store(session_factory) -> TaskSegmentStore:
    store = TaskSegmentStore()
    store.initialize(session_factory)
    return store


@pytest.fixture
def graph_store(session_factory) -> HarnessGraphStore:
    store = HarnessGraphStore()
    store.initialize(session_factory)
    return store


@pytest.fixture
def task_center_run_id() -> str:
    return "run1"
