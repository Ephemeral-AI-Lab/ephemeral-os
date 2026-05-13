"""Entrypoint for ``python -m sandbox.runtime.daemon`` inside the sandbox."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import logging
import os
import sys
from pathlib import Path

from sandbox.runtime.daemon.rpc.server import DEFAULT_PID_PATH, DEFAULT_SOCKET_PATH, serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sandbox.runtime.daemon")
    parser.add_argument("--socket", default=DEFAULT_SOCKET_PATH)
    parser.add_argument("--pid-file", default=DEFAULT_PID_PATH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pid_lock_fd: int | None = None
    try:
        pid_lock_fd = _acquire_pid_lock(Path(args.pid_file))
        asyncio.run(serve(Path(args.socket), Path(args.pid_file)))
    except KeyboardInterrupt:
        return 0
    finally:
        if pid_lock_fd is not None:
            with contextlib.suppress(OSError):
                os.close(pid_lock_fd)
    return 0


def _acquire_pid_lock(pid_path: Path) -> int:
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(pid_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise RuntimeError(f"sandbox runtime daemon already running: {pid_path}") from exc
    return fd


if __name__ == "__main__":  # pragma: no cover - exercised in-sandbox
    raise SystemExit(main(sys.argv[1:]))


__all__ = ["main"]
