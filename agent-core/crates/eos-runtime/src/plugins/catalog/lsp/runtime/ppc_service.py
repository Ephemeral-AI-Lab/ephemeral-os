#!/usr/bin/env python3
import ast
import json
import os
import re
import socket
from pathlib import Path


WORKSPACE_ROOT = Path(os.environ["EOS_PLUGIN_WORKSPACE_ROOT"]).resolve()
PACKAGE_ROOT = Path(os.environ["EOS_PLUGIN_PACKAGE_ROOT"])
DEPENDENCY_ROOT = Path(os.environ["EOS_PLUGIN_DEPENDENCY_ROOT"])
SOCKET_PATH = os.environ["EOS_PLUGIN_PPC_SOCKET"]


def pyright_command():
    node = DEPENDENCY_ROOT / "node22" / "bin" / "node"
    server = (
        DEPENDENCY_ROOT
        / "node22"
        / "lib"
        / "node_modules"
        / "pyright"
        / "langserver.index.js"
    )
    return [str(node), str(server), "--stdio"]


def decode_body(request):
    raw = request.get("args", {}).get("body", "{}")
    if isinstance(raw, str):
        return json.loads(raw or "{}")
    if isinstance(raw, dict):
        return raw
    return {}


def send(sock, invocation_id, body):
    frame = {
        "op": "reply",
        "invocation_id": invocation_id,
        "args": {
            "direction": "reply",
            "body": json.dumps(body, separators=(",", ":")),
        },
    }
    sock.sendall(json.dumps(frame, separators=(",", ":")).encode() + b"\n")


def workspace_path(raw_path):
    if not raw_path:
        return None
    path = Path(raw_path)
    candidate = path if path.is_absolute() else WORKSPACE_ROOT / path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(WORKSPACE_ROOT)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {raw_path}") from exc
    return resolved


def relative_path(path):
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def symbol_range(node):
    start_line = max(getattr(node, "lineno", 1) - 1, 0)
    start_char = max(getattr(node, "col_offset", 0), 0)
    end_line = max(getattr(node, "end_lineno", getattr(node, "lineno", 1)) - 1, start_line)
    end_char = max(getattr(node, "end_col_offset", start_char), start_char)
    return {
        "start": {"line": start_line, "character": start_char},
        "end": {"line": end_line, "character": end_char},
    }


def collect_symbols(path):
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [], [syntax_diagnostic(path, exc)]
    except FileNotFoundError:
        return [], []

    symbols = []
    for node in ast.walk(tree):
        name = getattr(node, "name", None)
        kind = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "function"
        elif isinstance(node, ast.ClassDef):
            kind = "class"
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbols.append(symbol_entry(path, target.id, "variable", target))
            continue
        if name and kind:
            symbols.append(symbol_entry(path, name, kind, node))
    symbols.sort(key=lambda item: (item["file_path"], item["range"]["start"]["line"], item["name"]))
    return symbols, []


def symbol_entry(path, name, kind, node):
    range_value = symbol_range(node)
    return {
        "name": name,
        "kind": kind,
        "file_path": relative_path(path),
        "range": range_value,
        "selection_range": range_value,
    }


def syntax_diagnostic(path, exc):
    line = max((exc.lineno or 1) - 1, 0)
    character = max((exc.offset or 1) - 1, 0)
    return {
        "file_path": relative_path(path),
        "severity": "error",
        "message": exc.msg,
        "range": {
            "start": {"line": line, "character": character},
            "end": {"line": line, "character": character + 1},
        },
    }


def python_files(body):
    path = workspace_path(body.get("file_path"))
    if path:
        return [path]
    files = []
    for candidate in sorted(WORKSPACE_ROOT.rglob("*.py")):
        if len(files) >= 200:
            break
        if ".git" in candidate.parts:
            continue
        files.append(candidate)
    return files


def query_symbols(body):
    query = str(body.get("query") or "").lower()
    symbols = []
    diagnostics = []
    for path in python_files(body):
        path_symbols, path_diagnostics = collect_symbols(path)
        diagnostics.extend(path_diagnostics)
        for symbol in path_symbols:
            if not query or query in symbol["name"].lower():
                symbols.append(symbol)
    return ok({"symbols": symbols, "diagnostics": diagnostics})


