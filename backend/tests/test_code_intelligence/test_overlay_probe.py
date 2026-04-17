"""Unit tests for :mod:`code_intelligence.routing.overlay_probe`."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from code_intelligence.routing.overlay_probe import (
    OverlayCapabilityCache,
    probe_overlay_capability,
)


def _reply(text: str) -> SimpleNamespace:
    return SimpleNamespace(result=text)


@pytest.mark.asyncio
async def test_probe_detects_supported() -> None:
    async def fake_exec(sandbox: Any, command: str, *, timeout: Any) -> Any:
        return _reply("some debug\nPROBE_OK\nmore\n")

    result = await probe_overlay_capability(object(), fake_exec)
    assert result.supported
    assert result.reason == "ok"


@pytest.mark.asyncio
async def test_probe_detects_overlay_failure() -> None:
    async def fake_exec(sandbox: Any, command: str, *, timeout: Any) -> Any:
        return _reply("tmpfs ok\nPROBE_FAIL:overlay\n")

    result = await probe_overlay_capability(object(), fake_exec)
    assert not result.supported
    assert result.reason == "overlay"


@pytest.mark.asyncio
async def test_probe_returns_unknown_on_silent_failure() -> None:
    async def fake_exec(sandbox: Any, command: str, *, timeout: Any) -> Any:
        return _reply("")

    result = await probe_overlay_capability(object(), fake_exec)
    assert not result.supported
    assert result.reason == "unknown"


@pytest.mark.asyncio
async def test_probe_returns_transport_failure_reason() -> None:
    async def fake_exec(sandbox: Any, command: str, *, timeout: Any) -> Any:
        raise RuntimeError("remote disconnected")

    result = await probe_overlay_capability(object(), fake_exec)
    assert not result.supported
    assert "transport" in result.reason


@pytest.mark.asyncio
async def test_capability_cache_probes_once() -> None:
    call_count = {"n": 0}

    async def fake_exec(sandbox: Any, command: str, *, timeout: Any) -> Any:
        call_count["n"] += 1
        return _reply("PROBE_OK")

    cache = OverlayCapabilityCache()
    r1 = await cache.probe("sbx-1", object(), fake_exec)
    r2 = await cache.probe("sbx-1", object(), fake_exec)
    assert r1.supported and r2.supported
    assert call_count["n"] == 1

    # force=True re-probes.
    await cache.probe("sbx-1", object(), fake_exec, force=True)
    assert call_count["n"] == 2

    # Different sandbox id -> fresh probe.
    await cache.probe("sbx-2", object(), fake_exec)
    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_capability_cache_invalidate() -> None:
    async def fake_exec(sandbox: Any, command: str, *, timeout: Any) -> Any:
        return _reply("PROBE_OK")

    cache = OverlayCapabilityCache()
    await cache.probe("sbx", object(), fake_exec)
    cache.invalidate("sbx")
    assert cache.get("sbx") is None
