"""Source-to-changeset converters for the OCC search/replace gate.

Three boundary functions translate the existing API/overlay shapes into the
strongly-typed ``Change`` union from :mod:`sandbox.occ.changeset.types`:

* :func:`write_specs_to_changeset` — typed ``write_file`` request → ``WriteChange``.
* :func:`edit_specs_to_changeset` — typed ``edit_file`` request → ``EditChange``.
* :func:`overlay_changes_to_changeset` — overlay upperdir scan →
  ``WriteChange``/``DeleteChange``/``SymlinkChange``/``OpaqueDirChange``/
  ``BinaryChange``.

See plan §Source-to-changeset mapping for capture-point semantics.
"""

from __future__ import annotations

from collections.abc import Sequence

from sandbox.occ.changeset.types import (
    BinaryChange,
    Change,
    DeleteChange,
    EditChange,
    OpaqueDirChange,
    SymlinkChange,
    UpperChangeLike,
    WriteChange,
)
from sandbox.occ.content.hashing import content_hash
from sandbox.occ.content.manager import ContentManager
from sandbox.occ.types import EditSpec, WriteSpec


def write_specs_to_changeset(
    specs: Sequence[WriteSpec],
    *,
    content: ContentManager,
) -> list[Change]:
    """Translate ``write_file`` API specs into ``WriteChange``s.

    Reads each path's current content via ``content`` to compute the
    pre-mutation ``base_hash``. ``base_existed`` follows ``overwrite``: when the
    caller forbids overwrite (``overwrite=False``) the change pins the absent
    state, so the gate aborts if anything has appeared at the path by the time
    it acquires the per-file lock.
    """
    if not specs:
        return []
    paths = [spec.file_path for spec in specs]
    base = content.read_many(paths, allow_missing=True)
    out: list[Change] = []
    for spec in specs:
        current, existed = base[spec.file_path]
        out.append(
            WriteChange(
                path=spec.file_path,
                base_hash=content_hash(current) if existed else "",
                base_existed=existed if spec.overwrite else False,
                final_content=spec.content,
            )
        )
    return out


def edit_specs_to_changeset(specs: Sequence[EditSpec]) -> list[Change]:
    """Translate ``edit_file`` API specs into ``EditChange``s.

    No base read is needed: the gate re-reads each file under its per-file lock
    and uses ``old_text in current`` as the conflict signal.
    """
    return [
        EditChange(path=spec.file_path, edits=tuple(spec.edits))
        for spec in specs
    ]


def overlay_changes_to_changeset(
    upper: Sequence[UpperChangeLike],
) -> list[Change]:
    """Translate overlay upperdir kinds into typed ``Change``s.

    Encoding rules (mirrors plan §Source-to-changeset mapping):

    * ``regular`` UTF-8 → ``WriteChange`` (gated)
    * ``regular`` non-UTF-8 → ``BinaryChange`` (direct)
    * ``whiteout`` with ``base_existed`` → ``DeleteChange`` (gated)
    * ``whiteout`` without ``base_existed`` → skipped (no-op)
    * ``symlink`` → ``SymlinkChange`` (direct)
    * ``opaque_dir`` → ``OpaqueDirChange`` (direct;
      ``kept_children`` = first segment of any sibling change under the prefix)
    """
    out: list[Change] = []
    for change in upper:
        if change.kind == "whiteout":
            if not change.base_existed:
                continue
            base_bytes = change.base_bytes or b""
            try:
                base_text = base_bytes.decode("utf-8")
            except UnicodeDecodeError:
                out.append(BinaryChange(path=change.rel, final_bytes=None))
                continue
            out.append(
                DeleteChange(path=change.rel, base_hash=content_hash(base_text))
            )
            continue

        if change.kind == "regular":
            upper_bytes = change.upper_bytes or b""
            try:
                final_text = upper_bytes.decode("utf-8")
                base_text = (change.base_bytes or b"").decode("utf-8") if change.base_existed else ""
            except UnicodeDecodeError:
                out.append(BinaryChange(path=change.rel, final_bytes=upper_bytes))
                continue
            out.append(
                WriteChange(
                    path=change.rel,
                    base_hash=content_hash(base_text) if change.base_existed else "",
                    base_existed=change.base_existed,
                    final_content=final_text,
                )
            )
            continue

        if change.kind == "symlink":
            target = (change.upper_bytes or b"").decode("utf-8", errors="replace")
            out.append(SymlinkChange(path=change.rel, target=target))
            continue

        if change.kind == "opaque_dir":
            kept = _kept_children_for(change.rel, upper)
            out.append(OpaqueDirChange(path=change.rel, kept_children=frozenset(kept)))
            continue

    return out


def _kept_children_for(
    rel: str,
    all_changes: Sequence[UpperChangeLike],
) -> set[str]:
    prefix = f"{rel}/"
    kept: set[str] = set()
    for item in all_changes:
        if not item.rel.startswith(prefix):
            continue
        rest = item.rel[len(prefix):]
        if not rest:
            continue
        kept.add(rest.split("/", 1)[0])
    return kept


__all__ = [
    "edit_specs_to_changeset",
    "overlay_changes_to_changeset",
    "write_specs_to_changeset",
]
