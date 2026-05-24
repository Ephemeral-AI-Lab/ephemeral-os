"""Synthesize an overlay upperdir from a copy-backed merged tree.

When the kernel overlay mount is not available, the copy-backed strategy
runs a command against a merged copy of the base repo. We then diff that
merged tree against the original base to emit the same overlay marker
shape (writes, whiteouts, opaque dirs) the kernel would have produced.

Vocabulary here is overlayfs-native because this file emulates kernel
overlay semantics — that emulation is the file's whole reason for existing.
"""

from __future__ import annotations

import os
import shutil
import stat
from contextlib import suppress
from pathlib import Path

from sandbox.layer_stack.paths import relative_symlink_target_escapes
from sandbox.layer_stack.layer_index import OPAQUE_MARKER, WHITEOUT_PREFIX
from sandbox._shared.clock import monotonic_now


def synthesize_writes(
    *,
    merged: Path,
    base_repo: Path,
    into: Path,
    timings: dict[str, float] | None = None,
) -> None:
    """Populate ``into`` with the overlay diff of ``merged`` vs ``base_repo``."""
    populate_start = monotonic_now()
    _populate_upperdir_from_diff(
        lowerdir=base_repo,
        workspace_root=merged,
        upperdir=into,
    )
    if timings is not None:
        timings["overlay.capture.populate_upperdir_s"] = (
            monotonic_now() - populate_start
        )


def _populate_upperdir_from_diff(
    *,
    lowerdir: Path,
    workspace_root: Path,
    upperdir: Path,
) -> None:
    # Reset upperdir to a clean slate before materializing the diff.
    shutil.rmtree(upperdir, ignore_errors=True)
    upperdir.mkdir(parents=True)

    lower_paths = _payload_paths(lowerdir)
    merged_paths = _payload_paths(workspace_root)
    # Prefix index: every dir that has at least one descendant in merged_paths.
    # O(N) to build, O(1) per lookup — replaces the O(N²) inner scan that
    # `_has_payload_descendant` used to do.
    dirs_with_descendants: set[Path] = {
        parent for path in merged_paths for parent in path.parents if parent != Path(".")
    }

    for rel in sorted(lower_paths - merged_paths):
        if _has_nondirectory_payload_ancestor(
            rel,
            merged_paths,
            root=workspace_root,
        ):
            continue
        _write_whiteout(upperdir, rel)

    for rel in sorted(merged_paths):
        merged_entry = workspace_root / rel
        lower_entry = lowerdir / rel
        if rel in lower_paths and _entries_match(lower_entry, merged_entry):
            continue
        target = upperdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        _remove_path(target)
        if merged_entry.is_symlink():
            link_target = os.readlink(merged_entry)
            if link_target.startswith("/") or relative_symlink_target_escapes(link_target):
                raise ValueError(
                    "overlay capture refuses escaping symlink target: "
                    f"{rel.as_posix()}"
                )
            os.symlink(link_target, target)
        elif merged_entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            with suppress(OSError):
                shutil.copystat(merged_entry, target, follow_symlinks=False)
            if rel not in dirs_with_descendants:
                (target / OPAQUE_MARKER).write_text("", encoding="utf-8")
        elif merged_entry.is_file():
            shutil.copy2(merged_entry, target)


def _payload_paths(root: Path) -> set[Path]:
    if not root.exists():
        return set()
    paths: set[Path] = set()
    for entry in root.rglob("*"):
        if entry.name == OPAQUE_MARKER or entry.name.startswith(WHITEOUT_PREFIX):
            continue
        if entry.is_symlink() or entry.is_file() or entry.is_dir():
            paths.add(entry.relative_to(root))
    return paths


def _entries_match(left: Path, right: Path) -> bool:
    if left.is_symlink() or right.is_symlink():
        return (
            left.is_symlink()
            and right.is_symlink()
            and os.readlink(left) == os.readlink(right)
        )
    if left.is_dir() and right.is_dir():
        return _mode_bits(left) == _mode_bits(right)
    if left.is_file() and right.is_file():
        return (
            left.read_bytes() == right.read_bytes()
            and _mode_bits(left) == _mode_bits(right)
        )
    return False


def _has_nondirectory_payload_ancestor(
    rel: Path,
    payload_paths: set[Path],
    *,
    root: Path,
) -> bool:
    parts = rel.parts
    for index in range(1, len(parts)):
        ancestor = Path(*parts[:index])
        if ancestor not in payload_paths:
            continue
        entry = root / ancestor
        if entry.is_symlink() or entry.is_file():
            return True
    return False


def _mode_bits(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def _write_whiteout(upperdir: Path, rel: Path) -> None:
    marker = upperdir / rel.parent / f"{WHITEOUT_PREFIX}{rel.name}"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


__all__ = ["synthesize_writes"]
