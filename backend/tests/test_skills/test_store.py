"""Unit tests for skill definition store behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.base import Base
from skills.db.model import SkillDefinitionRecord  # noqa: F401
from skills.db.store import SkillDefinitionStore


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def store(session_factory) -> SkillDefinitionStore:
    skill_store = SkillDefinitionStore()
    skill_store.initialize(session_factory)
    return skill_store


def test_update_ignores_immutable_fields(store: SkillDefinitionStore) -> None:
    now = datetime.now(UTC)
    created = store.create(
        SkillDefinitionRecord(
            id=str(uuid4()),
            name="deploy",
            description="Deploy guidance",
            content="v1",
            version=4,
            created_at=now,
            updated_at=now,
        )
    )

    updated = store.update(
        "deploy",
        {
            "description": "Updated guidance",
            "content": "v2",
            "id": "replacement",
            "name": "renamed",
            "created_at": datetime.now(UTC),
            "version": 999,
        },
    )

    assert updated.id == created.id
    assert updated.name == "deploy"
    assert updated.description == "Updated guidance"
    assert updated.content == "v2"
    assert updated.version == 5


def test_soft_delete_hides_skill_from_active_lookup(store: SkillDefinitionStore) -> None:
    store.create(
        SkillDefinitionRecord(
            id=str(uuid4()),
            name="cleanup",
            description="Cleanup guidance",
            content="body",
        )
    )

    deleted = store.soft_delete("cleanup")

    assert deleted is True
    assert store.get_by_name("cleanup") is None
    assert [record.name for record in store.list_active()] == []
