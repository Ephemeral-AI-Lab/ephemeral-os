"""Tests for public sandbox API runtime transport helpers."""

from __future__ import annotations

from typing import Any

import pytest

from sandbox.api.tool import _runtime as runtime_mod


class _Adapter:
    async def exec(self, *_args: object, **_kwargs: object) -> Any:
        raise AssertionError("runtime dispatch is mocked in this test")


@pytest.mark.asyncio
async def test_call_runtime_api_dispatches_without_bundle_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch_calls: list[tuple[str, str, dict[str, object], int]] = []

    async def fake_call_runtime_server(*, exec_fn, sandbox_id, op, args, timeout):
        del exec_fn
        dispatch_calls.append((sandbox_id, op, args, timeout))
        return {"success": True, "timings": {}}

    monkeypatch.setattr(runtime_mod, "get_adapter", lambda _sandbox_id: _Adapter())
    monkeypatch.setattr(runtime_mod, "_call_runtime_server", fake_call_runtime_server)

    await runtime_mod.call_runtime_api(
        "sb-1",
        "api.first",
        {"path": "a.txt"},
        timeout=10,
        layer_stack_root="/runtime/layers",
    )
    await runtime_mod.call_runtime_api(
        "sb-1",
        "api.second",
        {"path": "b.txt"},
        timeout=20,
        layer_stack_root="/runtime/layers",
    )

    assert dispatch_calls == [
        (
            "sb-1",
            "api.first",
            {"layer_stack_root": "/runtime/layers", "path": "a.txt"},
            10,
        ),
        (
            "sb-1",
            "api.second",
            {"layer_stack_root": "/runtime/layers", "path": "b.txt"},
            20,
        ),
    ]
