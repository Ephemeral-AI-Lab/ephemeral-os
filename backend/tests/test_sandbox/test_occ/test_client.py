"""Tests for the host-side OCC runtime client."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from sandbox.api.models import RawExecResult
from sandbox.occ.client import OCCClient, OCCClientError
from sandbox.occ.types import WriteSpec
from sandbox.providers.registry import dispose_adapter, register_adapter
from sandbox.runtime.bundle import BUNDLE_REMOTE_DIR


class _Adapter:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None, int | None]] = []

    async def exec(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> RawExecResult:
        self.calls.append((sandbox_id, command, cwd, timeout))
        argv = shlex.split(command)
        payload = json.loads(argv[-1])
        assert argv[:3] == ["python3", "-m", "sandbox.runtime.server"]
        assert payload["op"] == "occ.write"
        assert payload["args"]["workspace_root"] == "/workspace"
        return RawExecResult(
            exit_code=0,
            stdout=json.dumps(
                {
                    "success": True,
                    "status": "committed",
                    "files": [],
                    "conflict_file": None,
                    "conflict_reason": "",
                    "timings": {},
                }
            ),
        )


class _FailingAdapter:
    name = "failing"

    async def exec(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> RawExecResult:
        del sandbox_id, command, cwd, timeout
        return RawExecResult(exit_code=127, stdout="", stderr="python3: not found")


@pytest.mark.asyncio
async def test_occ_client_uses_one_adapter_exec_per_request() -> None:
    adapter = _Adapter()
    register_adapter("sb-occ", adapter)
    try:
        result = await OCCClient("sb-occ").write(
            WriteSpec(file_path="/workspace/a.txt", content="a\n")
        )
    finally:
        dispose_adapter("sb-occ")

    assert result.success is True
    assert len(adapter.calls) == 1
    assert adapter.calls[0][0] == "sb-occ"
    assert adapter.calls[0][2] == BUNDLE_REMOTE_DIR


@pytest.mark.asyncio
async def test_occ_client_surfaces_exec_failure_before_json_errors() -> None:
    register_adapter("sb-occ-fail", _FailingAdapter())
    try:
        with pytest.raises(OCCClientError) as exc:
            await OCCClient("sb-occ-fail").write(
                WriteSpec(file_path="/workspace/a.txt", content="a\n")
            )
    finally:
        dispose_adapter("sb-occ-fail")

    assert exc.value.kind == "RuntimeExecFailed"
    assert exc.value.details == {"exit_code": 127}


def test_occ_client_does_not_import_handlers_or_overlay() -> None:
    import sandbox.occ.client as client_module

    source = Path(client_module.__file__).read_text(encoding="utf-8")

    assert "sandbox.occ.handlers" not in source
    assert "sandbox.overlay" not in source
