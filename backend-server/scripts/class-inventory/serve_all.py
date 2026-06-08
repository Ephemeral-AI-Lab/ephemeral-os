#!/usr/bin/env python3
"""Serve all generated class inventories from one local docs server."""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import shutil
import subprocess
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]


@dataclass(frozen=True)
class InventoryMount:
    name: str
    label: str
    workspace: Path
    output_dir: Path
    manifest: Path

    @property
    def prefix(self) -> str:
        return f"/{self.name}"


MOUNTS = {
    mount.name: mount
    for mount in (
        InventoryMount(
            name="agent-core",
            label="agent-core",
            workspace=REPO_ROOT / "agent-core",
            output_dir=REPO_ROOT / "agent-core" / "docs" / "class-inventory" / "html",
            manifest=REPO_ROOT / "agent-core" / "scripts" / "class-inventory" / "Cargo.toml",
        ),
        InventoryMount(
            name="sandbox",
            label="sandbox",
            workspace=REPO_ROOT / "sandbox",
            output_dir=REPO_ROOT / "sandbox" / "docs" / "class_inventory" / "html",
            manifest=REPO_ROOT / "sandbox" / "scripts" / "class-inventory" / "Cargo.toml",
        ),
        InventoryMount(
            name="backend-server",
            label="backend-server",
            workspace=REPO_ROOT / "backend-server",
            output_dir=REPO_ROOT / "backend-server" / "docs" / "class_inventory" / "html",
            manifest=REPO_ROOT
            / "backend-server"
            / "scripts"
            / "class-inventory"
            / "Cargo.toml",
        ),
    )
}


class AggregateInventoryHandler(BaseHTTPRequestHandler):
    server_version = "ClassInventoryServer/1.0"

    def do_GET(self) -> None:
        self.handle_request(send_body=True)

    def do_HEAD(self) -> None:
        self.handle_request(send_body=False)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/__refresh/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        target = path.removeprefix("/__refresh/")
        try:
            refreshed = refresh(target)
        except Exception as exc:  # noqa: BLE001 - surface command failures to the caller.
            self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return
        self.write_json(HTTPStatus.OK, {"ok": True, "refreshed": refreshed})

    def handle_request(self, *, send_body: bool) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self.write_html(HTTPStatus.OK, index_page(), send_body=send_body)
            return
        if path == "/favicon.ico":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        mount, subpath = resolve_mount(path)
        if mount is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if subpath == "":
            self.redirect(f"{mount.prefix}/index.html")
            return

        try:
            file_path = resolve_static_path(mount, subpath)
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if file_path.is_dir():
            self.redirect(f"{path.rstrip('/')}/index.html")
            return
        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        self.write_file(file_path, send_body=send_body)

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def write_file(self, file_path: Path, *, send_body: bool) -> None:
        data = file_path.read_bytes() if send_body else b""
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def write_html(self, status: HTTPStatus, body: str, *, send_body: bool) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def write_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def resolve_mount(path: str) -> tuple[InventoryMount | None, str]:
    for mount in MOUNTS.values():
        if path == mount.prefix:
            return mount, ""
        prefix = f"{mount.prefix}/"
        if path.startswith(prefix):
            return mount, path.removeprefix(prefix)
    return None, ""


def resolve_static_path(mount: InventoryMount, subpath: str) -> Path:
    root = mount.output_dir.resolve()
    candidate = (root / unquote(subpath)).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("static path escapes inventory root")
    return candidate


def refresh(target: str) -> list[str]:
    if target == "all":
        mounts = list(MOUNTS.values())
    elif target in MOUNTS:
        mounts = [MOUNTS[target]]
    else:
        raise ValueError(f"unknown inventory target: {target}")

    refreshed = []
    for mount in mounts:
        refresh_mount(mount)
        refreshed.append(mount.name)
    return refreshed


def refresh_mount(mount: InventoryMount) -> None:
    if not mount.manifest.is_file():
        raise FileNotFoundError(f"missing inventory manifest: {mount.manifest}")
    result = subprocess.run(
        ["cargo", "run", "--manifest-path", str(mount.manifest)],
        cwd=mount.workspace,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stdout[-4000:] or f"{mount.name} inventory refresh failed")


def index_page() -> str:
    rows = []
    for mount in MOUNTS.values():
        status = "ready" if (mount.output_dir / "index.html").is_file() else "missing"
        rows.append(
            f"""
            <tr>
              <td><a href="{mount.prefix}/index.html">{html.escape(mount.label)}</a></td>
              <td><code>{html.escape(str(mount.output_dir.relative_to(REPO_ROOT)))}</code></td>
              <td>{status}</td>
              <td><form method="post" action="/__refresh/{mount.name}">
                <button type="submit">Refresh</button>
              </form></td>
            </tr>
            """
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Class Inventories</title>
  <style>
    body {{
      margin: 0;
      color: #1f2933;
      background: #f7f8fa;
      font: 15px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      max-width: 1040px;
      margin: 0 auto;
      padding: 36px 24px;
    }}
    h1 {{
      margin: 0 0 20px;
      font-size: 28px;
      font-weight: 650;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      border: 1px solid #d7dde5;
    }}
    th,
    td {{
      padding: 12px 14px;
      text-align: left;
      border-bottom: 1px solid #e4e8ee;
      vertical-align: middle;
    }}
    th {{
      background: #eef1f5;
      font-size: 13px;
      font-weight: 650;
      text-transform: uppercase;
    }}
    a {{
      color: #0f5f8c;
      font-weight: 650;
      text-decoration: none;
    }}
    a:hover {{
      text-decoration: underline;
    }}
    code {{
      font-size: 13px;
      white-space: nowrap;
    }}
    button {{
      min-width: 78px;
      padding: 6px 10px;
      border: 1px solid #aeb8c5;
      border-radius: 6px;
      background: #ffffff;
      color: #1f2933;
      font: inherit;
      cursor: pointer;
    }}
    button:hover {{
      background: #eef6fb;
      border-color: #7ea4bd;
    }}
    .actions {{
      margin-top: 16px;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Class Inventories</h1>
    <table>
      <thead>
        <tr>
          <th>Workspace</th>
          <th>HTML root</th>
          <th>Status</th>
          <th>Refresh</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    <form class="actions" method="post" action="/__refresh/all">
      <button type="submit">Refresh all</button>
    </form>
  </main>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()

    if not shutil.which("cargo"):
        raise SystemExit("cargo is required for refresh")
    server = ThreadingHTTPServer((args.bind, args.port), AggregateInventoryHandler)
    print(f"serving class inventories at http://{args.bind}:{args.port}/")
    for mount in MOUNTS.values():
        print(f"  {mount.prefix}/ -> {mount.output_dir}")
    print("refresh endpoints: POST /__refresh/{agent-core|sandbox|backend-server|all}")
    server.serve_forever()


if __name__ == "__main__":
    main()
