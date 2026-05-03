"""Shared constants and runtime-bundle helpers for overlay command execution."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

RUN_DIR_PREFIX = "/tmp/eos-shell-overlay"
PROGRESS_POLL_INTERVAL_SECONDS = 2.0
PROGRESS_READ_CHUNK_BYTES = 64 * 1024
SLOW_OVERLAY_STAGE_SECONDS = 1.0
SLOW_OVERLAY_TOTAL_SECONDS = 5.0
COMMAND_SAMPLE_LIMIT = 160

WorkspaceFingerprint = tuple[tuple[str, int, int, int, int], ...]


def command_sample(command: str) -> str:
    compact = " ".join(command.split())
    if len(compact) <= COMMAND_SAMPLE_LIMIT:
        return compact
    return compact[:COMMAND_SAMPLE_LIMIT] + "..."


def overlay_runtime_bundle_bytes() -> bytes:
    """Return a tar.gz containing the sandbox-side overlay runtime."""
    root = Path(__file__).parent
    runtime_dir = root / "runtime"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(root / "run.py", arcname="overlay_run.py")
        for path in sorted(runtime_dir.rglob("*.py")):
            rel = path.relative_to(runtime_dir).as_posix()
            tar.add(path, arcname=f"overlay_runtime/{rel}")
    return buffer.getvalue()


def workspace_fingerprint(workspace_root: str) -> WorkspaceFingerprint:
    root = Path(workspace_root)
    paths = (root, root / ".git" / "index", root / ".git" / "HEAD")
    rows: list[tuple[str, int, int, int, int]] = []
    for path in paths:
        try:
            st = path.stat()
        except OSError:
            rows.append((str(path), -1, -1, -1, -1))
            continue
        rows.append((str(path), st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size))
    return tuple(rows)
