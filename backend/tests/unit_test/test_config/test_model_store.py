"""Tests for DB-backed model registration store behavior."""

from __future__ import annotations

from datetime import UTC, datetime

import db.models  # noqa: F401 - populate Base.metadata
from config import model_config
from db.base import Base
from db.models.model_registration import ModelRegistrationRecord
from db.stores.model_store import ModelStore
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _store():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    store = ModelStore()
    return engine, sf, store


def test_model_store_uses_sync_store_readiness_contract() -> None:
    engine, sf, store = _store()
    try:
        assert store.is_ready is False
        assert not hasattr(store, "is_available")

        store.initialize(sf)

        assert store.is_ready is True
    finally:
        engine.dispose()


def test_to_dict_model_id_uses_present_key_not_truthiness() -> None:
    engine, sf, store = _store()
    try:
        store.initialize(sf)
        record = store.register(
            key="zero-model",
            label="Zero Model",
            class_path="providers.clients.fake.FakeClient",
            kwargs={"model": 0},
        )

        assert record["model_id"] == 0
    finally:
        engine.dispose()


def test_active_model_id_uses_present_key_not_truthiness(monkeypatch) -> None:
    monkeypatch.setattr(
        model_config,
        "get_active_model_kwargs",
        lambda: {"model": 0},
    )

    assert model_config.get_active_model_id() == "0"


def test_delete_active_model_promotes_oldest_remaining_model() -> None:
    engine, sf, store = _store()
    try:
        store.initialize(sf)
        store.register(
            key="delete-me",
            label="Delete Me",
            class_path="providers.clients.fake.FakeClient",
            kwargs={"model": "delete-me"},
            activate=True,
        )
        store.register(
            key="oldest",
            label="Oldest",
            class_path="providers.clients.fake.FakeClient",
            kwargs={"model": "oldest"},
        )
        store.register(
            key="newest",
            label="Newest",
            class_path="providers.clients.fake.FakeClient",
            kwargs={"model": "newest"},
        )
        with sf() as db:
            db.query(ModelRegistrationRecord).filter_by(key="oldest").update(
                {"created_at": datetime(2026, 1, 1, tzinfo=UTC)}
            )
            db.query(ModelRegistrationRecord).filter_by(key="newest").update(
                {"created_at": datetime(2026, 1, 2, tzinfo=UTC)}
            )
            db.commit()

        assert store.delete("delete-me") is True

        active = store.get_active()
        assert active is not None
        assert active["key"] == "oldest"
    finally:
        engine.dispose()
