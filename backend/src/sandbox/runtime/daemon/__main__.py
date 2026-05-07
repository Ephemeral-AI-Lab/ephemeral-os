"""Entrypoint for ``python -m sandbox.runtime.daemon`` inside the sandbox."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sandbox.runtime.daemon.rpc.server import DEFAULT_PID_PATH, DEFAULT_SOCKET_PATH, serve


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


__all__ = ["main"]
