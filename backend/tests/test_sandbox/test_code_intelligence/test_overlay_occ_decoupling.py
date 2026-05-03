"""Slice 5a integration tests — overlay never drives OCC commit.

Three gating scenarios:
1. Overlay-reject: write_coordinator.commit_operation_against_base is NOT called.
2. Overlay-success → OCC-conflict: ConflictInfo(reason='patch_failed') flows
   through; the upper-layer live path is captured for diagnosis.
3. Argv overflow: SandboxTransportError surfaces as
   ConflictInfo(reason='argv_too_large') — not a bare-string failure.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from sandbox.api.errors import SandboxTransportError
from sandbox.code_intelligence.core.types import OperationResult
from sandbox.code_intelligence.overlay.command_executor import AuditedCommandExecutor
from sandbox.code_intelligence.overlay.types import (
    OverlayPolicyReject,
    OverlayRunOutcome,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    return tmp_path


def _outcome_with_dirty(workspace: Path) -> OverlayRunOutcome:
    """An overlay run that produced one tracked dirty change."""
    from sandbox.code_intelligence.core.types import OperationChange

    change = OperationChange(
        file_path=str(workspace / "app.py"),
        base_content="old\n",
        base_hash="hash-old",
        final_content="new\n",
        base_existed=True,
        strict_base=True,
    )
    return OverlayRunOutcome(
        exit_code=0,
        stdout="ran\n",
        dirty_changes=(change,),
        overlay_rejected=False,
        conflict=None,
        gitignore_paths=(),
        gitinclude_live_paths=(str(workspace / "app.py"),),
        mixed_gitinclude_gitignore=False,
        warnings=(),
        overlay_run_timings={},
        overlay_stage_timings={},
        policy_reject=None,
    )


def _make_executor(
    workspace: Path,
    *,
    write_coordinator,
    sandbox_id: str = "slice-5a-test",
) -> AuditedCommandExecutor:
    return AuditedCommandExecutor(
        sandbox_id=sandbox_id,
        workspace_root=str(workspace),
        write_coordinator=write_coordinator,
        rebind_sandbox=lambda _sandbox: None,
        transport=None,
        daemon_local=False,
    )


# ---------------------------------------------------------------------------
# Test 1 — overlay-reject path: OCC commit is never invoked.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overlay_reject_skips_occ_commit(workspace: Path) -> None:
    write_coordinator = MagicMock()
    write_coordinator.commit_operation_against_base = MagicMock()

    reject = OverlayPolicyReject(
        reason="overlay_rejected_dotgit_writes",
        paths=(".git/config",),
    )
    reject_outcome = OverlayRunOutcome(
        exit_code=201,
        stdout="",
        dirty_changes=(),
        overlay_rejected=True,
        conflict=None,
        gitignore_paths=(),
        gitinclude_live_paths=(),
        mixed_gitinclude_gitignore=False,
        warnings=("overlay_rejected_dotgit_writes",),
        overlay_run_timings={},
        overlay_stage_timings={},
        policy_reject=reject,
    )
    fake_overlay = SimpleNamespace(execute=AsyncMock(return_value=reject_outcome))

    executor = _make_executor(workspace, write_coordinator=write_coordinator)
    executor._ensure_overlay_auditor = AsyncMock(return_value=fake_overlay)  # type: ignore[method-assign]

    result = await executor.cmd(SimpleNamespace(), "echo .git/hack")

    # The defining correctness assertion of slice 5a — OCC is never touched
    # when overlay rejected the run.
    assert write_coordinator.commit_operation_against_base.call_count == 0
    assert result.git_commit_status == "rejected"
    assert "overlay_rejected_dotgit_writes" in (result.git_conflict_reason or "")
    assert result.changed_paths == []


# ---------------------------------------------------------------------------
# Test 2 — overlay-success → OCC-conflict: ConflictInfo(reason='patch_failed').
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overlay_success_then_occ_conflict_surfaces_patch_failed(
    workspace: Path,
) -> None:
    occ_failure = OperationResult(
        success=False,
        status="aborted_version",
        files=(),
        conflict_file=str(workspace / "app.py"),
        conflict_reason="base_mismatch",
        timings={"total": 0.01},
    )
    write_coordinator = MagicMock()
    write_coordinator.commit_operation_against_base = MagicMock(return_value=occ_failure)

    success_outcome = _outcome_with_dirty(workspace)
    fake_overlay = SimpleNamespace(execute=AsyncMock(return_value=success_outcome))

    executor = _make_executor(workspace, write_coordinator=write_coordinator)
    executor._ensure_overlay_auditor = AsyncMock(return_value=fake_overlay)  # type: ignore[method-assign]

    result = await executor.cmd(SimpleNamespace(), "echo hi")

    # Raw OCC verdict flows verbatim onto git_commit_status.
    assert result.git_commit_status == "aborted_version"
    # Slice 5a normalises the conflict reason to 'patch_failed'.
    assert result.git_conflict_reason == "patch_failed"
    assert result.git_conflict_file == str(workspace / "app.py")
    # Overlay's upper layer captured for diagnosis (not lost).
    assert str(workspace / "app.py") in result.ambient_changed_paths
    assert result.changed_paths == []
    assert write_coordinator.commit_operation_against_base.call_count == 1


# ---------------------------------------------------------------------------
# Test 3 — argv overflow surfaces as ConflictInfo(reason='argv_too_large').
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_argv_overflow_surfaces_as_argv_too_large(workspace: Path) -> None:
    def _raise_argv_overflow(*_args, **_kwargs):
        raise SandboxTransportError(
            "checked batch apply failed: exit_code=126 stdout='Argument list too long'"
        )

    write_coordinator = MagicMock()
    write_coordinator.commit_operation_against_base = MagicMock(
        side_effect=_raise_argv_overflow
    )

    success_outcome = _outcome_with_dirty(workspace)
    fake_overlay = SimpleNamespace(execute=AsyncMock(return_value=success_outcome))

    executor = _make_executor(workspace, write_coordinator=write_coordinator)
    executor._ensure_overlay_auditor = AsyncMock(return_value=fake_overlay)  # type: ignore[method-assign]

    result = await executor.cmd(SimpleNamespace(), "echo hi")

    assert result.git_conflict_reason == "argv_too_large"
    assert result.git_commit_status == "failed"
    assert result.git_conflict_file == str(workspace / "app.py")
    assert any("argv could fit" in w.lower() for w in result.warnings)
    assert result.changed_paths == []


@pytest.mark.asyncio
async def test_argv_overflow_via_runtime_error_also_surfaces(workspace: Path) -> None:
    """ContentManager bubbles transport failures as RuntimeError(str(exc))."""

    def _raise_runtime_argv(*_args, **_kwargs):
        raise RuntimeError("checked batch apply failed: argument list too long")

    write_coordinator = MagicMock()
    write_coordinator.commit_operation_against_base = MagicMock(
        side_effect=_raise_runtime_argv
    )

    success_outcome = _outcome_with_dirty(workspace)
    fake_overlay = SimpleNamespace(execute=AsyncMock(return_value=success_outcome))

    executor = _make_executor(workspace, write_coordinator=write_coordinator)
    executor._ensure_overlay_auditor = AsyncMock(return_value=fake_overlay)  # type: ignore[method-assign]

    result = await executor.cmd(SimpleNamespace(), "echo hi")

    assert result.git_conflict_reason == "argv_too_large"
    assert result.git_commit_status == "failed"


@pytest.mark.asyncio
async def test_unrelated_runtime_error_propagates(workspace: Path) -> None:
    """RuntimeError without argv-overflow signal must NOT be swallowed."""

    def _raise_other(*_args, **_kwargs):
        raise RuntimeError("disk full")

    write_coordinator = MagicMock()
    write_coordinator.commit_operation_against_base = MagicMock(side_effect=_raise_other)

    success_outcome = _outcome_with_dirty(workspace)
    fake_overlay = SimpleNamespace(execute=AsyncMock(return_value=success_outcome))

    executor = _make_executor(workspace, write_coordinator=write_coordinator)
    executor._ensure_overlay_auditor = AsyncMock(return_value=fake_overlay)  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="disk full"):
        await executor.cmd(SimpleNamespace(), "echo hi")
