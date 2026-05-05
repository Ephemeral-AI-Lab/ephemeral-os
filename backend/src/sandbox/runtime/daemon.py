"""Resident in-sandbox runtime daemon.

Replaces the per-call ``python -m sandbox.runtime.server <json>`` boot path
with a single long-lived process that listens on AF_UNIX. Each host call
still goes through ``provider.exec(...)`` (Daytona constraint), but the
per-call command is now a thin client that connects to the socket, sends
one newline-terminated JSON envelope, and prints the JSON response.

Wire format (newline-delimited JSON):

  request:  {"op": "...", "args": {...}}\\n
  response: {"success": true, ...}\\n

The daemon imports ``sandbox.runtime.server`` so the ``OP_TABLE`` is
populated by the standard peer bootstrap, then dispatches via
:func:`server.dispatch_envelope_async`. State that is expensive to
construct — ``LayerStackManager``, ``OccService``,
``LayerStackGitignoreOracle`` — is cached across calls inside the
``api_handlers`` module's ``_SERVICE_CACHE`` and thus amortizes naturally
because the daemon is one Python process.

Lifecycle:

* The daemon is launched once per sandbox via
  ``sandbox.control.daemon.command`` issuing a ``nohup`` invocation
  through the provider adapter's ``exec``.
* It writes its PID to ``<bundle>/runtime.pid`` and binds AF_UNIX to
  ``<bundle>/runtime.sock``.
* On startup it sets ``EPHEMERALOS_RUNTIME_DAEMON=1`` so handlers know
  the in-process ``asyncio.Lock`` serializes commits and the cross-process
  flock fence can be skipped.
* Restart safety: stale PID and stale socket are cleaned up before bind.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

from sandbox.runtime import server as runtime_server

logger = logging.getLogger("sandbox.runtime.daemon")

DEFAULT_SOCKET_PATH = "/tmp/eos-sandbox-runtime/runtime.sock"
DEFAULT_PID_PATH = "/tmp/eos-sandbox-runtime/runtime.pid"


async def _handle_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    boot_t0 = time.perf_counter()
    try:
        raw = await reader.readline()
        if not raw:
            return
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            response = {
                "success": False,
                "warnings": [],
                "timings": {},
                "error": {
                    "kind": "bad_json",
                    "message": "runtime daemon request must be valid JSON",
                    "details": {"message": str(exc)},
                },
            }
        else:
            if not isinstance(envelope, dict):
                response = {
                    "success": False,
                    "warnings": [],
                    "timings": {},
                    "error": {
                        "kind": "invalid_envelope",
                        "message": "runtime envelope must be a JSON object",
                        "details": {},
                    },
                }
            else:
                response = await runtime_server.dispatch_envelope_async(
                    envelope, boot_t0=boot_t0
                )
        payload = json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n"
        writer.write(payload)
        await writer.drain()
    except Exception:  # pragma: no cover - logged for diagnostics
        logger.exception("runtime daemon connection failed")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # pragma: no cover
            pass


def _prepare_socket_path(socket_path: Path) -> None:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists() or socket_path.is_symlink():
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass


def _write_pid(pid_path: Path) -> None:
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")


def _remove_pid(pid_path: Path) -> None:
    try:
        pid_path.unlink()
    except FileNotFoundError:
        pass


async def serve(socket_path: Path, pid_path: Path) -> None:
    os.environ["EPHEMERALOS_RUNTIME_DAEMON"] = "1"
    _prepare_socket_path(socket_path)
    server = await asyncio.start_unix_server(_handle_connection, path=str(socket_path))
    try:
        os.chmod(socket_path, 0o600)
    except OSError:
        pass
    _write_pid(pid_path)
    logger.info("runtime daemon listening on %s pid=%s", socket_path, os.getpid())

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _signal_stop() -> None:
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_stop)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - non-unix
            pass

    try:
        async with server:
            serve_task = asyncio.create_task(server.serve_forever())
            stop_task = asyncio.create_task(stop.wait())
            done, pending = await asyncio.wait(
                {serve_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc is not None and not isinstance(exc, asyncio.CancelledError):
                    raise exc
    finally:
        _remove_pid(pid_path)
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sandbox.runtime.daemon")
    parser.add_argument("--socket", default=DEFAULT_SOCKET_PATH)
    parser.add_argument("--pid-file", default=DEFAULT_PID_PATH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(serve(Path(args.socket), Path(args.pid_file)))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised in-sandbox
    raise SystemExit(main(sys.argv[1:]))


__all__ = [
    "DEFAULT_PID_PATH",
    "DEFAULT_SOCKET_PATH",
    "main",
    "serve",
]
