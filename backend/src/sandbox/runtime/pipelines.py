"""In-sandbox runtime pipelines.

OCC pipelines run local to the deployed runtime process and are reached through
``sandbox.runtime.server`` handlers. Public ``sandbox.api`` verbs are wired in a
later slice.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from typing import NoReturn

from sandbox.occ.engine import LocalOCCEngine
from sandbox.occ.types import EditSpec, OperationResult, WriteSpec


def shell_pipeline(*_args: object, **_kwargs: object) -> NoReturn:
    raise NotImplementedError("shell_pipeline is implemented in Slice 5b")


@contextmanager
def _occ_engine(workspace_root: str):
    engine = LocalOCCEngine(workspace_root=workspace_root)
    try:
        yield engine
    finally:
        engine.dispose()


def edit_pipeline(
    specs: Sequence[EditSpec] | EditSpec,
    *,
    workspace_root: str = "/workspace",
    agent_id: str = "",
    description: str = "",
) -> OperationResult:
    """Apply a batch of edit specs and commit once through OCC."""
    with _occ_engine(workspace_root) as engine:
        return engine.edit_file(
            specs,
            agent_id=agent_id,
            description=description,
        )


def write_pipeline(
    specs: Sequence[WriteSpec] | WriteSpec,
    *,
    workspace_root: str = "/workspace",
    agent_id: str = "",
    description: str = "",
) -> OperationResult:
    """Write files and commit once through OCC."""
    with _occ_engine(workspace_root) as engine:
        return engine.write_file(
            specs,
            agent_id=agent_id,
            description=description,
        )


__all__ = ["edit_pipeline", "shell_pipeline", "write_pipeline"]
