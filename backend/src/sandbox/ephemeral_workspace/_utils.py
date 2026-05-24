"""Small helpers shared by ephemeral workspace pipeline modules."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil

from sandbox.ephemeral_workspace.events import PathChange
from sandbox.ephemeral_workspace.transient import TRANSIENT_LOWERDIR_DIR


def foreign_watch_interval_s() -> float:
    raw = os.environ.get("EOS_OVERLAY_FOREIGN_WATCH_INTERVAL_S", "").strip()
    if not raw:
        return 0.25
    try:
        return max(0.05, float(raw))
    except ValueError:
        return 0.25


def event_path_change(change: object) -> PathChange:
    if hasattr(change, "path") and hasattr(change, "kind"):
        return PathChange.from_overlay_change(change)  # type: ignore[arg-type]
    return PathChange(path=str(change), kind="write", existed_before=False)


def _drop_transient_lowerdir(
    lowerdir_raw: str | None,
    *,
    storage_root: Path | None,
    scratch_root: Path,
) -> None:
    if not lowerdir_raw:
        return
    lowerdir = Path(str(lowerdir_raw))
    scratch_dir = lowerdir.parent
    transient_roots = {
        (scratch_root / "runtime" / TRANSIENT_LOWERDIR_DIR).resolve(strict=False),
    }
    if storage_root is not None:
        transient_roots.add(
            (storage_root / "runtime" / TRANSIENT_LOWERDIR_DIR).resolve(strict=False)
        )
    if (
        lowerdir.name != "lower"
        or scratch_dir.parent.name != TRANSIENT_LOWERDIR_DIR
        or scratch_dir.parent.resolve(strict=False) not in transient_roots
    ):
        return
    shutil.rmtree(scratch_dir, ignore_errors=True)


def safe_request_part(value: str) -> str:
    safe = "".join(
        char if char.isalnum() or char in ("-", "_") else "-"
        for char in str(value)
    ).strip("-")
    return safe or "operation"


def runtime_key(workspace_ref: str, workspace_root: str) -> str:
    raw = f"{workspace_ref}\0{workspace_root}".encode("utf-8", "surrogateescape")
    return hashlib.sha256(raw).hexdigest()[:16]


__all__ = [
    "_drop_transient_lowerdir",
    "event_path_change",
    "foreign_watch_interval_s",
    "runtime_key",
    "safe_request_part",
]
