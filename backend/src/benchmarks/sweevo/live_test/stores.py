"""TaskCenterStoreBundle + create_in_memory_task_center_stores.

Relocated from ``benchmarks.sweevo.mock_agent_execution`` in S-03.
"""

from __future__ import annotations

from dataclasses import dataclass

from db.base import Base
import db.models  # noqa: F401 - populate SQLAlchemy metadata
from db.stores.attempt_store import AttemptStore
from db.stores.context_packet_store import ContextPacketStore
from db.stores.episode_store import EpisodeStore
from db.stores.mission_store import MissionStore
from db.stores.task_center_store import TaskCenterStore
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


@dataclass(slots=True)
class TaskCenterStoreBundle:
    engine: Engine
    task_store: TaskCenterStore
    mission_store: MissionStore
    episode_store: EpisodeStore
    attempt_store: AttemptStore
    context_packet_store: ContextPacketStore

    def close(self) -> None:
        self.engine.dispose()


def create_in_memory_task_center_stores() -> TaskCenterStoreBundle:
    """Create isolated real TaskCenter stores for a benchmark run."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    task_store = TaskCenterStore()
    mission_store = MissionStore()
    episode_store = EpisodeStore()
    attempt_store = AttemptStore()
    context_packet_store = ContextPacketStore()
    for store in (
        task_store,
        mission_store,
        episode_store,
        attempt_store,
        context_packet_store,
    ):
        store.initialize(session_factory)

    return TaskCenterStoreBundle(
        engine=engine,
        task_store=task_store,
        mission_store=mission_store,
        episode_store=episode_store,
        attempt_store=attempt_store,
        context_packet_store=context_packet_store,
    )


__all__ = [
    "TaskCenterStoreBundle",
    "create_in_memory_task_center_stores",
]
