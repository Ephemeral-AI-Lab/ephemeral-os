from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from benchmarks.sweevo import sandbox as sweevo_sandbox
from benchmarks.sweevo.models import SWEEvoInstance


def _instance() -> SWEEvoInstance:
    return SWEEvoInstance(
        instance_id="dask__dask_2023.3.2_2023.4.0",
        repo="dask/dask",
        base_commit="abc123",
        problem_statement="",
        patch="",
        test_patch="diff --git a/foo b/foo\n",
        fail_to_pass=["dask/tests/test_cli.py::test_config_get"],
        pass_to_pass=["dask/tests/test_config.py::test_collect"],
        docker_image="example/image",
        test_cmds="pytest -q",
        environment_setup_commit="",
    )


@pytest.mark.asyncio
async def test_ensure_sweevo_test_patch_uploads_bytes_before_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[str] = []

    async def fake_exec(_sandbox_id: str, command: str, **_kwargs) -> str:
        commands.append(command)
        if "git apply --check" in command:
            return "APPLYABLE"
        return ""

    monkeypatch.setattr(sweevo_sandbox, "_exec", fake_exec)

    await sweevo_sandbox.ensure_sweevo_test_patch(_instance(), "sbx-1")

    assert commands[:3] == [
        ": > /tmp/sweevo_test.patch.b64",
        "printf %s ZGlmZiAtLWdpdCBhL2ZvbyBiL2Zvbwo= >> /tmp/sweevo_test.patch.b64",
        "base64 -d /tmp/sweevo_test.patch.b64 > /tmp/sweevo_test.patch && rm -f /tmp/sweevo_test.patch.b64",
    ]


@pytest.mark.asyncio
async def test_create_sweevo_test_sandbox_does_not_apply_test_patch_before_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SimpleNamespace(
        create_sandbox=lambda **_: {"id": "sbx-1"},
        get_sandbox=lambda _sandbox_id: {"id": "sbx-1"},
    )
    monkeypatch.setattr(sweevo_sandbox, "_service", lambda: service)
    monkeypatch.setattr(sweevo_sandbox, "setup_sweevo_sandbox", AsyncMock(return_value="/testbed"))
    ensure_mock = AsyncMock()
    monkeypatch.setattr(sweevo_sandbox, "ensure_sweevo_test_patch", ensure_mock)

    result = await sweevo_sandbox.create_sweevo_test_sandbox(
        _instance(),
        register_snapshot=False,
    )

    assert result["sandbox_id"] == "sbx-1"
    ensure_mock.assert_not_awaited()
