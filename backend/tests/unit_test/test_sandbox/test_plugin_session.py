"""Unit tests for sandbox.plugin.session.call_plugin."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from plugins.core.manifest import PluginManifest, parse_plugin_manifest
from sandbox.plugin import session as session_mod
from sandbox.plugin.session import call_plugin
from tools.core.context import ToolExecutionContextService


def _make_context(sandbox_id: str = "sb-1") -> ToolExecutionContextService:
    ctx = ToolExecutionContextService(cwd=Path("/tmp"))
    ctx["sandbox_id"] = sandbox_id
    ctx["repo_root"] = "/testbed"
    return ctx


def _seed_demo_manifest(tmp_path: Path) -> PluginManifest:
    plugin_dir = tmp_path / "demo"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.md").write_text(
        "---\nname: demo\ndescription: demo\ntools:\n"
        "  - name: demo.run\n    module: tools/run.py\nsetup: setup.sh\n"
        "---\n",
        encoding="utf-8",
    )
    (plugin_dir / "tools").mkdir()
    (plugin_dir / "tools" / "run.py").write_text("x=1\n", encoding="utf-8")
    (plugin_dir / "setup.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    return parse_plugin_manifest(plugin_dir)


@pytest.fixture(autouse=True)
def _isolate_session() -> Iterator[None]:
    session_mod.reset_session_cache()
    yield
    session_mod.reset_session_cache()


def test_call_plugin_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _seed_demo_manifest(tmp_path)
    monkeypatch.setattr(
        session_mod, "_manifest_cache", {"demo": manifest}, raising=False
    )

    install_calls: list[str] = []

    async def fake_install(sandbox_id: str, m: PluginManifest) -> str:
        install_calls.append(sandbox_id)
        return "abc123"

    dispatch_calls: list[tuple[str, str, dict[str, Any]]] = []

    async def fake_dispatch(
        sandbox_id: str, op: str, args: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        del kwargs
        dispatch_calls.append((sandbox_id, op, dict(args)))
        if op == "api.plugin.ensure":
            return {"success": True, "plugin": "demo", "registered_ops": []}
        return {"success": True, "plugin": "demo", "result": {"value": 42}}

    result = asyncio.run(
        call_plugin(
            _make_context(),
            plugin="demo",
            op="run",
            payload={"x": 1},
            install_runner=fake_install,
            daemon_dispatcher=fake_dispatch,
        )
    )

    assert not result.is_error
    decoded = json.loads(result.output)
    assert decoded["result"] == {"value": 42}
    assert install_calls == ["sb-1"]
    op_names = [op for _sb, op, _args in dispatch_calls]
    assert op_names == ["api.plugin.ensure", "plugin.demo.run"]
    assert dispatch_calls[0][2] == {"plugin": "demo", "digest": "abc123"}


def test_call_plugin_install_failure_surfaces_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _seed_demo_manifest(tmp_path)
    monkeypatch.setattr(
        session_mod, "_manifest_cache", {"demo": manifest}, raising=False
    )

    async def boom_install(sandbox_id: str, m: PluginManifest) -> str:
        raise RuntimeError("install boom")

    async def never_dispatch(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("dispatch should not be reached after install fails")

    result = asyncio.run(
        call_plugin(
            _make_context(),
            plugin="demo",
            op="run",
            payload={},
            install_runner=boom_install,
            daemon_dispatcher=never_dispatch,
        )
    )

    assert result.is_error
    assert "install" in result.metadata.get("step", "")
    assert "install boom" in result.output


def test_call_plugin_dispatch_error_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _seed_demo_manifest(tmp_path)
    monkeypatch.setattr(
        session_mod, "_manifest_cache", {"demo": manifest}, raising=False
    )

    async def fake_install(sandbox_id: str, m: PluginManifest) -> str:
        return "abc"

    async def fake_dispatch(
        sandbox_id: str, op: str, args: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        if op == "api.plugin.ensure":
            return {"success": True, "registered_ops": []}
        return {
            "success": False,
            "error": {"kind": "OpFailed", "message": "boom in plugin op"},
        }

    result = asyncio.run(
        call_plugin(
            _make_context(),
            plugin="demo",
            op="run",
            payload={},
            install_runner=fake_install,
            daemon_dispatcher=fake_dispatch,
        )
    )

    assert result.is_error
    assert "boom in plugin op" in result.output
    assert result.metadata["step"] == "dispatch"


def test_call_plugin_reensures_runtime_when_digest_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _seed_demo_manifest(tmp_path)
    monkeypatch.setattr(
        session_mod, "_manifest_cache", {"demo": manifest}, raising=False
    )
    digests = iter(["digest-a", "digest-b"])
    ensure_payloads: list[dict[str, Any]] = []

    async def changing_install(sandbox_id: str, m: PluginManifest) -> str:
        del sandbox_id, m
        return next(digests)

    async def fake_dispatch(
        sandbox_id: str, op: str, args: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        del sandbox_id, kwargs
        if op == "api.plugin.ensure":
            ensure_payloads.append(dict(args))
        return {"success": True}

    for _ in range(2):
        result = asyncio.run(
            call_plugin(
                _make_context(),
                plugin="demo",
                op="run",
                payload={},
                install_runner=changing_install,
                daemon_dispatcher=fake_dispatch,
            )
        )
        assert not result.is_error

    assert ensure_payloads == [
        {"plugin": "demo", "digest": "digest-a"},
        {"plugin": "demo", "digest": "digest-b"},
    ]


def test_call_plugin_missing_sandbox_id_returns_error() -> None:
    ctx = ToolExecutionContextService(cwd=Path("/tmp"))
    # No sandbox_id set.

    async def never_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("should not run when sandbox_id missing")

    result = asyncio.run(
        call_plugin(
            ctx,
            plugin="demo",
            op="run",
            payload={},
            install_runner=never_called,
            daemon_dispatcher=never_called,
        )
    )
    assert result.is_error


def test_call_plugin_unknown_plugin_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_mod, "_manifest_cache", {}, raising=False)

    async def never_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("should not run for unknown plugin")

    result = asyncio.run(
        call_plugin(
            _make_context(),
            plugin="ghost",
            op="run",
            payload={},
            install_runner=never_called,
            daemon_dispatcher=never_called,
        )
    )
    assert result.is_error
    assert result.metadata["step"] == "manifest"


def test_call_plugin_serializes_concurrent_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _seed_demo_manifest(tmp_path)
    monkeypatch.setattr(
        session_mod, "_manifest_cache", {"demo": manifest}, raising=False
    )

    install_starts: list[int] = []
    install_running = 0
    max_install_concurrency = 0

    async def fake_install(sandbox_id: str, m: PluginManifest) -> str:
        nonlocal install_running, max_install_concurrency
        install_running += 1
        max_install_concurrency = max(
            max_install_concurrency, install_running
        )
        await asyncio.sleep(0.01)
        install_starts.append(install_running)
        install_running -= 1
        return "abc"

    async def fake_dispatch(
        sandbox_id: str, op: str, args: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        if op == "api.plugin.ensure":
            return {"success": True, "registered_ops": []}
        return {"success": True}

    async def runner() -> None:
        await asyncio.gather(
            *(
                call_plugin(
                    _make_context(),
                    plugin="demo",
                    op="run",
                    payload={},
                    install_runner=fake_install,
                    daemon_dispatcher=fake_dispatch,
                )
                for _ in range(5)
            )
        )

    asyncio.run(runner())
    assert max_install_concurrency == 1
