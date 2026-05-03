"""Contract tests for :class:`sandbox.daytona.transport.DaytonaTransport`.

Uses an in-memory fake sandbox object to verify each method calls the
expected SDK surface and parses results correctly. No live Daytona
connection is required.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from sandbox.api.errors import SandboxTransportError
from sandbox.api.models import CheckedWriteSpec
from sandbox.api.bash import _EXIT_MARKER
from sandbox.daytona.transport import DaytonaTransport


def _exec_response(stdout: str, exit_code: int = 0) -> SimpleNamespace:
    """Build a fake Daytona ``process.exec`` response.

    The transport pulls the synthetic exit code marker out of the
    ``result`` payload, so the marker must be appended after the user
    stdout for the parser to recover it.
    """
    return SimpleNamespace(
        result=f"{stdout}\n{_EXIT_MARKER}{exit_code}",
        exit_code=exit_code,
    )


@pytest.fixture
def fake_sandbox() -> SimpleNamespace:
    process = SimpleNamespace(exec=AsyncMock())
    fs = SimpleNamespace(
        download_file=AsyncMock(),
        upload_file=AsyncMock(),
    )
    return SimpleNamespace(process=process, fs=fs)


@pytest.fixture
def transport(fake_sandbox: SimpleNamespace) -> DaytonaTransport:
    async def _resolver(_sid: str) -> Any:
        return fake_sandbox

    return DaytonaTransport(sandbox_resolver=_resolver)


# -- exec --------------------------------------------------------------------


async def test_exec_extracts_exit_code(
    transport: DaytonaTransport, fake_sandbox: SimpleNamespace,
) -> None:
    fake_sandbox.process.exec.return_value = _exec_response("hello", exit_code=0)

    result = await transport.exec("sb-1", "echo hello")

    assert result.exit_code == 0
    assert result.stdout == "hello"
    fake_sandbox.process.exec.assert_awaited_once()


async def test_exec_passes_through_non_zero_exit(
    transport: DaytonaTransport, fake_sandbox: SimpleNamespace,
) -> None:
    fake_sandbox.process.exec.return_value = _exec_response("oops", exit_code=2)

    result = await transport.exec("sb-1", "false")

    assert result.exit_code == 2
    assert result.stdout == "oops"


async def test_exec_raises_on_sdk_failure(
    transport: DaytonaTransport, fake_sandbox: SimpleNamespace,
) -> None:
    fake_sandbox.process.exec.side_effect = RuntimeError("network down")

    with pytest.raises(SandboxTransportError, match="daytona exec failed"):
        await transport.exec("sb-1", "echo hi")


# -- read_bytes / write_bytes ------------------------------------------------


async def test_read_bytes_returns_payload(
    transport: DaytonaTransport, fake_sandbox: SimpleNamespace,
) -> None:
    fake_sandbox.fs.download_file.return_value = b"hello"

    result = await transport.read_bytes("sb-1", "/file")

    assert result == b"hello"
    fake_sandbox.fs.download_file.assert_awaited_once_with("/file")


async def test_read_bytes_propagates_file_not_found(
    transport: DaytonaTransport, fake_sandbox: SimpleNamespace,
) -> None:
    fake_sandbox.fs.download_file.side_effect = FileNotFoundError("/missing")

    with pytest.raises(FileNotFoundError):
        await transport.read_bytes("sb-1", "/missing")


async def test_write_bytes_calls_upload(
    transport: DaytonaTransport, fake_sandbox: SimpleNamespace,
) -> None:
    fake_sandbox.fs.upload_file.return_value = None

    await transport.write_bytes("sb-1", "/file", b"data")

    fake_sandbox.fs.upload_file.assert_awaited_once_with(b"data", "/file")


# -- apply_diff_batch_checked ------------------------------------------------


async def test_apply_diff_batch_checked_empty_specs_short_circuits(
    transport: DaytonaTransport, fake_sandbox: SimpleNamespace,
) -> None:
    result = await transport.apply_diff_batch_checked("sb-1", ())

    assert result.success is True
    assert result.written_paths == ()
    fake_sandbox.process.exec.assert_not_called()


async def test_apply_diff_batch_checked_success_parses_written_paths(
    transport: DaytonaTransport, fake_sandbox: SimpleNamespace,
) -> None:
    fake_sandbox.process.exec.return_value = _exec_response(
        json.dumps({"ok": True, "written_paths": ["/a", "/b"]}),
        exit_code=0,
    )
    specs = (
        CheckedWriteSpec(path="/a", content=b"A", expected_sha=None),
        CheckedWriteSpec(path="/b", content=b"B", expected_sha="deadbeef"),
    )

    result = await transport.apply_diff_batch_checked("sb-1", specs)

    assert result.success is True
    assert result.written_paths == ("/a", "/b")


async def test_apply_diff_batch_checked_conflict_returns_failure(
    transport: DaytonaTransport, fake_sandbox: SimpleNamespace,
) -> None:
    fake_sandbox.process.exec.return_value = _exec_response(
        json.dumps({
            "ok": False,
            "reason": "base_mismatch",
            "path": "/a",
            "message": "file content changed",
        }),
        exit_code=0,
    )
    specs = (CheckedWriteSpec(path="/a", content=b"A", expected_sha="oldsha"),)

    result = await transport.apply_diff_batch_checked("sb-1", specs)

    assert result.success is False
    assert result.conflict_paths == ("/a",)
    assert result.conflict_reason == "base_mismatch"


async def test_apply_diff_batch_checked_handles_delete_specs(
    transport: DaytonaTransport, fake_sandbox: SimpleNamespace,
) -> None:
    """A spec with ``content=None`` is forwarded as a delete to the apply script."""
    import base64 as _b64
    import re as _re

    captured_commands: list[str] = []

    async def _record(command: str, **_kwargs: Any) -> SimpleNamespace:
        captured_commands.append(command)
        return _exec_response(
            json.dumps({"ok": True, "written_paths": ["/dead"]}),
            exit_code=0,
        )

    fake_sandbox.process.exec.side_effect = _record

    specs = (
        CheckedWriteSpec(path="/dead", content=None, expected_sha="oldsha"),
    )
    result = await transport.apply_diff_batch_checked("sb-1", specs)

    assert result.success is True
    assert result.written_paths == ("/dead",)
    # Decode the inlined payload from the captured bash command and verify
    # the spec marshals as ``content_b64: null`` (delete sentinel).
    inline_command = captured_commands[0]
    match = _re.search(r'base64\.b64decode\("([A-Za-z0-9+/=]+)"\)', inline_command)
    assert match, f"could not extract inline payload from: {inline_command[:200]}"
    decoded = json.loads(_b64.b64decode(match.group(1)).decode("utf-8"))
    assert decoded == [
        {"path": "/dead", "expected_sha": "oldsha", "content_b64": None},
    ]


async def test_apply_diff_batch_checked_stages_oversize_payload(
    transport: DaytonaTransport, fake_sandbox: SimpleNamespace,
) -> None:
    """Payloads larger than the inline limit go through a staged tmp-file path."""
    captured_commands: list[str] = []

    async def _record(command: str, **_kwargs: Any) -> SimpleNamespace:
        captured_commands.append(command)
        # Return a synthetic OK response — the staging sequence is at least
        # 3 calls (truncate + N appends + apply + cleanup), so we must
        # respond OK for every one.
        if "json.loads(pathlib.Path(sys.argv[1]).read_text" in command:
            return _exec_response(
                json.dumps({"ok": True, "written_paths": ["/big"]}),
                exit_code=0,
            )
        return _exec_response("", exit_code=0)

    fake_sandbox.process.exec.side_effect = _record

    big = b"x" * (32 * 1024)
    specs = (CheckedWriteSpec(path="/big", content=big, expected_sha=None),)

    result = await transport.apply_diff_batch_checked("sb-1", specs)

    assert result.success is True
    assert result.written_paths == ("/big",)
    # Look for the staged-prelude marker (reads ops from sys.argv[1]) —
    # confirms we took the staged path, not inline.
    staged = [c for c in captured_commands if "json.loads(pathlib.Path(sys.argv[1]).read_text" in c]
    assert staged, f"staged apply not invoked; captured: {captured_commands}"
    # And we cleaned up the tmp file.
    cleanup = [c for c in captured_commands if c.startswith(("env -u LC_ALL", "")) and "rm -f" in c]
    assert cleanup, f"tmp cleanup not invoked; captured: {captured_commands}"


# -- read_bytes_batch -------------------------------------------------------


async def test_read_bytes_batch_empty_short_circuits(
    transport: DaytonaTransport, fake_sandbox: SimpleNamespace,
) -> None:
    result = await transport.read_bytes_batch("sb-1", [])
    assert result == {}
    fake_sandbox.fs.download_file.assert_not_called()


async def test_read_bytes_batch_falls_back_when_no_batch_api(
    transport: DaytonaTransport, fake_sandbox: SimpleNamespace,
) -> None:
    """When fs has no download_files, the transport reads files one at a time."""
    fake_sandbox.fs.download_file.side_effect = [
        b"alpha",
        FileNotFoundError("/missing"),
        b"gamma",
    ]
    paths = ("/a", "/missing", "/c")

    result = await transport.read_bytes_batch("sb-1", paths)

    assert result == {"/a": b"alpha", "/missing": None, "/c": b"gamma"}
    assert fake_sandbox.fs.download_file.await_count == 3


# -- resolver injection ------------------------------------------------------


async def test_resolver_failure_raises_transport_error() -> None:
    async def _broken(_sid: str) -> Any:
        raise RuntimeError("no such sandbox")

    transport = DaytonaTransport(sandbox_resolver=_broken)
    with pytest.raises(SandboxTransportError, match="could not resolve sandbox"):
        await transport.exec("sb-missing", "echo")


def test_transport_satisfies_protocol_method_set() -> None:
    """The class declares every method named on ``SandboxTransport``."""
    from sandbox.api.transport import SandboxTransport

    expected = {
        name
        for name in dir(SandboxTransport)
        if not name.startswith("_") and callable(getattr(SandboxTransport, name))
    }
    declared = {
        name
        for name in dir(DaytonaTransport)
        if not name.startswith("_") and callable(getattr(DaytonaTransport, name))
    }
    missing = expected - declared
    assert not missing, f"DaytonaTransport missing methods: {sorted(missing)}"
