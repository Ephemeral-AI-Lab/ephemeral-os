"""Entrypoint for ``python -m sandbox.daemon`` inside the sandbox."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import logging
import os
import resource
import sys
from pathlib import Path

from sandbox.daemon.rpc.server import DEFAULT_PID_PATH, DEFAULT_SOCKET_PATH, serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sandbox.daemon")
    parser.add_argument("--socket", default=DEFAULT_SOCKET_PATH)
    parser.add_argument("--pid-file", default=DEFAULT_PID_PATH)
    parser.add_argument("--tcp-host", default=os.environ.get("EOS_DAEMON_TCP_HOST"))
    parser.add_argument(
        "--tcp-port",
        type=int,
        default=_optional_int(os.environ.get("EOS_DAEMON_TCP_PORT")),
    )
    parser.add_argument(
        "--auth-token",
        default=os.environ.get("EOS_DAEMON_AUTH_TOKEN"),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _bump_nofile()
    _log_mount_api_capability()
    pid_lock_fd: int | None = None
    try:
        pid_lock_fd = _acquire_pid_lock(Path(args.pid_file))
        asyncio.run(
            serve(
                Path(args.socket),
                Path(args.pid_file),
                tcp_host=args.tcp_host,
                tcp_port=args.tcp_port,
                auth_token=args.auth_token,
            )
        )
    except KeyboardInterrupt:
        return 0
    finally:
        if pid_lock_fd is not None:
            os.close(pid_lock_fd)
    return 0


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value)


def _acquire_pid_lock(pid_path: Path) -> int:
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(pid_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise RuntimeError(f"sandbox runtime daemon already running: {pid_path}") from exc
    return fd


def _bump_nofile(target: int = 8192) -> None:
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft < target:
            new_soft = min(target, hard) if hard != resource.RLIM_INFINITY else target
            resource.setrlimit(resource.RLIMIT_NOFILE, (new_soft, hard))
            logging.getLogger(__name__).info(
                "daemon.rlimit_nofile soft=%d->%d hard=%d", soft, new_soft, hard
            )
    except (ValueError, OSError):
        logging.getLogger(__name__).warning(
            "daemon.rlimit_nofile bump failed", exc_info=True
        )


def _log_mount_api_capability() -> None:
    from sandbox.execution.overlay.capability import new_mount_api_supported
    supported = new_mount_api_supported()
    logging.getLogger(__name__).info(
        "daemon.overlay_mount_api supported=%s", supported
    )


if __name__ == "__main__":  # pragma: no cover - exercised in-sandbox
    raise SystemExit(main(sys.argv[1:]))


__all__ = ["main"]
