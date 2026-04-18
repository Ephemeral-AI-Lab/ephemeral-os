"""Tests for ``CodeIntelligenceService.cmd`` fail-closed semantics.

Two guard rails protect the OCC contract: the overlay capability probe
(tmpfs / userxattr / overlay available) and lowerdir snapshot
materialization. When either signals unsupported, ``svc.cmd`` raises
:class:`OverlayCapabilityMissingError` — there is no fallback to the
pre-OCC ``ProcessAuditor`` path.
"""

from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from code_intelligence.routing.overlay_probe import OverlayProbeResult
from code_intelligence.routing.service import (
    CodeIntelligenceService,
    OverlayCapabilityMissingError,
    dispose_all_code_intelligence,
)


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    dispose_all_code_intelligence()
    yield
    dispose_all_code_intelligence()


def _svc(tmp_path) -> CodeIntelligenceService:
    return CodeIntelligenceService(
        sandbox_id=f"sandbox-cmd-{tmp_path.name}",
        workspace_root=str(tmp_path),
    )


@pytest.mark.asyncio
async def test_cmd_raises_when_overlay_probe_reports_unsupported(tmp_path) -> None:
    svc = _svc(tmp_path)
    svc._overlay_capability.probe = AsyncMock(  # type: ignore[method-assign]
        return_value=OverlayProbeResult(supported=False, reason="no_tmpfs"),
    )

    with pytest.raises(OverlayCapabilityMissingError) as excinfo:
        await svc.cmd(object(), "echo hi")

    assert "no_tmpfs" in str(excinfo.value)
    # Fail-closed: no auditor materialized and no lowerdir snapshot attempted.
    assert svc._overlay_auditor is None
    assert svc._overlay_lowerdir is None


@pytest.mark.asyncio
async def test_cmd_raises_when_lowerdir_snapshot_fails(tmp_path) -> None:
    svc = _svc(tmp_path)
    svc._overlay_capability.probe = AsyncMock(  # type: ignore[method-assign]
        return_value=OverlayProbeResult(supported=True, reason="ok"),
    )
    svc._exec_sandbox_process = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(result="snapshot-unavailable", exit_code=2),
    )

    with pytest.raises(OverlayCapabilityMissingError) as excinfo:
        await svc.cmd(object(), "echo hi")

    assert "lowerdir snapshot failed" in str(excinfo.value)
    # Fail-closed: no auditor materialized; lowerdir stays un-memoized so a
    # later sandbox rebind can retry cleanly.
    assert svc._overlay_auditor is None
    assert svc._overlay_lowerdir is None


@pytest.mark.asyncio
async def test_lowerdir_probe_prefers_reflink_then_byte_copy(tmp_path) -> None:
    """Regression guard: Linux should try a true CoW clone first, then
    fall back to a plain byte copy. The unsafe option is hardlinking the
    lowerdir to the workspace; byte copies are independent, just slower.
    """
    svc = _svc(tmp_path)
    svc._overlay_capability.probe = AsyncMock(  # type: ignore[method-assign]
        return_value=OverlayProbeResult(supported=True, reason="ok"),
    )
    recorded: list[str] = []

    async def _capture_exec(_sandbox, command, *, timeout=None):
        del timeout
        recorded.append(command)
        return SimpleNamespace(result="cow-reflink", exit_code=0)

    svc._exec_sandbox_process = _capture_exec  # type: ignore[method-assign]

    # _ensure_overlay_auditor will also need a no-op OverlayAuditor; stop
    # before it by raising OverlayCapabilityMissingError-free success and
    # then short-circuit by inspecting the captured probe command.
    try:
        await svc._ensure_overlay_lowerdir(object())
    finally:
        assert recorded, "probe command was never executed"

    probe_cmd = recorded[0]
    assert "--reflink=always" in probe_cmd, (
        "Linux branch should prefer true reflink snapshots when available."
    )
    assert "--reflink=auto" not in probe_cmd, (
        "The fallback must be explicit so logs distinguish CoW from byte copy."
    )
    assert 'cp -a "$src/." "$dst/"' in probe_cmd, (
        "Non-reflink filesystems still need an independent byte-copy snapshot."
    )
    assert "Linux)" in probe_cmd and "Darwin)" in probe_cmd, (
        "Probe must branch per kernel — BSD cp has no --reflink flag, and "
        "APFS clonefile is implicit on plain `cp -a`."
    )


