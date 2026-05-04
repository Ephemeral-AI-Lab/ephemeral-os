"""Build the Python runtime bundle uploaded for overlay commands."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

_CAPTURE_RUNTIME_BUNDLE_CACHE: bytes | None = None


def capture_runtime_bundle_bytes() -> bytes:
    """Return a tar.gz containing the sandbox-side overlay runtime."""
    global _CAPTURE_RUNTIME_BUNDLE_CACHE
    if _CAPTURE_RUNTIME_BUNDLE_CACHE is not None:
        return _CAPTURE_RUNTIME_BUNDLE_CACHE

    sandbox_dir = Path(__file__).resolve().parents[2]
    runtime_dir = sandbox_dir / "runtime" / "overlay_capture_runtime"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(runtime_dir.rglob("*.py")):
            rel = path.relative_to(runtime_dir).as_posix()
            tar.add(path, arcname=f"overlay_runtime/{rel}")
    _CAPTURE_RUNTIME_BUNDLE_CACHE = buffer.getvalue()
    return _CAPTURE_RUNTIME_BUNDLE_CACHE


__all__ = ["capture_runtime_bundle_bytes"]
