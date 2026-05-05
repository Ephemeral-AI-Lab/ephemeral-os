"""Tests for host-side OverlayClient routing."""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.overlay.client import OverlayClient
from sandbox.overlay.capture.types import OverlayCapture


@pytest.mark.asyncio
async def test_overlay_client_run_uses_snapshot_runner() -> None:
    class _Runner:
        def __init__(self) -> None:
            self.commands: list[tuple[str, ...]] = []

        async def shell(self, request):
            self.commands.append(request.command)
            return OverlayCapture(
                exit_code=0,
                stdout_ref="/tmp/stdout",
                stderr_ref="/tmp/stderr",
                snapshot_version=3,
                changes=(),
            )

    runner = _Runner()
    result = await OverlayClient(runner=runner).run(("echo", "hi"))

    assert result.snapshot_version == 3
    assert runner.commands == [("echo", "hi")]


def test_overlay_client_does_not_import_occ_or_handlers() -> None:
    import sandbox.overlay.client as client_module

    source = Path(client_module.__file__).read_text(encoding="utf-8")

    assert "sandbox.occ" not in source
    assert "sandbox.overlay.handlers" not in source
