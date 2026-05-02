"""Storage layer for the in-sandbox CI runtime.

Owns the ``$HOME/.cache/eos-ci/<workspace_root_hash>/v1/`` resolver, an atomic
snapshot writer, an integrity-checking reader, and a load-bearing
path-confinement guard.

Phase 1 ships pickle snapshots; Phase 3.5 will swap in SQLite without
changing the public ``write_snapshot`` / ``read_snapshot`` API.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import pickle
import tempfile
from pathlib import Path
from typing import Any

__all__ = [
    "CiStoragePathEscape",
    "CiStorageUnavailable",
    "_confine",
    "read_snapshot",
    "state_dir",
    "workspace_root_hash",
    "write_snapshot",
]

logger = logging.getLogger(__name__)


class CiStorageUnavailable(Exception):
    """Raised when ``$HOME/.cache/eos-ci/...`` cannot be created or written.

    Carries the ``errno`` and resolved path so the Phase 1 privilege probe
    can fail loud with the exact context.
    """

    def __init__(self, errno: int, path: str, message: str) -> None:
        super().__init__(message)
        self.errno = errno
        self.path = path
        self.message = message


class CiStoragePathEscape(Exception):
    """Raised when a write target escapes the state-dir confinement."""


def workspace_root_hash(workspace_root: str) -> str:
    """Stable 16-hex digest of ``realpath(workspace_root)``.

    Symlinks resolve to the same hash as their target — ``ci_index`` and the
    daemon must agree on the snapshot location even when callers pass
    differently-symlinked paths.
    """
    real = os.path.realpath(workspace_root)
    return hashlib.sha256(real.encode("utf-8")).hexdigest()[:16]


def state_dir(workspace_root: str) -> Path:
    """Resolve ``$HOME/.cache/eos-ci/<wh>/v1/`` and ``mkdir -p``.

    Raises :class:`CiStorageUnavailable` if the directory cannot be created
    (typically a privilege failure on a sandbox image where ``$HOME`` is not
    writable). Does NOT silently fall back to ``/tmp`` — surfacing the
    failure is load-bearing for the Phase 1 privilege probe.
    """
    home = Path(os.path.expanduser("~"))
    base = home / ".cache" / "eos-ci" / workspace_root_hash(workspace_root) / "v1"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise CiStorageUnavailable(
            errno=exc.errno or errno.EACCES,
            path=str(base),
            message=(
                f"Cannot create CI state dir at {base} (errno={exc.errno}); "
                f"running as user={os.getenv('USER')}, HOME={home}"
            ),
        ) from exc
    except OSError as exc:
        raise CiStorageUnavailable(
            errno=exc.errno or errno.EACCES,
            path=str(base),
            message=(
                f"Cannot create CI state dir at {base} (errno={exc.errno}, {exc.strerror}); "
                f"HOME={home}"
            ),
        ) from exc
    return base


def _confine(state: Path, name: str) -> Path:
    """Resolve ``name`` under ``state`` and reject path traversal.

    Load-bearing for the storage boundary: an RPC handler must not be able
    to write outside the per-workspace state directory. Rejects ``..``,
    absolute paths, and symlink-traversal targets that escape ``state`` after
    resolution.
    """
    state_real = state.resolve()
    target = (state / name).resolve()
    if target == state_real:
        raise CiStoragePathEscape(
            f"target {target} resolves to the state dir itself"
        )
    if state_real not in target.parents:
        raise CiStoragePathEscape(
            f"path {target} escapes state dir {state_real}"
        )
    return target


def write_snapshot(state: Path, name: str, payload: Any) -> None:
    """Atomic pickle write into ``state/name``.

    Writes to a temp file in the same directory, fsyncs, then ``os.replace``s
    onto the final target. Cleans up the temp file on exception. Pickle
    protocol 5; ``payload`` may be any pickleable structure.
    """
    target = _confine(state, name)
    fd, tmp = tempfile.mkstemp(prefix=f".{Path(name).name}.", suffix=".tmp", dir=state)
    try:
        with os.fdopen(fd, "wb") as f:
            pickle.dump(payload, f, protocol=5)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_snapshot(state: Path, name: str) -> Any | None:
    """Load a pickle snapshot from ``state/name``.

    Returns ``None`` for a missing target. On any pickle/IO corruption,
    logs a warning, unlinks the corrupt file, and returns ``None`` so the
    caller can rebuild from scratch.
    """
    target = _confine(state, name)
    if not target.exists():
        return None
    try:
        with open(target, "rb") as f:
            return pickle.load(f)
    except (EOFError, pickle.UnpicklingError, OSError) as exc:
        logger.warning(
            "ci_storage: corrupt snapshot at %s (%s); unlinking",
            target,
            exc,
        )
        try:
            target.unlink()
        except OSError:
            pass
        return None
