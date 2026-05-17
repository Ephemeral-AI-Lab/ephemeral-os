"""Tests for the ``install_lsp`` kwarg added to ``setup_sweevo_sandbox``.

The kwarg is additive (default False) — existing callers must see
byte-identical behavior. Only when ``install_lsp=True`` does the
function reach into :func:`sandbox.plugin.install.ensure_installed`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
        test_patch="",
        fail_to_pass=[],
        pass_to_pass=[],
        docker_image="example/image",
        test_cmds="pytest -q",
        environment_setup_commit="",
    )


def _install_common_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the side-effecting helpers ``setup_sweevo_sandbox`` calls."""

    async def fake_wait(_sandbox_id: str, *, attempts: int = 6) -> None:
        return None

    async def fake_exec(_sandbox_id: str, _command: str, **_kwargs: Any) -> str:
        return ""

    async def fake_rebuild(
        _sandbox_id: str, _repo_dir: str, *, on_progress: Any = None
    ) -> None:
        return None

    monkeypatch.setattr(sweevo_sandbox, "_wait_for_sandbox_exec_ready", fake_wait)
    monkeypatch.setattr(sweevo_sandbox, "_exec", fake_exec)
    monkeypatch.setattr(sweevo_sandbox, "_rebuild_sweevo_workspace_base", fake_rebuild)
    monkeypatch.setattr(
        sweevo_sandbox.sandbox_api,
        "get_sandbox",
        lambda _sandbox_id: {"labels": {}},
    )
    monkeypatch.setattr(
        sweevo_sandbox.sandbox_api,
        "set_sandbox_labels",
        lambda _sandbox_id, _labels: None,
    )


@pytest.mark.asyncio
async def test_setup_sandbox_default_skips_lsp_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default ``install_lsp=False`` must NOT call ``ensure_installed``."""
    _install_common_stubs(monkeypatch)

    raising_ensure = AsyncMock(side_effect=AssertionError("must not be called"))
    monkeypatch.setattr("sandbox.plugin.install.ensure_installed", raising_ensure)

    await sweevo_sandbox.setup_sweevo_sandbox(_instance(), "sbx-1")

    raising_ensure.assert_not_called()


@pytest.mark.asyncio
async def test_setup_sandbox_install_lsp_invokes_ensure_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_common_stubs(monkeypatch)

    captured: dict[str, Any] = {}

    async def fake_ensure(sandbox_id: str, manifest: Any) -> str:
        captured["sandbox_id"] = sandbox_id
        captured["manifest"] = manifest
        return "digest123"

    monkeypatch.setattr("sandbox.plugin.install.ensure_installed", fake_ensure)

    await sweevo_sandbox.setup_sweevo_sandbox(
        _instance(), "sbx-1", install_lsp=True
    )

    assert captured["sandbox_id"] == "sbx-1"
    # ``parse_plugin_manifest(DEFAULT_CATALOG_DIR / "lsp")`` returns a real
    # PluginManifest dataclass; verify the resolved plugin name is ``"lsp"``.
    assert captured["manifest"].name == "lsp"


@pytest.mark.asyncio
async def test_setup_sandbox_install_lsp_runs_after_workspace_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LSP install must NOT block the existing workspace-rebuild path."""
    order: list[str] = []

    async def fake_wait(_sandbox_id: str, *, attempts: int = 6) -> None:
        order.append("wait")

    async def fake_exec(_sandbox_id: str, _command: str, **_kwargs: Any) -> str:
        return ""

    async def fake_rebuild(
        _sandbox_id: str, _repo_dir: str, *, on_progress: Any = None
    ) -> None:
        order.append("rebuild")

    async def fake_ensure(sandbox_id: str, manifest: Any) -> str:
        order.append("ensure_installed")
        return "digest"

    monkeypatch.setattr(sweevo_sandbox, "_wait_for_sandbox_exec_ready", fake_wait)
    monkeypatch.setattr(sweevo_sandbox, "_exec", fake_exec)
    monkeypatch.setattr(sweevo_sandbox, "_rebuild_sweevo_workspace_base", fake_rebuild)
    monkeypatch.setattr(
        sweevo_sandbox.sandbox_api,
        "get_sandbox",
        lambda _sandbox_id: {"labels": {}},
    )
    monkeypatch.setattr(
        sweevo_sandbox.sandbox_api,
        "set_sandbox_labels",
        lambda _sandbox_id, _labels: None,
    )
    monkeypatch.setattr("sandbox.plugin.install.ensure_installed", fake_ensure)

    await sweevo_sandbox.setup_sweevo_sandbox(
        _instance(), "sbx-1", install_lsp=True
    )

    assert order.index("rebuild") < order.index("ensure_installed")


@pytest.mark.asyncio
async def test_sweevo_provisioner_default_does_not_install_lsp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``SweevoProvisioner`` constructed without ``install_lsp`` keeps the legacy path."""
    from task_center_runner.benchmarks.sweevo import provisioner as provisioner_mod

    captured: dict[str, Any] = {}

    async def fake_setup(*args: Any, **kwargs: Any) -> str:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "/testbed"

    monkeypatch.setattr(provisioner_mod, "setup_sweevo_sandbox", fake_setup)

    provisioner = provisioner_mod.SweevoProvisioner(
        _instance(), "sbx-1", repo_dir="/testbed"
    )
    await provisioner.provision(MagicMock())

    assert captured["kwargs"].get("install_lsp") is False


@pytest.mark.asyncio
async def test_sweevo_provisioner_forwards_install_lsp_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from task_center_runner.benchmarks.sweevo import provisioner as provisioner_mod

    captured: dict[str, Any] = {}

    async def fake_setup(*args: Any, **kwargs: Any) -> str:
        captured["kwargs"] = kwargs
        return "/testbed"

    monkeypatch.setattr(provisioner_mod, "setup_sweevo_sandbox", fake_setup)

    provisioner = provisioner_mod.SweevoProvisioner(
        _instance(), "sbx-1", repo_dir="/testbed", install_lsp=True
    )
    await provisioner.provision(MagicMock())

    assert captured["kwargs"].get("install_lsp") is True
