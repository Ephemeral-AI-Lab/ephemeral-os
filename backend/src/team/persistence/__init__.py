"""Team definition persistence exports.

Avoid importing the SQLAlchemy-backed model/store at package import time so
non-database unit tests can import lightweight event helpers without the ORM
stack being installed.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["TeamDefinitionRecord", "TeamDefinitionStore"]


def __getattr__(name: str) -> Any:
    if name == "TeamDefinitionRecord":
        return import_module("team.persistence.model").TeamDefinitionRecord
    if name == "TeamDefinitionStore":
        return import_module("team.persistence.store").TeamDefinitionStore
    raise AttributeError(name)
