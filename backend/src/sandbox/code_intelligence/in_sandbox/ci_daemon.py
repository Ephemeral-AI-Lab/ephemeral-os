"""Asyncio Unix-socket daemon for sandbox-local code intelligence RPC."""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import signal
import sys
import time
import traceback
from collections.abc import Awaitable, Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from sandbox.code_intelligence.in_sandbox import ci_storage
from sandbox.code_intelligence.in_sandbox.ci_protocol import (
    CI_PROTOCOL_VERSION,
    FrameError,
    SchemaError,
    encode_frame,
    parse_request,
    read_frame,
)

__all__ = [
    "DAEMON_VERSION",
    "DISPATCH",
    "DaemonAlreadyRunning",
    "handle_client",
    "handle_ping",
    "handle_shutdown",
    "handle_version",
    "run_daemon",
]

logger = logging.getLogger(__name__)

DAEMON_VERSION = "0.1.0"
_STARTED_AT = time.time()
_SHUTDOWN_GRACE_S = 5.0


class DaemonAlreadyRunning(Exception):
    """Raised when a live daemon already owns the state directory."""

    exit_code = 11


async def handle_ping(args: dict[str, Any]) -> dict[str, Any]:
    """Return daemon health."""
    del args
    return {"pong": True, "uptime_s": time.time() - _STARTED_AT}


async def handle_shutdown(args: dict[str, Any]) -> dict[str, bool]:
    """Ask the daemon process to terminate after this response drains."""
    del args
    loop = asyncio.get_running_loop()
    loop.call_later(0.05, lambda: os.kill(os.getpid(), signal.SIGTERM))
    return {"shutting_down": True}


async def handle_version(args: dict[str, Any]) -> dict[str, Any]:
    """Return protocol and runtime version details."""
    del args
    return {
        "protocol": CI_PROTOCOL_VERSION,
        "daemon": DAEMON_VERSION,
        "python": sys.version,
    }


DISPATCH: dict[str, Callable[[dict[str, Any]], Awaitable[Any]]] = {
    "ping": handle_ping,
    "shutdown": handle_shutdown,
    "version": handle_version,
}


async def _dispatch_request(body: dict[str, Any]) -> dict[str, Any]:
    """Run one validated RPC request and return a response envelope."""
    try:
        request = parse_request(body)
    except SchemaError as exc:
        return {
            "v": CI_PROTOCOL_VERSION,
            "id": str(body.get("id") or ""),
            "ok": False,
            "error": {"kind": "InvalidSchema", "message": str(exc), "details": {}},
        }

    handler = DISPATCH.get(request.op)
    if handler is None:
        return {
            "v": CI_PROTOCOL_VERSION,
            "id": request.id,
            "ok": False,
            "error": {
                "kind": "UnsupportedOp",
                "message": f"unknown op: {request.op}",
                "details": {},
            },
        }

    try:
        result = await handler(request.args)
    except Exception as exc:  # pragma: no cover - defensive envelope path
        logger.exception("ci daemon handler failed for op=%s", request.op)
        return {
            "v": CI_PROTOCOL_VERSION,
            "id": request.id,
            "ok": False,
            "error": {
                "kind": "InternalError",
                "message": str(exc),
                "details": {"traceback": traceback.format_exc()},
            },
        }
    return {"v": CI_PROTOCOL_VERSION, "id": request.id, "ok": True, "result": result}


async def handle_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    """Serve requests on one Unix-socket connection."""
    peer = writer.get_extra_info("peername")
    try:
        while not reader.at_eof():
            try:
                body = await read_frame(reader)
            except (FrameError, SchemaError, asyncio.IncompleteReadError):
                logger.debug("closing malformed ci daemon connection from %r", peer)
                break
            response = await _dispatch_request(body)
            writer.write(encode_frame(response))
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _prepare_state_paths(workspace_root: str) -> tuple[Path, Path, Path, Path]:
    state = ci_storage.state_dir(workspace_root)
    socket_path = state / "daemon.sock"
    pid_path = state / "daemon.pid"
    log_path = state / "daemon.log"

    pid = _read_pid(pid_path) if pid_path.exists() else None
    if pid is not None and _pid_is_alive(pid):
        raise DaemonAlreadyRunning(f"daemon already running with pid {pid}")

    for path in (pid_path, socket_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise
    return state, socket_path, pid_path, log_path


def _configure_file_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    wanted = str(log_path)
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler) and handler.baseFilename == wanted:
            return
    handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


async def run_daemon(workspace_root: str) -> None:
    """Start the CI daemon and return after graceful shutdown."""
    state, socket_path, pid_path, log_path = _prepare_state_paths(workspace_root)
    _configure_file_logging(log_path)
    pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")

    shutdown_event = asyncio.Event()
    active_tasks: set[asyncio.Task[None]] = set()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []

    async def tracked_client(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            active_tasks.add(task)
        try:
            await handle_client(reader, writer)
        finally:
            if task is not None:
                active_tasks.discard(task)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
            installed_signals.append(sig)
        except (NotImplementedError, RuntimeError):
            logger.debug("signal handler unavailable for %s", sig, exc_info=True)

    server: asyncio.AbstractServer | None = None
    try:
        server = await asyncio.start_unix_server(
            tracked_client,
            path=str(socket_path),
        )
        os.chmod(socket_path, 0o600)
        logger.info("ci daemon listening on %s (state=%s)", socket_path, state)

        serve_task = asyncio.create_task(server.serve_forever())
        wait_task = asyncio.create_task(shutdown_event.wait())
        done, pending = await asyncio.wait(
            {serve_task, wait_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if serve_task in done and serve_task.exception() is not None:
            raise serve_task.exception()  # type: ignore[misc]
        for task in pending:
            task.cancel()
    finally:
        if server is not None:
            server.close()
            await server.wait_closed()
        if active_tasks:
            done, pending = await asyncio.wait(active_tasks, timeout=_SHUTDOWN_GRACE_S)
            del done
            for task in pending:
                task.cancel()
        for sig in installed_signals:
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError):
                pass
        for path in (socket_path, pid_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        logger.info("ci daemon stopped")
