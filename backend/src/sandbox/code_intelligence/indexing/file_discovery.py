"""Local file discovery and content reads for the symbol index."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sandbox.api.transport import SandboxTransport
from sandbox.client.async_bridge import run_sync
from sandbox.code_intelligence.core.constants import SKIP_DIRECTORIES, SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)


def collect_local_files(root: Path, max_files: int) -> list[Path]:
    """Walk *root* collecting indexable files (bounded by *max_files*)."""
    files: list[Path] = []
    for path in root.rglob("*"):
        if len(files) >= max_files:
            break
        if any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        if path.is_file() and path.suffix in SUPPORTED_EXTENSIONS:
            files.append(path)
    files.sort()
    return files


def read_file_content(
    file_path: str,
    sandbox: Any = None,
    *,
    transport: SandboxTransport | None = None,
    sandbox_id: str = "",
) -> str | None:
    """Read a file from local disk, transport, or filesystem-only sandbox."""
    try:
        return Path(file_path).read_text(encoding="utf-8")
    except Exception:
        pass

    if transport is not None and sandbox_id:
        try:
            payload = run_sync(transport.read_bytes(sandbox_id, file_path))
        except FileNotFoundError:
            return None
        except Exception:
            logger.debug(
                "transport read_bytes failed for %s", file_path, exc_info=True,
            )
            return None
        if isinstance(payload, bytes):
            return payload.decode("utf-8")
        return str(payload)

    fs = getattr(sandbox, "fs", None) if sandbox is not None else None
    download = getattr(fs, "download_file", None)
    if not callable(download):
        return None
    try:
        raw = run_sync(download(file_path))
    except Exception:
        logger.debug("download_file failed for %s", file_path, exc_info=True)
        return None
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    return str(raw)
