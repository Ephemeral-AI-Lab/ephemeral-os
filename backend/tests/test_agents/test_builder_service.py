"""Unit tests for agent builder/store refactors."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.api.schemas import AgentDefinitionCreate
from agents.builder.service import AgentBuilderService
from agents.builder.validation import AgentDefinitionValidator
from agents.db.model import AgentDefinitionRecord  # noqa: F401
from agents.db.store import AgentDefinitionStore
from db.base import Base


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def store(session_factory) -> AgentDefinitionStore:
    agent_store = AgentDefinitionStore()
    agent_store.initialize(session_factory)
    return agent_store


@pytest.fixture
def service(store: AgentDefinitionStore, monkeypatch: pytest.MonkeyPatch) -> AgentBuilderService:
    validator = AgentDefinitionValidator(tool_registry=None)
    monkeypatch.setattr("agents.registry.get_definition", lambda name: None)
    monkeypatch.setattr("agents.registry.register_definition", lambda definition: None)
    return AgentBuilderService(store, validator)


def test_create_agent_reactivates_inactive_record(service: AgentBuilderService, store: AgentDefinitionStore) -> None:
    now = datetime.now(UTC)
    store.create(
        AgentDefinitionRecord(
            id=str(uuid4()),
            name="planner",
            description="old",
            model="gpt-old",
            is_active=False,
            version=3,
            created_at=now,
            updated_at=now,
        )
    )

    response = service.create_agent(
        AgentDefinitionCreate(
            name="planner",
            description="new",
            model="gpt-new",
            skills=["triage"],
            background=True,
            metadata={"owner": "ops"},
            created_by="tester",
        )
    )

    record = store.get_by_name("planner", active_only=False)
    assert record is not None
    assert response.id == record.id
    assert record.is_active is True
    assert record.version == 4
    assert record.description == "new"
    assert record.model == "gpt-new"
    assert record.skills == ["triage"]
    assert record.background is True
    assert record.metadata_json == {"owner": "ops"}
    assert record.created_by == "tester"


def test_clone_reuses_inactive_target_record(store: AgentDefinitionStore) -> None:
    now = datetime.now(UTC)
    source = store.create(
        AgentDefinitionRecord(
            id=str(uuid4()),
            name="source",
            description="Source agent",
            model="gpt-4.1",
            skills=["analyze"],
            tags=["team"],
            metadata_json={"priority": "high"},
            created_at=now,
            updated_at=now,
        )
    )
    inactive_target = store.create(
        AgentDefinitionRecord(
            id=str(uuid4()),
            name="target",
            description="stale",
            model="old-model",
            is_active=False,
            version=2,
            created_at=now,
            updated_at=now,
        )
    )

    cloned = store.clone("source", "target")

    assert cloned.id == inactive_target.id
    assert cloned.id != source.id
    assert cloned.is_active is True
    assert cloned.version == 3
    assert cloned.description == "Source agent"
    assert cloned.model == "gpt-4.1"
    assert cloned.skills == ["analyze"]
    assert cloned.tags == ["team"]
    assert cloned.metadata_json == {"priority": "high"}
