"""Source-to-changeset converters for the OCC search/replace gate.

After the OCC simplification, only the overlay-builder remains. The typed
``write_file`` / ``edit_file`` API verbs build :class:`WriteChange` and
:class:`EditChange` directly from the request shape — there is no
intermediate spec layer.
"""

from __future__ import annotations

from collections.abc import Sequence

from sandbox.occ.changeset.types import (
    BinaryChange,
    Change,
    DeleteChange,
    OpaqueDirChange,
    SymlinkChange,
    UpperChangeLike,
    WriteChange,
)
from sandbox.occ.content.hashing import content_hash


def overlay_changes_to_changeset(
    upper: Sequence[UpperChangeLike],
) -> list[Change]:
    """Translate overlay upperdir kinds into typed ``Change``s.

    Encoding rules:

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


__all__ = ["overlay_changes_to_changeset"]
