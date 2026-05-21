"""Runtime scratch-root selection for mount-backed command execution."""

from __future__ import annotations

import os
from pathlib import Path


def command_exec_scratch_root(storage_root: Path) -> Path:
    """Return the writable scratch filesystem for overlay upper/work dirs.

    Docker-backed sandboxes mount the container root itself as overlayfs, and
    overlayfs rejects using that filesystem as a writable upperdir. The runtime
    bootstrap provides ``/eos-mount-scratch`` as tmpfs for mount-backed scratch;
    fall back to ``storage_root`` only when that mount is unavailable.
    """
    raw = os.environ.get("EPHEMERALOS_COMMAND_EXEC_SCRATCH_ROOT", "").strip()
    if raw:
        return Path(raw)
    mount_scratch = Path("/eos-mount-scratch")
    if mount_scratch.is_dir() and os.access(mount_scratch, os.W_OK | os.X_OK):
        return mount_scratch / "eos-sandbox-runtime"
    return storage_root


__all__ = ["command_exec_scratch_root"]
