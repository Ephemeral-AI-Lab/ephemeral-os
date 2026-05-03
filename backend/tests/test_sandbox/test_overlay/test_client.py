"""Tests for host-side OverlayClient routing."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from sandbox.api.models import RawExecResult
from sandbox.overlay.client import OverlayClient
from sandbox.providers.registry import dispose_adapter, register_adapter
from sandbox.runtime.bundle import BUNDLE_REMOTE_DIR


class _Adapter:
    name = "overlay-fake"

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
        assert payload["op"] == "overlay.run"
        assert payload["args"]["command"] == "echo hi"
        assert payload["args"]["workspace_root"] == "/workspace"
        return RawExecResult(
            exit_code=0,
            stdout=json.dumps(
                {
                    "exit_code": 0,
                    "stdout": "hi\n",
                    "upper_changes": [],
                    "overlay_rejected": False,
                    "conflict": None,
                    "warnings": [],
                    "overlay_run_timings": {},
                    "overlay_stage_timings": {},
                    "policy_reject": None,
                }
            ),
        )


@pytest.mark.asyncio
async def test_overlay_client_uses_one_adapter_exec_per_request() -> None:
    adapter = _Adapter()
    register_adapter("sb-overlay", adapter)
    try:
        result = await OverlayClient("sb-overlay").run("echo hi")
    finally:
        dispose_adapter("sb-overlay")

    assert result.stdout == "hi\n"
    assert len(adapter.calls) == 1
    assert adapter.calls[0][0] == "sb-overlay"
    assert adapter.calls[0][2] == BUNDLE_REMOTE_DIR


def test_overlay_client_does_not_import_occ_or_handlers() -> None:
    import sandbox.overlay.client as client_module

    source = Path(client_module.__file__).read_text(encoding="utf-8")

    assert "sandbox.occ" not in source
    assert "sandbox.overlay.handlers" not in source
