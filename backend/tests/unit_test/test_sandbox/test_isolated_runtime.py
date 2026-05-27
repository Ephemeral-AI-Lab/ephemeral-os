"""Unit contracts for isolated workspace runtime helpers."""

from __future__ import annotations

import signal
import subprocess
from typing import Any

import pytest

from sandbox.isolated_workspace._control_plane import namespace_runtime as runtime_module
from sandbox.isolated_workspace.scripts import ns_holder as ns_holder_module


class _FakePopen:
    def __init__(self, *, timeout_first_wait: bool = False) -> None:
        self.timeout_first_wait = timeout_first_wait
        self.wait_calls: list[float | None] = []

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        if self.timeout_first_wait and len(self.wait_calls) == 1:
            raise subprocess.TimeoutExpired(cmd="holder", timeout=timeout)
        return 0


def _patch_process_primitives(
    monkeypatch: pytest.MonkeyPatch,
    *,
    reaped_pid: int | None = None,
) -> list[tuple[int, Any]]:
    signals: list[tuple[int, Any]] = []

    def fake_kill(pid: int, sig: Any) -> None:
        signals.append((pid, sig))

    def fake_waitpid(pid: int, _options: int) -> tuple[int, int]:
        if reaped_pid is not None and pid == reaped_pid:
            return pid, 0
        return 0, 0

    monkeypatch.setattr(runtime_module.os, "kill", fake_kill)
    monkeypatch.setattr(runtime_module.os, "waitpid", fake_waitpid)
    return signals


def test_kill_holder_reaps_tracked_process_without_sigkill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals = _patch_process_primitives(monkeypatch)
    runtime = runtime_module._KernelNamespaceRuntime()
    proc = _FakePopen()
    runtime._holders[1234] = proc  # type: ignore[assignment]

    runtime.kill_holder(1234, grace_s=5.0)

    assert signals == [(1234, signal.SIGTERM)]
    assert proc.wait_calls == [5.0]
    assert runtime._holders == {}


def test_kill_holder_signals_tracked_grandchild_for_graceful_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals = _patch_process_primitives(monkeypatch, reaped_pid=5678)
    runtime = runtime_module._KernelNamespaceRuntime()
    proc = _FakePopen()
    runtime._holders[1234] = proc  # type: ignore[assignment]
    runtime._grandchildren[1234] = 5678

    runtime.kill_holder(1234, grace_s=5.0)

    assert signals == [(5678, signal.SIGTERM)]
    assert proc.wait_calls == [5.0]
    assert runtime._holders == {}
    assert runtime._grandchildren == {}


def test_kill_holder_sigkills_tracked_process_after_grace_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals = _patch_process_primitives(monkeypatch)
    runtime = runtime_module._KernelNamespaceRuntime()
    proc = _FakePopen(timeout_first_wait=True)
    runtime._holders[1234] = proc  # type: ignore[assignment]

    runtime.kill_holder(1234, grace_s=0.25)

    assert signals == [(1234, signal.SIGTERM), (1234, signal.SIGKILL)]
    assert proc.wait_calls == [0.25, 2.0]
    assert runtime._holders == {}


def test_ns_holder_disables_router_advertisements_on_actual_interfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(
        args: list[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(ns_holder_module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        ns_holder_module.os,
        "listdir",
        lambda path: ["all", "default", "lo", "eos-iws-abc123n"]
        if path == "/proc/sys/net/ipv6/conf"
        else [],
    )

    ns_holder_module._purge_ipv6_default_routes()

    sysctl_keys = [
        command[2]
        for command in commands
        if command[:2] == ["sysctl", "-w"]
    ]
    assert "net.ipv6.conf.eos-iws-abc123n.accept_ra=0" in sysctl_keys
