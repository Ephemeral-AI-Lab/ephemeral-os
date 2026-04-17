"""Persistent Jedi worker process.

Runs as a long-lived subprocess alongside the :class:`LspClient` and
amortises Jedi's cold-import cost across every ``_python_*`` call in the
sandbox. Communicates with the parent over stdio using newline-delimited
JSON — one request per line, one response per line.

Protocol
--------

Request::

    {"id": "<opaque>", "op": "<name>", "args": {...}}

Response::

    {"id": "<same>", "ok": true|false, "result": <any>, "error": "<msg>|null"}

Supported ops: ``ping``, ``definitions``, ``references``, ``rename``,
``hover``, ``invalidate``, ``shutdown``.

Scope (per plan Phase 3, minimal delivery)
------------------------------------------

* Local-mode only — the caller owns spawning and lifecycle. Running the
  worker inside a Daytona sandbox over persistent stdio is gated on
  sandbox-SDK capability (plan line 158) and is left as follow-up.
* Single in-flight request at a time (Jedi's ``Project`` cache is not
  safe across concurrent ``Script`` calls rooted at the same project).
* No shadow mode, no generation-keyed cache, no N-request restart
  ceiling yet — those belong to a second P3 increment once this
  path has shadow-traffic coverage.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any


def _safe_import_jedi():
    try:
        import jedi  # type: ignore[import-untyped]
    except Exception as exc:
        return None, str(exc)
    return jedi, None


def _make_script(jedi_mod, project, path: str):
    return jedi_mod.Script(path=path, project=project)


def _op_ping(_jedi, _project, _args: dict[str, Any]) -> Any:
    return {"pong": True}


def _op_definitions(jedi_mod, project, args: dict[str, Any]) -> Any:
    path = str(args["path"])
    line = int(args["line"])
    column = int(args["column"])
    script = _make_script(jedi_mod, project, path)
    out = []
    for name in script.goto(line=line, column=column, follow_imports=True):
        out.append(
            {
                "name": getattr(name, "name", ""),
                "type": getattr(name, "type", ""),
                "module_path": str(getattr(name, "module_path", "") or ""),
                "line": int(getattr(name, "line", 0) or 0),
                "column": int(getattr(name, "column", 0) or 0),
                "description": getattr(name, "description", "") or "",
            }
        )
    return out


def _op_references(jedi_mod, project, args: dict[str, Any]) -> Any:
    path = str(args["path"])
    line = int(args["line"])
    column = int(args["column"])
    script = _make_script(jedi_mod, project, path)
    out = []
    for name in script.get_references(line=line, column=column, include_builtins=False):
        out.append(
            {
                "name": getattr(name, "name", ""),
                "module_path": str(getattr(name, "module_path", "") or ""),
                "line": int(getattr(name, "line", 0) or 0),
                "column": int(getattr(name, "column", 0) or 0),
            }
        )
    return out


def _op_rename(jedi_mod, project, args: dict[str, Any]) -> Any:
    path = str(args["path"])
    line = int(args["line"])
    column = int(args["column"])
    new_name = str(args["new_name"])
    script = _make_script(jedi_mod, project, path)
    refactoring = script.rename(line=line, column=column, new_name=new_name)
    out: dict[str, str] = {}
    for p, cf in refactoring.get_changed_files().items():
        try:
            out[str(p)] = cf.get_new_code()
        except Exception:  # pragma: no cover - per-file degradation
            continue
    return out


def _op_hover(jedi_mod, project, args: dict[str, Any]) -> Any:
    path = str(args["path"])
    line = int(args["line"])
    column = int(args["column"])
    script = _make_script(jedi_mod, project, path)
    names = script.help(line=line, column=column)
    if not names:
        return None
    n = names[0]
    sigs = script.get_signatures(line=line, column=column)
    sig = str(sigs[0]) if sigs else ""
    return {
        "name": getattr(n, "name", ""),
        "type": getattr(n, "type", ""),
        "docstring": (n.docstring() or "")[:500],
        "signature": sig,
    }


def _op_invalidate(_jedi, project, args: dict[str, Any]) -> Any:
    path = str(args.get("path", ""))
    if not path or project is None:
        return {"invalidated": False}
    try:
        state = getattr(project, "_inference_state", None)
        module_cache = getattr(state, "module_cache", None) if state else None
        if module_cache is not None and hasattr(module_cache, "delete"):
            module_cache.delete(path)
            return {"invalidated": True}
    except Exception:
        pass
    return {"invalidated": False}


_DISPATCH = {
    "ping": _op_ping,
    "definitions": _op_definitions,
    "references": _op_references,
    "rename": _op_rename,
    "hover": _op_hover,
    "invalidate": _op_invalidate,
}


def _respond(req_id: str, *, ok: bool, result: Any = None, error: str | None = None) -> None:
    payload = json.dumps({"id": req_id, "ok": ok, "result": result, "error": error})
    sys.stdout.write(payload + "\n")
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    workspace_root = argv[1] if len(argv) > 1 else ""
    jedi_mod, import_err = _safe_import_jedi()
    project = None
    if jedi_mod is not None and workspace_root:
        try:
            project = jedi_mod.Project(path=workspace_root)
        except Exception as exc:
            jedi_mod = None
            import_err = str(exc)

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as exc:
            _respond("", ok=False, error=f"json_decode: {exc}")
            continue
        req_id = str(req.get("id", ""))
        op = str(req.get("op", ""))
        args = req.get("args") or {}
        if op == "shutdown":
            _respond(req_id, ok=True, result={"bye": True})
            break
        if jedi_mod is None and op != "ping":
            _respond(req_id, ok=False, error=f"jedi_unavailable: {import_err}")
            continue
        handler = _DISPATCH.get(op)
        if handler is None:
            _respond(req_id, ok=False, error=f"unknown_op: {op}")
            continue
        try:
            result = handler(jedi_mod, project, args)
            _respond(req_id, ok=True, result=result)
        except Exception as exc:
            _respond(
                req_id,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                result={"trace": traceback.format_exc(limit=5)},
            )
    return 0


if __name__ == "__main__":  # pragma: no cover - executed as subprocess
    sys.exit(main(sys.argv))
