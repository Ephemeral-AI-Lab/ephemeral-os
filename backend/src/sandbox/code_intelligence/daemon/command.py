"""Sandbox-local command dispatcher for code-intelligence mutations."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import traceback
from types import SimpleNamespace
from typing import Any


COMMAND_VERSION = "0.4.0"


def run_command(*, workspace_root: str, op: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one command and return a JSON-serializable response envelope."""
    try:
        result = _dispatch(workspace_root=workspace_root, op=op, args=args)
    except KeyError:
        return {
            "ok": False,
            "error": {
                "kind": "UnsupportedOp",
                "message": f"unknown op: {op}",
                "details": {},
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": {
                "kind": "InternalError",
                "message": str(exc),
                "details": {"traceback": traceback.format_exc()},
            },
        }
    return {"ok": True, "result": _to_dict(result)}


def main(argv: list[str] | None = None) -> int:
    """Small CLI wrapper for manual and bundle-import smoke checks."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return 0
    if len(args) < 2:
        print("usage: command <workspace_root> <op> [json_args]", file=sys.stderr)
        return 2
    workspace_root, op, *rest = args
    command_args = json.loads(rest[0]) if rest else {}
    if not isinstance(command_args, dict):
        print("json_args must decode to an object", file=sys.stderr)
        return 2
    response = run_command(workspace_root=workspace_root, op=op, args=command_args)
    print(json.dumps(response, separators=(",", ":")))
    return 0 if response.get("ok") else 1


def _dispatch(*, workspace_root: str, op: str, args: dict[str, Any]) -> Any:
    if op == "ping":
        return {"pong": True}
    if op == "version":
        return {"command": COMMAND_VERSION, "python": sys.version}

    svc, ledger = _build_service(workspace_root)
    try:
        if op == "svc_cmd":
            return _svc_cmd(svc, args)
        if op == "apply_edit":
            return svc.apply_edit(_edit_request_from_dict(args["request"]))
        if op == "commit_operation_against_base":
            changes = [_operation_change_from_dict(c) for c in args.get("changes", [])]
            return svc.commit_operation_against_base(
                changes,
                agent_id=str(args.get("agent_id", "")),
                edit_type=str(args["edit_type"]),
                description=str(args.get("description", "")),
            )
        if op == "commit_specs_many":
            return svc.commit_specs_many(list(args.get("requests", [])))
        if op == "write_file":
            specs = [_writespec_from_dict(s) for s in args.get("specs", [])]
            return svc.write_file(
                specs,
                agent_id=str(args.get("agent_id", "")),
                description=str(args.get("description", "")),
            )
        if op == "edit_file":
            specs = [_editspec_from_dict(s) for s in args.get("specs", [])]
            return svc.edit_file(
                specs,
                agent_id=str(args.get("agent_id", "")),
                description=str(args.get("description", "")),
            )
        if op == "undo_last_edit":
            return svc.undo_last_edit(str(args["file_path"]))
        raise KeyError(op)
    finally:
        try:
            svc.dispose()
        finally:
            ledger.close()


def _build_service(workspace_root: str) -> tuple[Any, Any]:
    from sandbox.code_intelligence.daemon.storage import LedgerStore, state_dir
    from sandbox.code_intelligence.service import CodeIntelligenceService

    ledger = LedgerStore(state_dir_path=state_dir(workspace_root))
    svc = CodeIntelligenceService(
        sandbox_id="local",
        workspace_root=workspace_root,
        sandbox=None,
        transport=None,
        edit_history=ledger,
        daemon_local=True,
    )
    return svc, ledger


def _svc_cmd(svc: Any, args: dict[str, Any]) -> Any:
    timeout_raw = args.get("timeout")
    timeout = int(timeout_raw) if timeout_raw is not None else None
    stdin_raw = args.get("stdin")
    result = asyncio.run(
        svc.cmd(
            None,
            str(args["command"]),
            timeout=timeout,
            description=str(args.get("description", "")),
            agent_id=str(args.get("agent_id", "")),
            run_id=str(args.get("run_id", "")),
            agent_run_id=str(args.get("agent_run_id", "")),
            task_id=str(args.get("task_id", "")),
            stdin=str(stdin_raw) if stdin_raw is not None else None,
            attribute_changes=bool(args.get("attribute_changes", True)),
        )
    )
    return _svc_cmd_result_to_dict(result)


def _writespec_from_dict(d: dict[str, Any]) -> Any:
    from sandbox.code_intelligence.core.types import WriteSpec

    return WriteSpec(
        file_path=str(d["file_path"]),
        content=str(d.get("content", "")),
        overwrite=bool(d.get("overwrite", True)),
    )


def _editspec_from_dict(d: dict[str, Any]) -> Any:
    from sandbox.code_intelligence.core.types import EditSpec

    return EditSpec(
        file_path=str(d["file_path"]),
        edits=tuple(d.get("edits", ())),
    )


def _operation_change_from_dict(d: dict[str, Any]) -> Any:
    from sandbox.code_intelligence.core.types import OperationChange

    return OperationChange(
        file_path=str(d["file_path"]),
        base_content=str(d.get("base_content", "")),
        base_hash=str(d.get("base_hash", "")),
        final_content=d.get("final_content"),
        base_existed=bool(d.get("base_existed", True)),
        strict_base=bool(d.get("strict_base", False)),
    )


def _edit_request_from_dict(d: dict[str, Any]) -> Any:
    from sandbox.code_intelligence.core.types import EditRequest

    return EditRequest(
        file_path=str(d["file_path"]),
        old_text=str(d.get("old_text", "")),
        new_text=str(d.get("new_text", "")),
        agent_id=str(d.get("agent_id", "")),
        description=str(d.get("description", "")),
    )


def _to_dict(obj: Any) -> Any:
    """Convert dataclasses recursively into JSON-safe objects."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, SimpleNamespace):
        return {str(k): _to_dict(v) for k, v in vars(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _to_dict(v) for k, v in obj.items()}
    return obj


_SVC_CMD_RESULT_DEFAULTS: dict[str, Any] = {
    "result": "",
    "exit_code": 1,
    "changed_paths": [],
    "ambient_changed_paths": [],
    "files_written": 0,
    "git_commit_status": None,
    "git_conflict_file": None,
    "git_conflict_reason": None,
    "gitinclude_changed_paths": [],
    "gitignore_direct_merged_paths": [],
    "gitignore_direct_merged_count": 0,
    "mixed_gitinclude_gitignore": False,
    "mixed_partial_apply": False,
    "warnings": [],
    "overlay_run_timings": {},
    "overlay_stage_timings": {},
}


def _svc_cmd_result_to_dict(result: Any) -> dict[str, Any]:
    return {
        field: _to_dict(getattr(result, field, default))
        for field, default in _SVC_CMD_RESULT_DEFAULTS.items()
    }


__all__ = ["COMMAND_VERSION", "main", "run_command"]
