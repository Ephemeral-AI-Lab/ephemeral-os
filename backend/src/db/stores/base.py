"""Base mixin for stores that use lazy ``initialize()`` + session factory.

``SyncStoreMixin`` wraps a ``sessionmaker[Session]``. Subclasses inherit
``__init__``, ``initialize``, ``initialized`` (with ``is_ready`` alias), and
the ``_sf`` accessor that asserts readiness.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


class SyncStoreMixin:
    """Lazy-init pattern for synchronous SQLAlchemy stores."""

    _store_label: ClassVar[str] = ""  # override for log messages

    def __init__(self) -> None:
        self._session_factory: sessionmaker[Session] | None = None

    def initialize(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        label = self._store_label or self.__class__.__name__
        logger.info("%s initialised", label)

    @property
    def initialized(self) -> bool:
        """True once ``initialize`` has been called."""
        return self._session_factory is not None

    # Keep ``is_ready`` as an alias — several call sites use it.
    is_ready = initialized

    @property
    def _sf(self) -> sessionmaker[Session]:
        if self._session_factory is None:
            raise RuntimeError(f"{self.__class__.__name__} not initialised")
        return self._session_factory
