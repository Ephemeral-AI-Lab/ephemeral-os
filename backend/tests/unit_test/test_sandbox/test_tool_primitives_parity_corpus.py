"""Replay the Phase 1 ephemeral tool-primitives parity corpus."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from sandbox.daemon import occ_backend, workspace_server
from sandbox.daemon.handler import edit, read, write
from sandbox.daemon.handler.glob import DEFAULT_GLOB_LIMIT, _glob_sync
from sandbox.daemon.handler.grep import _grep_sync
from sandbox.daemon.occ_backend import build_occ_backend
from sandbox.ephemeral_workspace.pipeline import _execute_shell, _payload_from_result
from sandbox.ephemeral_workspace.shell_contract import ShellProcessResult
from sandbox.layer_stack.workspace_base import build_workspace_base

_CORPUS = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "task_center_runner"
    / "tests"
    / "mock"
    / "sandbox"
    / "_fixtures"
    / "tool_primitives_parity_corpus.json"
)


@pytest.fixture(autouse=True)
def _clear_runtime_caches() -> None:
    occ_backend.clear_backend_cache()
    workspace_server.clear_layer_stack_server_caches_for_tests()
    try:
        yield
    finally:
        occ_backend.clear_backend_cache()
        workspace_server.clear_layer_stack_server_caches_for_tests()


def _load_cases() -> list[dict[str, str]]:
    payload = json.loads(_CORPUS.read_text(encoding="utf-8"))
    cases = payload["cases"]
    assert payload["schema_version"] == 1
    assert len(cases) >= 40
    assert {case["mode"] for case in cases} == {"ephemeral"}
    assert {case["verb"] for case in cases} == {
        "edit",
        "glob",
        "grep",
        "read",
        "shell",
        "write",
    }
    assert len({case["id"] for case in cases}) == len(cases)
    return cases


@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["id"])
def test_tool_primitives_parity_corpus_replays_byte_equivalent(
    case: dict[str, str],
    tmp_path: Path,
) -> None:
    case_id = case["id"]
    actual = asyncio.run(_run_case(case_id, tmp_path))
    expected = _expected(case_id)

    actual_bytes = _stable_json(_normalize(actual, tmp_path)).encode("utf-8")
    expected_bytes = _stable_json(expected).encode("utf-8")
    assert actual_bytes == expected_bytes


async def _run_case(case_id: str, tmp_path: Path) -> object:
    stack, workspace = _seed_workspace(tmp_path)
    base = {"layer_stack_root": stack}
    host = _host_path(tmp_path)

    try:
        if case_id == "read.in_workspace_utf8":
            return await read.read_file({**base, "path": "docs/readme.txt"})
        if case_id == "read.in_workspace_missing":
            return await read.read_file({**base, "path": "docs/missing.txt"})
        if case_id == "read.out_of_workspace_utf8":
            host.write_text("host text\n", encoding="utf-8")
            return await read.read_file({**base, "path": host.as_posix()})
        if case_id == "read.out_of_workspace_missing":
            return await read.read_file({**base, "path": (tmp_path / "missing-host.txt").as_posix()})
        if case_id == "read.large_out_of_workspace_rejected":
            large = tmp_path / "large-host.txt"
            with large.open("wb") as handle:
                handle.truncate(16 * 1024 * 1024 + 1)
            return await read.read_file({**base, "path": large.as_posix()})
        if case_id == "read.empty_path_rejected":
            return await read.read_file({**base, "path": ""})
        if case_id == "read.dotdot_escape_rejected":
            return await read.read_file({**base, "path": "../escape.txt"})

        if case_id == "write.in_workspace_create":
            return await write.write_file({**base, "path": "created.txt", "content": "created\n"})
        if case_id == "write.in_workspace_overwrite":
            return await write.write_file({**base, "path": "docs/readme.txt", "content": "new\n"})
        if case_id == "write.in_workspace_create_only":
            return await write.write_file(
                {**base, "path": "create-only.txt", "content": "x", "overwrite": False}
            )
        if case_id == "write.in_workspace_create_only_existing_conflict":
            return await write.write_file(
                {**base, "path": "docs/readme.txt", "content": "x", "overwrite": False}
            )
        if case_id == "write.out_of_workspace_create":
            return await write.write_file({**base, "path": host.as_posix(), "content": "host\n"})
        if case_id == "write.out_of_workspace_create_only_existing_conflict":
            host.write_text("old", encoding="utf-8")
            return await write.write_file(
                {**base, "path": host.as_posix(), "content": "x", "overwrite": False}
            )
        if case_id == "write.empty_path_rejected":
            return await write.write_file({**base, "path": "", "content": "x"})

        edit_args = {
            "edits": [{"old_text": "hello", "new_text": "hi", "expected_occurrences": 1}]
        }
        if case_id == "edit.in_workspace_single_replace":
            return await edit.edit_file({**base, "path": "docs/readme.txt", **edit_args})
        if case_id == "edit.in_workspace_multiple_replace":
            return await edit.edit_file(
                {
                    **base,
                    "path": "src/app.py",
                    "edits": [{"old_text": "print", "new_text": "echo", "expected_occurrences": 2}],
                }
            )
        if case_id == "edit.in_workspace_count_mismatch":
            return await edit.edit_file(
                {
                    **base,
                    "path": "docs/readme.txt",
                    "edits": [{"old_text": "missing", "new_text": "x", "expected_occurrences": 1}],
                }
            )
        if case_id == "edit.out_of_workspace_single_replace":
            host.write_text("hello host\n", encoding="utf-8")
            return await edit.edit_file({**base, "path": host.as_posix(), **edit_args})
        if case_id == "edit.out_of_workspace_missing_anchor_conflict":
            host.write_text("host\n", encoding="utf-8")
            return await edit.edit_file(
                {
                    **base,
                    "path": host.as_posix(),
                    "edits": [{"old_text": "missing", "new_text": "x", "expected_occurrences": 1}],
                }
            )
        if case_id == "edit.non_utf8_rejected":
            return await edit.edit_file({**base, "path": "binary.bin", **edit_args})
        if case_id == "edit.empty_anchor_rejected":
            return await edit.edit_file(
                {
                    **base,
                    "path": "docs/readme.txt",
                    "edits": [{"old_text": "", "new_text": "x", "expected_occurrences": 1}],
                }
            )

        if case_id == "grep.files_with_matches":
            return _grep_sync({**base, "pattern": "hello"})
        if case_id == "grep.count":
            return _grep_sync({**base, "pattern": "hello", "output_mode": "count"})
        if case_id == "grep.content_line_numbers":
            return _grep_sync(
                {**base, "pattern": "hello", "output_mode": "content", "line_numbers": True}
            )
        if case_id == "grep.case_insensitive":
            return _grep_sync({**base, "pattern": "mixed", "case_insensitive": True})
        if case_id == "grep.glob_filter":
            return _grep_sync({**base, "pattern": "hello", "glob_filter": "*.py"})
        if case_id == "grep.multiline":
            return _grep_sync({**base, "pattern": "hello.*again", "multiline": True})
        if case_id == "grep.out_of_workspace_path_rejected":
            return _grep_sync({**base, "pattern": "hello", "path": "/etc"})

        if case_id == "glob.basic_pattern":
            return _glob_sync({**base, "pattern": "*.py"})
        if case_id == "glob.subpath_filter":
            return _glob_sync({**base, "pattern": "*.py", "path": "src"})
        if case_id == "glob.excludes_vcs":
            return _glob_sync({**base, "pattern": "*"})
        if case_id == "glob.truncates_limit":
            return _glob_sync({**base, "pattern": "many/item_*.txt"})
        if case_id == "glob.missing_pattern_rejected":
            return _glob_sync({**base})
        if case_id == "glob.nested_pattern":
            return _glob_sync({**base, "pattern": "src/*.py"})
        if case_id == "glob.no_matches":
            return _glob_sync({**base, "pattern": "*.rs"})

        if case_id.startswith("shell."):
            return await _run_shell_case(case_id, base, stack)
    except Exception as exc:  # parity corpus records daemon exception shape too
        return {"raises": type(exc).__name__, "message": str(exc)}

    raise AssertionError(f"unhandled parity corpus case: {case_id}")


def _seed_workspace(tmp_path: Path) -> tuple[str, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    files: dict[str, str | bytes] = {
        "docs/readme.txt": "hello world\n",
        "src/app.py": "print('hello')\nprint('bye')\n",
        "src/other.py": "hello again\n",
        "src/mixed.txt": "MiXeD case\n",
        "notes.md": "plain notes\n",
        ".git/config": "secret\n",
        "many/marker.txt": "marker\n",
        "binary.bin": b"\xff\xfe\x00",
    }
    files.update({f"many/item_{index:03d}.txt": str(index) for index in range(105)})
    for rel, content in files.items():
        target = workspace.joinpath(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    stack = tmp_path / "layer-stack"
    build_workspace_base(workspace_root=workspace, layer_stack_root=stack)
    return stack.as_posix(), workspace


async def _run_shell_case(case_id: str, base: dict[str, str], stack: str) -> dict[str, object]:
    services = build_occ_backend(stack)
    args: dict[str, object] = {**base, "request_id": case_id, "command": "printf shell-ok"}
    if case_id == "shell.argv_command_success":
        args["command"] = ["printf", "argv-ok"]
    elif case_id == "shell.env_projection":
        args.update({"command": "printf env", "env": {"PARITY_TOKEN": "env-ok"}})
    elif case_id == "shell.cwd_projection":
        args.update({"command": "pwd", "cwd": "src"})
    elif case_id == "shell.nonzero_exit":
        args["command"] = "exit 7"
    elif case_id == "shell.stderr_projection":
        args["command"] = "stderr"
    elif case_id == "shell.timeout_projection":
        args.update({"command": "timeout", "timeout_seconds": 2.5})
    result = await _execute_shell(
        args,
        layer_stack=services.layer_stack,
        occ_client=services.occ_client,
        gitignore=services.gitignore,
        storage_root=Path(stack),
        command_runner=_fake_command_runner,
    )
    return _payload_from_result(result)


def _fake_command_runner(
    *,
    request: Any,
    run_dir: Path,
    timings: dict[str, float],
    **_: object,
) -> ShellProcessResult:
    stdout_ref = run_dir / "stdout"
    stderr_ref = run_dir / "stderr"
    stdout_ref.parent.mkdir(parents=True, exist_ok=True)
    stderr = ""
    exit_code = 0
    command = tuple(request.command)
    if command == ("bash", "-lc", "printf shell-ok"):
        stdout = "shell-ok"
    elif command == ("printf", "argv-ok"):
        stdout = "argv-ok"
    elif request.env.get("PARITY_TOKEN") == "env-ok":
        stdout = "env-ok"
    elif request.cwd == "src":
        stdout = "src"
    elif command == ("bash", "-lc", "exit 7"):
        stdout = ""
        exit_code = 7
    elif command == ("bash", "-lc", "stderr"):
        stdout = ""
        stderr = "stderr-ok"
    elif command == ("bash", "-lc", "timeout"):
        stdout = str(request.timeout_seconds)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError(f"unexpected shell command: {command!r}")
    stdout_ref.write_text(stdout, encoding="utf-8")
    stderr_ref.write_text(stderr, encoding="utf-8")
    timings["command_exec.run_command_s"] = 0.0
    return ShellProcessResult(
        exit_code=exit_code,
        stdout_ref=str(stdout_ref),
        stderr_ref=str(stderr_ref),
        mounted_workspace_root=request.workspace_root,
        mount_mode="private_namespace",
    )


def _expected(case_id: str) -> object:
    common_success = {"success": True, "timings": "__timings__"}
    expectations: dict[str, object] = {
        "read.in_workspace_utf8": {
            **common_success,
            "exists": True,
            "content": "hello world\n",
            "encoding": "utf-8",
        },
        "read.in_workspace_missing": {
            **common_success,
            "exists": False,
            "content": "",
            "encoding": "utf-8",
        },
        "read.out_of_workspace_utf8": {
            **common_success,
            "exists": True,
            "content": "host text\n",
            "encoding": "utf-8",
        },
        "read.out_of_workspace_missing": {
            **common_success,
            "exists": False,
            "content": "",
            "encoding": "utf-8",
        },
        "read.large_out_of_workspace_rejected": {
            "raises": "ValueError",
            "message": "file too large: 16777217 > 16777216 bytes",
        },
        "read.empty_path_rejected": {"raises": "ValueError", "message": "path is required"},
        "read.dotdot_escape_rejected": {
            "raises": "ValueError",
            "message": "path escapes workspace via '..': ../escape.txt",
        },
        "write.in_workspace_create": _write_ok("created.txt", "committed"),
        "write.in_workspace_overwrite": _write_ok("docs/readme.txt", "committed"),
        "write.in_workspace_create_only": _write_ok("create-only.txt", "committed"),
        "write.in_workspace_create_only_existing_conflict": {
            "success": False,
            "changed_paths": [],
            "status": "rejected",
            "conflict": {
                "reason": "create_only_existing",
                "conflict_file": "docs/readme.txt",
                "message": (
                    "create-only write rejected: path exists in validation snapshot "
                    "at docs/readme.txt"
                ),
            },
            "conflict_reason": "create_only_existing",
            "timings": "__timings__",
        },
        "write.out_of_workspace_create": _write_ok("<tmp>/host.txt", "ok"),
        "write.out_of_workspace_create_only_existing_conflict": {
            "success": False,
            "changed_paths": [],
            "status": "rejected",
            "conflict": {
                "reason": "create_only_existing",
                "conflict_file": "<tmp>/host.txt",
                "message": "create-only write rejected: path exists at <tmp>/host.txt",
            },
            "conflict_reason": "create_only_existing",
            "timings": "__timings__",
        },
        "write.empty_path_rejected": {"raises": "ValueError", "message": "path is required"},
        "edit.in_workspace_single_replace": _edit_ok("docs/readme.txt"),
        "edit.in_workspace_multiple_replace": _edit_ok("src/app.py"),
        "edit.in_workspace_count_mismatch": {
            "raises": "ValueError",
            "message": "anchor not found in docs/readme.txt: expected 1 occurrences of 'missing', found 0",
        },
        "edit.out_of_workspace_single_replace": _edit_ok("<tmp>/host.txt", status="ok"),
        "edit.out_of_workspace_missing_anchor_conflict": {
            "success": False,
            "changed_paths": ["<tmp>/host.txt"],
            "applied_edits": 0,
            "status": "aborted_overlap",
            "conflict": {
                "reason": "aborted_overlap",
                "conflict_file": "<tmp>/host.txt",
                "message": (
                    "anchor not found in <tmp>/host.txt: expected 1 occurrences "
                    "of 'missing', found 0"
                ),
            },
            "conflict_reason": (
                "anchor not found in <tmp>/host.txt: expected 1 occurrences "
                "of 'missing', found 0"
            ),
            "timings": "__timings__",
        },
        "edit.non_utf8_rejected": {
            "raises": "ValueError",
            "message": "file is not valid UTF-8 text: binary.bin",
        },
        "edit.empty_anchor_rejected": {
            "raises": "ValueError",
            "message": "edit anchor old_text must be non-empty for docs/readme.txt",
        },
        "grep.files_with_matches": _grep_expected(["docs/readme.txt", "src/app.py", "src/other.py"], 3),
        "grep.count": {
            **_grep_expected(["docs/readme.txt", "src/app.py", "src/other.py"], 3),
            "output_mode": "count",
            "content": "docs/readme.txt:1\nsrc/app.py:1\nsrc/other.py:1",
        },
        "grep.content_line_numbers": {
            **_grep_expected(["docs/readme.txt", "src/app.py", "src/other.py"], 3),
            "output_mode": "content",
            "content": (
                "docs/readme.txt:1:hello world\n"
                "src/app.py:1:print('hello')\n"
                "src/other.py:1:hello again\n"
            ),
            "num_lines": 3,
        },
        "grep.case_insensitive": _grep_expected(["src/mixed.txt"], 1),
        "grep.glob_filter": _grep_expected(["src/app.py", "src/other.py"], 2),
        "grep.multiline": _grep_expected(["src/other.py"], 1),
        "grep.out_of_workspace_path_rejected": {
            "raises": "ValueError",
            "message": "search path must be inside the workspace: /etc",
        },
        "glob.basic_pattern": _glob_expected(["src/app.py", "src/other.py"]),
        "glob.subpath_filter": _glob_expected(["src/app.py", "src/other.py"]),
        "glob.excludes_vcs": {
            **_glob_expected(_glob_all_without_vcs()[:DEFAULT_GLOB_LIMIT]),
            "truncated": True,
        },
        "glob.truncates_limit": {
            **_glob_expected([f"many/item_{index:03d}.txt" for index in range(DEFAULT_GLOB_LIMIT)]),
            "truncated": True,
        },
        "glob.missing_pattern_rejected": {
            "raises": "ValueError",
            "message": "pattern is required",
        },
        "glob.nested_pattern": _glob_expected(["src/app.py", "src/other.py"]),
        "glob.no_matches": _glob_expected([]),
        "shell.string_command_success": _shell_expected("shell-ok"),
        "shell.argv_command_success": _shell_expected("argv-ok"),
        "shell.env_projection": _shell_expected("env-ok"),
        "shell.cwd_projection": _shell_expected("src"),
        "shell.nonzero_exit": _shell_expected("", exit_code=7, success=False, status="error"),
        "shell.stderr_projection": _shell_expected("", stderr="stderr-ok"),
        "shell.timeout_projection": _shell_expected("2.5"),
    }
    return expectations[case_id]


def _write_ok(path: str, status: str) -> dict[str, object]:
    return {
        "success": True,
        "changed_paths": [path],
        "status": status,
        "conflict": None,
        "conflict_reason": None,
        "timings": "__timings__",
    }


def _edit_ok(path: str, *, status: str = "committed") -> dict[str, object]:
    return {**_write_ok(path, status), "applied_edits": 1}


def _grep_expected(filenames: list[str], matches: int) -> dict[str, object]:
    return {
        "success": True,
        "output_mode": "files_with_matches",
        "filenames": filenames,
        "content": "",
        "num_files": len(filenames),
        "num_lines": 0,
        "num_matches": matches,
        "applied_limit": 250,
        "applied_offset": 0,
        "truncated": False,
        "timings": "__timings__",
    }


def _glob_expected(filenames: list[str]) -> dict[str, object]:
    return {
        "success": True,
        "filenames": filenames,
        "num_files": len(filenames),
        "truncated": False,
        "timings": "__timings__",
    }


def _glob_all_without_vcs() -> list[str]:
    base = [
        "binary.bin",
        "docs/readme.txt",
        "many/marker.txt",
        "notes.md",
        "src/app.py",
        "src/mixed.txt",
        "src/other.py",
    ]
    base.extend(f"many/item_{index:03d}.txt" for index in range(105))
    return sorted(base)


def _shell_expected(
    stdout: str,
    *,
    stderr: str = "",
    exit_code: int = 0,
    success: bool = True,
    status: str = "ok",
) -> dict[str, object]:
    return {
        "success": success,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "changed_paths": [],
        "status": status,
        "conflict": None,
        "conflict_reason": None,
        "workspace_capture": {
            "snapshot_version": 1,
            "mount_mode": "private_namespace",
            "changes": [],
        },
        "warnings": [],
        "timings": "__timings__",
    }


def _host_path(tmp_path: Path) -> Path:
    return tmp_path / "host.txt"


def _normalize(value: object, tmp_path: Path) -> object:
    real_tmp = os.path.realpath(tmp_path.as_posix())
    raw_tmp = tmp_path.as_posix()
    if isinstance(value, dict):
        return {
            key: "__timings__" if key == "timings" else _normalize(item, tmp_path)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize(item, tmp_path) for item in value]
    if isinstance(value, str):
        return value.replace(real_tmp, "<tmp>").replace(raw_tmp, "<tmp>")
    return value


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
