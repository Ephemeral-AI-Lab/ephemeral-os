"""State-bearing overlay handle."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class OverlayHandle:
    """State-bearing handle for a mounted overlay.

    This is not a value type. ``_destroyed`` is flipped by
    ``sandbox.overlay.lifecycle.destroy`` under the per-handle ``_destroy_lock``
    so cleanup is idempotent for concurrent destroy callers.

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
    snapshot_manifest: object | None = None
    _destroyed: bool = False
    _destroy_lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
        compare=False,
    )
    _release: Callable[[], bool] | None = field(
        default=None,
        repr=False,
        compare=False,
    )


__all__ = ["OverlayHandle"]
