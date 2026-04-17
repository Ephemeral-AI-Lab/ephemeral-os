"""Walk an overlayfs upperdir tarball into structured changes.

Overlay upperdir entries encode three kinds of change relative to
lowerdir:

* **Regular files / symlinks** — "this path has this new content/target".
* **Character device with (major, minor) == (0, 0)** — whiteout: "this
  path was deleted".
* **Directories with xattr ``user.overlay.opaque=y``** — opaque: "the
  lowerdir's version of this directory is replaced wholesale; children
  visible in upperdir are the only survivors".

This module consumes the tar produced by :mod:`overlay_exec` and yields
one :class:`UpperdirChange` per structural change, filtering out
ignored paths (notably ``.git/``).

The tar is created inside the namespace with
``tar --xattrs --xattrs-include='user.overlay.*'``. Python's
:mod:`tarfile` surfaces xattrs via the ``pax_headers`` dict keyed by
``SCHILY.xattr.<name>`` (GNU tar convention).
"""

from __future__ import annotations

import logging
import os
import stat
import tarfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

_OPAQUE_PAX_KEY = "SCHILY.xattr.user.overlay.opaque"
_DEFAULT_IGNORE_PREFIXES: tuple[str, ...] = (".git/", ".git")


class ChangeKind(Enum):
    MODIFY = "modify"
    """Regular file with new content (also covers create)."""
    DELETE = "delete"
    """Whiteout: path removed."""
    OPAQUE_DIR = "opaque_dir"
    """Directory that replaces its lowerdir counterpart wholesale."""
    SYMLINK = "symlink"
    """Symlink create or update to ``target``."""
    CHMOD = "chmod"
    """Mode-only change (no content delta). Reserved; current walker
    emits MODIFY for any regular-file entry since overlay copies the
    full file on any attribute touch."""


@dataclass(frozen=True)
class UpperdirChange:
    """One structural change extracted from the upperdir tarball."""

    kind: ChangeKind
    path: str
    """Path relative to the overlay merged root (== repo_root)."""
    content: bytes | None = None
    """New content for MODIFY; ``None`` otherwise."""
    mode: int | None = None
    """POSIX mode bits (from tar member). ``None`` if not applicable."""
    symlink_target: str | None = None
    """Only populated for SYMLINK."""


def _normalize(path: str) -> str | None:
    """Return a normalized repo-relative path, or ``None`` to skip.

    ``tar -C dir .`` emits entries as ``./pkg/foo`` and the root as
    ``.`` — we strip exactly one leading ``./`` (not a char set) so
    entries like ``./.git/HEAD`` don't have their leading ``.`` eaten.
    """
    if path == "." or path == "./":
        return None
    cleaned = path[2:] if path.startswith("./") else path
    cleaned = cleaned.rstrip("/")
    return cleaned or None


def _is_whiteout(info: tarfile.TarInfo) -> bool:
    return info.ischr() and info.devmajor == 0 and info.devminor == 0


def _is_opaque_dir(info: tarfile.TarInfo) -> bool:
    if not info.isdir():
        return False
    value = info.pax_headers.get(_OPAQUE_PAX_KEY, "")
    return value == "y"


def _ignored(path: str, ignore_prefixes: tuple[str, ...]) -> bool:
    return any(path == p or path.startswith(p) for p in ignore_prefixes)


def iter_upperdir_changes(
    tar_path: str,
    *,
    ignore_prefixes: Iterable[str] = _DEFAULT_IGNORE_PREFIXES,
) -> Iterator[UpperdirChange]:
    """Yield one :class:`UpperdirChange` per actionable tar entry.

    Parameters
    ----------
    tar_path:
        Path to a tarball produced by ``OverlayExec``.
    ignore_prefixes:
        Repo-relative prefixes to skip. Defaults exclude ``.git/``.
    """
    prefixes = tuple(ignore_prefixes)
    with tarfile.open(tar_path, mode="r") as tf:
        for info in tf:
            rel = _normalize(info.name)
            if rel is None:
                continue
            if _ignored(rel, prefixes):
                continue

            if _is_whiteout(info):
                yield UpperdirChange(kind=ChangeKind.DELETE, path=rel)
                continue
            if _is_opaque_dir(info):
                yield UpperdirChange(
                    kind=ChangeKind.OPAQUE_DIR,
                    path=rel,
                    mode=stat.S_IMODE(info.mode),
                )
                continue
            if info.issym():
                yield UpperdirChange(
                    kind=ChangeKind.SYMLINK,
                    path=rel,
                    mode=stat.S_IMODE(info.mode),
                    symlink_target=info.linkname,
                )
                continue
            if info.isreg():
                reader = tf.extractfile(info)
                content = reader.read() if reader is not None else b""
                yield UpperdirChange(
                    kind=ChangeKind.MODIFY,
                    path=rel,
                    content=content,
                    mode=stat.S_IMODE(info.mode),
                )
                continue
            # Plain directories (not opaque): no change to emit.
            # Hardlinks, fifos, block devices: unsupported; warn once.
            if info.isdir():
                continue
            logger.debug(
                "upperdir_walker: skipping unsupported entry %s (type=%s)",
                rel,
                info.type,
            )


def collect_upperdir_changes(
    tar_path: str,
    *,
    ignore_prefixes: Iterable[str] = _DEFAULT_IGNORE_PREFIXES,
) -> list[UpperdirChange]:
    """Eagerly materialize all changes. Convenience for callers that
    don't need streaming."""
    return list(iter_upperdir_changes(tar_path, ignore_prefixes=ignore_prefixes))


def cleanup_tar(tar_path: str) -> None:
    """Remove an audit tarball on a best-effort basis."""
    try:
        os.unlink(tar_path)
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.debug("upperdir_walker: failed to remove %s: %s", tar_path, exc)


__all__ = [
    "ChangeKind",
    "UpperdirChange",
    "collect_upperdir_changes",
    "iter_upperdir_changes",
    "cleanup_tar",
]
