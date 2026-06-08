#!/usr/bin/env python3
"""Serve the generated class inventory and refresh it on request."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parents[1]
MANIFEST = SCRIPT_DIR / "Cargo.toml"
OUTPUT_DIR = WORKSPACE / "docs" / "class-inventory" / "html"
CRATES_DIR = OUTPUT_DIR / "crates"


class InventoryHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUTPUT_DIR), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self) -> None:
        if self.path != "/__class_inventory_refresh":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            refresh_inventory()
        except Exception as exc:  # noqa: BLE001 - report command failure to the page.
            self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return
        self.write_json(HTTPStatus.OK, {"ok": True})

    def write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def refresh_inventory() -> None:
    if CRATES_DIR.exists():
        for path in CRATES_DIR.glob("*.html"):
            path.unlink()
    else:
        CRATES_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["cargo", "run", "--manifest-path", str(MANIFEST)],
        cwd=WORKSPACE,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stdout[-4000:] or "inventory refresh failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    if not shutil.which("cargo"):
        raise SystemExit("cargo is required for refresh")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.bind, args.port), InventoryHandler)
    print(f"serving {OUTPUT_DIR} at http://{args.bind}:{args.port}/index.html")
    print("refresh endpoint: POST /__class_inventory_refresh")
    server.serve_forever()


if __name__ == "__main__":
    main()