@pytest.mark.asyncio
async def test_cached_overlay_auditor_rebuilds_when_lowerdir_missing(tmp_path) -> None:
    svc = _svc(tmp_path)
    old_auditor = object()
    sandbox = SimpleNamespace()
    svc._overlay_auditor = old_auditor  # type: ignore[assignment]
    svc._overlay_lowerdir = "/tmp/stale-lower"
    svc._lowerdir_is_live = AsyncMock(return_value=False)  # type: ignore[method-assign]

    async def _fresh_lowerdir(_sandbox):
        svc._overlay_lowerdir = "/tmp/fresh-lower"
        return "/tmp/fresh-lower"

    svc._ensure_overlay_lowerdir = AsyncMock(side_effect=_fresh_lowerdir)  # type: ignore[method-assign]

    auditor = await svc._ensure_overlay_auditor(sandbox)

    assert auditor is not old_auditor
    assert svc._overlay_lowerdir == "/tmp/fresh-lower"
    svc._lowerdir_is_live.assert_awaited_once_with(sandbox, "/tmp/stale-lower")


def test_refresh_overlay_lowerdir_mirrors_modify_and_delete(tmp_path) -> None:
    """A committed batch must be mirrored into the lowerdir snapshot so
    the next ``svc.cmd`` computes ``base_hash`` against workspace head,
    not a stale snapshot (P0.7). Covers both modify + delete kinds."""
    from code_intelligence.types import OperationChange

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    svc = CodeIntelligenceService(
        sandbox_id=f"sandbox-refresh-{tmp_path.name}",
        workspace_root=str(repo_root),
    )
    lowerdir = tmp_path / "lower"
    lowerdir.mkdir()
    (lowerdir / "foo.py").write_text("old\n", encoding="utf-8")
    (lowerdir / "bar.py").write_text("kept\n", encoding="utf-8")
    svc._overlay_lowerdir = str(lowerdir)

    asyncio.run(
        svc._refresh_overlay_lowerdir(
            [
                OperationChange(
                    file_path=f"{repo_root}/foo.py",
                    base_content="old\n",
                    base_hash="",
                    final_content="new\n",
                    base_existed=True,
                    strict_base=True,
                ),
                OperationChange(
                    file_path=f"{repo_root}/bar.py",
                    base_content="kept\n",
                    base_hash="",
                    final_content=None,  # delete
                    base_existed=True,
                    strict_base=True,
                ),
                OperationChange(
                    file_path=f"{repo_root}/nested/baz.py",
                    base_content="",
                    base_hash="",
                    final_content="fresh\n",
                    base_existed=False,
                ),
            ],
        )
    )

    assert (lowerdir / "foo.py").read_text(encoding="utf-8") == "new\n"
    assert not (lowerdir / "bar.py").exists()
    assert (lowerdir / "nested" / "baz.py").read_text(encoding="utf-8") == "fresh\n"


def test_refresh_overlay_lowerdir_uses_sandbox_transport_when_bound(tmp_path) -> None:
    """Live lowerdirs live in Daytona; refresh must write through process.exec."""
    from code_intelligence.types import OperationChange

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    lowerdir = tmp_path / "lower"
    lowerdir.mkdir()
    (lowerdir / "foo.py").write_text("old\n", encoding="utf-8")

    class _Process:
        async def exec(self, command: str, timeout: int | None = None):
            del timeout
            completed = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=True,
                text=True,
                capture_output=True,
                check=False,
            )
            return SimpleNamespace(
                result=completed.stdout + completed.stderr,
                exit_code=completed.returncode,
            )

    svc = CodeIntelligenceService(
        sandbox_id=f"sandbox-refresh-remote-{tmp_path.name}",
        workspace_root=str(repo_root),
        sandbox=SimpleNamespace(process=_Process()),
    )
    svc._overlay_lowerdir = str(lowerdir)

    asyncio.run(
        svc._refresh_overlay_lowerdir(
            [
                OperationChange(
                    file_path=f"{repo_root}/foo.py",
                    base_content="old\n",
                    base_hash="",
                    final_content="remote\n",
                    base_existed=True,
                    strict_base=True,
                ),
                OperationChange(
                    file_path=f"{repo_root}/created.py",
                    base_content="",
                    base_hash="",
                    final_content="created\n",
                    base_existed=False,
                ),
            ],
        )
    )

    assert (lowerdir / "foo.py").read_text(encoding="utf-8") == "remote\n"
    assert (lowerdir / "created.py").read_text(encoding="utf-8") == "created\n"
