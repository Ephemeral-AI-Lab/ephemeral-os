"""Tests for ``CodeIntelligenceService.cmd`` fail-closed semantics.

Two guard rails protect the OCC contract: the overlay capability probe
(tmpfs / userxattr / overlay available) and the CoW lowerdir snapshot
(reflink / clonefile). When either signals unsupported, ``svc.cmd``
raises :class:`OverlayCapabilityMissingError` — there is no fallback to
the pre-OCC ``ProcessAuditor`` path.
"""

from __future__ import annotations

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
async def test_cmd_raises_when_lowerdir_lacks_cow_support(tmp_path) -> None:
    svc = _svc(tmp_path)
    svc._overlay_capability.probe = AsyncMock(  # type: ignore[method-assign]
        return_value=OverlayProbeResult(supported=True, reason="ok"),
    )
    svc._exec_sandbox_process = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(result="cow-unavailable", exit_code=2),
    )

    with pytest.raises(OverlayCapabilityMissingError) as excinfo:
        await svc.cmd(object(), "echo hi")

    assert "lacks CoW support" in str(excinfo.value)
    # Fail-closed: no auditor materialized; lowerdir stays un-memoized so a
    # later sandbox rebind with CoW support can retry cleanly.
    assert svc._overlay_auditor is None
    assert svc._overlay_lowerdir is None


@pytest.mark.asyncio
async def test_lowerdir_probe_requires_reflink_always_on_linux(tmp_path) -> None:
    """Regression guard: ``cp -a --reflink=auto`` silently falls back to a
    byte copy on ext4, aliasing peer writes into ``base_hash``. The probe
    must pin Linux to ``--reflink=always`` so non-CoW filesystems fail
    the ``cp`` invocation and flip the branch to ``cow-unavailable``.
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
        "Linux branch must require --reflink=always; auto would silently "
        "degrade to byte copy on ext4 and defeat OCC drift detection."
    )
    assert "Linux)" in probe_cmd and "Darwin)" in probe_cmd, (
        "Probe must branch per kernel — BSD cp has no --reflink flag, and "
        "APFS clonefile is implicit on plain `cp -a`."
    )


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

    assert (lowerdir / "foo.py").read_text(encoding="utf-8") == "new\n"
    assert not (lowerdir / "bar.py").exists()
    assert (lowerdir / "nested" / "baz.py").read_text(encoding="utf-8") == "fresh\n"
