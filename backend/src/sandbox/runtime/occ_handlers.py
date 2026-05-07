"""occ-server externally-reachable surface.

Phase 05 binds the OCC mutation gate to a single externally-reachable
surface: ``apply_changeset`` plus ``start`` / ``stop`` / ``health``
lifecycle. No host-callable ``api.write_*`` / ``api.edit_*`` /
``api.read_*`` symbols live here — those dispatch to
``runtime.handlers`` instead (one module per verb). This module's
``OCC_OP_TABLE`` is a **structural assertion target**, not a wire
dispatch table; the §6 surface check pins its key set to exactly
``{apply_changeset, start, stop, health}``.

OCC remains an in-process Python boundary. Callers consume it via
:class:`sandbox.occ.client.OCCClient`; this module exposes the same
surface as a structural op table for assertions.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from sandbox.occ.changeset.prepared import CommitOptions, PreparedChangeset
from sandbox.occ.changeset.types import Change, ChangesetResult
from sandbox.occ.client import OCCClient


async def apply_changeset(
    occ_client: OCCClient,
    typed_changes: Sequence[Change],
    *,
    snapshot: Any = None,
    options: CommitOptions | None = None,
    workspace_ref: str | None = None,
) -> ChangesetResult | PreparedChangeset:
    """Forward into ``OCCClient.apply_changeset``.

    The mutation gate itself lives inside :class:`OccService` /
    :class:`OccSerialMerger`; this surface preserves that boundary while
    enforcing that occ-server has only one externally-reachable mutation
    method.
    """
    return await occ_client.apply_changeset(
        typed_changes,
        snapshot=snapshot,
        options=options,
        workspace_ref=workspace_ref,
    )


async def start(_args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Lifecycle: in-process OCC has no boot step. Returns ``ok``."""
    del _args
    return {"status": "ok", "running": True}


async def stop(_args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Lifecycle: in-process OCC has no shutdown step. Returns ``ok``."""
    del _args
    return {"status": "ok", "running": False}


async def health(_args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Lifecycle: report occ-server liveness."""
    del _args
    return {"status": "ok"}


OCC_OP_TABLE: dict[str, Callable[..., Awaitable[Any]]] = {
    "apply_changeset": apply_changeset,
    "start": start,
    "stop": stop,
    "health": health,
}
"""Structural surface assertion target.

Phase 05 §6 requires occ-server's externally-reachable wire methods to
equal exactly ``{apply_changeset, start, stop, health}``. Keep this table
in lockstep with the public callable surface of this module; tests in
``test_occ/test_mutation_gate.py`` enforce equality.
"""


__all__ = [
    "OCC_OP_TABLE",
    "apply_changeset",
    "health",
    "start",
    "stop",
]
