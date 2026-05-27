"""State-bearing overlay handle."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class OverlayHandle:
    """State-bearing handle for a mounted overlay.

    The unified handle for daemon per-call overlays, plugin per-operation
    overlays, and projection-direct overlays. The differences between those
    flavors live in the captured ``_release`` closure (daemon path emits
    ``LeaseGuard``/audit entries; projection path releases the lease directly)
    and in ``namespace_pid``:

    - per-call overlays leave ``namespace_pid`` as ``None`` because the
      namespace child exits before the tool call returns;
    - long-lived isolated-workspace overlays populate it with the namespace
      holder pid.

    ``_released`` is flipped by ``sandbox.overlay.lifecycle.release_overlay``
    under the per-handle ``_release_lock`` so cleanup is idempotent for
    concurrent release callers.
    """

    workspace_root: str
    layer_paths: tuple[str, ...]
    upperdir: Path
    workdir: Path
    snapshot_version: int
    lease_id: str
    namespace_pid: int | None
    run_dir: Path
    snapshot_manifest: object | None = None
    snapshot_timings: dict[str, float] = field(default_factory=dict)
    manifest_key: str = ""
    manifest_version: int = 0
    root_hash: str = ""
    operation_id: str = ""
    _released: bool = False
    _release_lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
        compare=False,
    )
    _release: Callable[[], None] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def release(self) -> None:
        """Idempotently release the captured lease and run-dir cleanup."""
        with self._release_lock:
            if self._released:
                return
            self._released = True
            release = self._release
        if release is not None:
            release()

    @property
    def released(self) -> bool:
        return self._released


__all__ = ["OverlayHandle"]
