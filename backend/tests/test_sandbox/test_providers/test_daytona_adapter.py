"""Tests for the Daytona provider adapter."""

from __future__ import annotations

from sandbox.api.models import RawExecResult
from sandbox.providers.daytona.adapter import DaytonaProviderAdapter


class FakeTransport:
    name = "fake-transport"

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
        return RawExecResult(exit_code=0, stdout="ok")


async def test_daytona_provider_adapter_delegates_exec() -> None:
    transport = FakeTransport()
    adapter = DaytonaProviderAdapter(transport=transport)

    result = await adapter.exec(
        "sb-1",
        "echo ok",
        cwd="/workspace",
        timeout=12,
    )

    assert result == RawExecResult(exit_code=0, stdout="ok")
    assert transport.calls == [("sb-1", "echo ok", "/workspace", 12)]


async def test_build_sandbox_transport_registers_provider_adapter(monkeypatch) -> None:
    from sandbox.lifecycle.workspace import _build_sandbox_transport
    from sandbox.providers.registry import dispose_adapter, get_adapter

    sandbox_id = "test-build-transport-registers-provider"
    transport = FakeTransport()
    dispose_adapter(sandbox_id)
    monkeypatch.setattr(
        "sandbox.daytona.transport.DaytonaTransport",
        lambda: transport,
    )

    built = _build_sandbox_transport(sandbox_id)

    assert built is transport
    adapter = get_adapter(sandbox_id)
    result = await adapter.exec(sandbox_id, "pwd")
    assert result == RawExecResult(exit_code=0, stdout="ok")
    assert transport.calls == [(sandbox_id, "pwd", None, None)]
    dispose_adapter(sandbox_id)