def diagnostics(body):
    results = []
    for path in python_files(body):
        _, path_diagnostics = collect_symbols(path)
        results.extend(path_diagnostics)
    return ok({"diagnostics": results})


def word_at(path, line, character):
    try:
        text_line = path.read_text(encoding="utf-8").splitlines()[line]
    except (FileNotFoundError, IndexError):
        return ""
    character = min(max(character, 0), len(text_line))
    left = character
    while left > 0 and re.match(r"[A-Za-z0-9_]", text_line[left - 1]):
        left -= 1
    right = character
    while right < len(text_line) and re.match(r"[A-Za-z0-9_]", text_line[right]):
        right += 1
    return text_line[left:right]


def definitions(body):
    path = workspace_path(body.get("file_path"))
    if not path:
        return ok({"definitions": []})
    name = word_at(path, int(body.get("line") or 0), int(body.get("character") or 0))
    symbols, path_diagnostics = collect_symbols(path)
    matches = [symbol for symbol in symbols if symbol["name"] == name]
    return ok({"definitions": matches, "diagnostics": path_diagnostics})


def hover(body):
    defs = definitions(body)
    definitions_value = defs.get("definitions") or []
    hover_value = None
    if definitions_value:
        symbol = definitions_value[0]
        hover_value = {
            "contents": f"{symbol['kind']} {symbol['name']}",
            "range": symbol["selection_range"],
        }
    return ok({"hover": hover_value, "definitions": definitions_value})


def references(body):
    path = workspace_path(body.get("file_path"))
    if not path:
        return ok({"references": []})
    name = word_at(path, int(body.get("line") or 0), int(body.get("character") or 0))
    if not name:
        return ok({"references": []})
    refs = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    for line_no, text in enumerate(lines):
        for match in pattern.finditer(text):
            refs.append(
                {
                    "file_path": relative_path(path),
                    "range": {
                        "start": {"line": line_no, "character": match.start()},
                        "end": {"line": line_no, "character": match.end()},
                    },
                }
            )
    return ok({"references": refs})


def ok(extra):
    result = {
        "success": True,
        "package_root": str(PACKAGE_ROOT),
        "dependency_root": str(DEPENDENCY_ROOT),
        "pyright_argv": pyright_command(),
        "node_exists": (DEPENDENCY_ROOT / "node22" / "bin" / "node").exists(),
        "langserver_exists": (
            DEPENDENCY_ROOT
            / "node22"
            / "lib"
            / "node_modules"
            / "pyright"
            / "langserver.index.js"
        ).exists(),
    }
    result.update(extra)
    return result


def unsupported(op):
    return ok(
        {
            "success": False,
            "error": f"{op} requires full Pyright edit support in the service runtime",
        }
    )


def handle_operation(op, body):
    if op == "plugin.lsp.query_symbols":
        return query_symbols(body)
    if op == "plugin.lsp.diagnostics":
        return diagnostics(body)
    if op == "plugin.lsp.find_definitions":
        return definitions(body)
    if op == "plugin.lsp.hover":
        return hover(body)
    if op == "plugin.lsp.find_references":
        return references(body)
    return unsupported(op)


def main():
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(SOCKET_PATH)
    buffer = b""
    manifest_key = "initial"
    refresh_events = 0
    while True:
        while b"\n" not in buffer:
            chunk = sock.recv(65536)
            if not chunk:
                return 0
            buffer += chunk
        line, buffer = buffer.split(b"\n", 1)
        request = json.loads(line.decode())
        body = decode_body(request)
        if request.get("op") == "daemon.workspace_snapshot_refresh":
            manifest_key = body.get("target_manifest_key") or body.get("manifest_key") or manifest_key
            refresh_events += 1
            send(
                sock,
                request["invocation_id"],
                {
                    "manifest_key": manifest_key,
                    "accepted": True,
                    "refresh_events": refresh_events,
                },
            )
            continue
        try:
            response = handle_operation(request.get("op", ""), body)
            response["manifest_key"] = manifest_key
            response["refresh_events"] = refresh_events
        except Exception as exc:
            response = {
                "success": False,
                "error": str(exc),
                "manifest_key": manifest_key,
                "refresh_events": refresh_events,
            }
        send(sock, request["invocation_id"], response)


if __name__ == "__main__":
    raise SystemExit(main())
