"""Optimistic concurrency control package."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORT_MODULES = {
    "Change": "sandbox.occ.changeset",
    "ChangesetResult": "sandbox.occ.changeset",
    "CommitOptions": "sandbox.occ.changeset",
    "FileResult": "sandbox.occ.changeset",
    "FileStatus": "sandbox.occ.changeset",
    "PreparedChangeset": "sandbox.occ.changeset",
    "CommitQueue": "sandbox.occ.commit_queue",
    "CommitTransaction": "sandbox.occ.commit_transaction",
    "ChangesetPreparer": "sandbox.occ.changeset_preparation",
    "DirectStager": "sandbox.occ.path_staging",
    "GatedStager": "sandbox.occ.path_staging",
    "OccClient": "sandbox.occ.client",
    "OccService": "sandbox.occ.service",
}

__all__ = sorted(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
