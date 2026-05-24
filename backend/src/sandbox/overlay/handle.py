"""State-bearing overlay handle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class OverlayHandle:
    """State-bearing handle for a mounted overlay.

    This is not a value type. ``_destroyed`` is flipped by
    ``sandbox.overlay.lifecycle.destroy`` so cleanup is idempotent.

    ``namespace_pid`` lifecycle:
    - isolated workspace handles populate it with the long-lived namespace
      holder pid;
    - ephemeral per-call handles leave it as ``None`` because the namespace
      child exits before the tool call returns.
    """

    workspace_root: str
    layer_paths: tuple[str, ...]
    upperdir: Path
    workdir: Path
    snapshot_version: int
    lease_id: str
    namespace_pid: int | None
    _destroyed: bool = False
    _release: Callable[[], bool] | None = field(
        default=None,
        repr=False,
        compare=False,
    )


__all__ = ["OverlayHandle"]
