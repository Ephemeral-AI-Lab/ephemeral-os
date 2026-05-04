"""In-sandbox runtime pipelines."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, Protocol

from sandbox.occ.changeset.builders import overlay_changes_to_changeset
from sandbox.occ.changeset.types import Change, ChangesetResult, FileStatus
from sandbox.occ.content.manager import ContentManager
from sandbox.occ.direct.direct_merge_coordinator import DirectMergeCoordinator
from sandbox.occ.gated.gated_coordinator import OCCGatedCoordinator
from sandbox.occ.orchestrator import ChangesetOrchestrator
from sandbox.occ.routing.gitignore import GitignoreOracle
from sandbox.overlay.engine import OverlayCaptureEngine, OverlayEngine
from sandbox.overlay.types import OverlayRunOutcome
from sandbox.runtime.types import ConflictInfo, ShellResult


class _OrchestratorLike(Protocol):
    """Subset of :class:`ChangesetOrchestrator` used by ``shell_pipeline``."""

    async def apply(self, changes: list[Change]) -> ChangesetResult: ...


async def shell_pipeline(
    *,
    command: str,
    workspace_root: str = "/workspace",
    sandbox_id: str = "local",
    timeout: int | None = None,
    stdin: str | None = None,
    description: str = "",
    agent_id: str = "",
    overlay_engine: OverlayEngine | None = None,
    orchestrator: _OrchestratorLike | None = None,
    overlay_sandbox: Any = None,
    on_progress_line: Callable[[str], None] | None = None,
) -> ShellResult:
    """Run shell through overlay capture, then project the gate's verdict.

    The default *orchestrator* is a fresh :class:`ChangesetOrchestrator` built
    per request. Tests inject a fake orchestrator to drive specific outcomes
    without standing up the real gate.
    """
    owns_overlay = overlay_engine is None
    overlay = overlay_engine or OverlayCaptureEngine(
        sandbox_id=sandbox_id,
        workspace_root=workspace_root,
        direct_runtime=True,
    )
    try:
        outcome = await _execute_overlay(
            overlay,
            command,
            sandbox=overlay_sandbox,
            timeout=timeout,
            stdin=stdin,
            description=description,
            agent_id=agent_id,
            on_progress_line=on_progress_line,
        )

        gate = orchestrator if orchestrator is not None else _build_orchestrator(workspace_root)
        typed_changes = overlay_changes_to_changeset(outcome.upper_changes)
        result = await gate.apply(typed_changes)
        return _shell_result_from_changeset(outcome, result, workspace_root=workspace_root)
    finally:
        if owns_overlay:
            dispose = getattr(overlay, "dispose", None)
            if callable(dispose):
                dispose()


def _build_orchestrator(workspace_root: str) -> ChangesetOrchestrator:
    content = ContentManager(workspace_root)
    return ChangesetOrchestrator(
        gitignore=GitignoreOracle(workspace_root),
        direct=DirectMergeCoordinator(content),
        gated=OCCGatedCoordinator(content),
    )


async def _execute_overlay(
    overlay: OverlayEngine,
    command: str,
    *,
    sandbox: Any,
    timeout: int | None,
    stdin: str | None,
    description: str,
    agent_id: str,
    on_progress_line: Callable[[str], None] | None,
) -> OverlayRunOutcome:
    result = overlay.execute(
        command,
        sandbox=sandbox,
        timeout=timeout,
        stdin=stdin,
        description=description,
        agent_id=agent_id,
        on_progress_line=on_progress_line,
    )
    return await _maybe_await(result)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _shell_result_from_changeset(
    outcome: OverlayRunOutcome,
    result: ChangesetResult,
    *,
    workspace_root: str,
) -> ShellResult:
    """Project a :class:`ChangesetResult` into a :class:`ShellResult`."""
    committed = sorted({
        _absolutize(f.path, workspace_root)
        for f in result.files
        if f.status is FileStatus.COMMITTED and f.path
    })
    changed_paths = tuple(committed)

    if result.success:
        return ShellResult(
            result=outcome.stdout,
            exit_code=outcome.exit_code,
            changed_paths=changed_paths,
            warnings=tuple(outcome.warnings),
            overlay_run_timings=dict(outcome.overlay_run_timings),
            overlay_stage_timings=dict(outcome.overlay_stage_timings),
        )

    bad = next((f for f in result.files if f.status is not FileStatus.COMMITTED), None)
    if bad is None:
        return ShellResult(  # pragma: no cover - success/failure mismatch guard
            result=outcome.stdout,
            exit_code=outcome.exit_code,
            changed_paths=changed_paths,
            warnings=tuple(outcome.warnings),
            overlay_run_timings=dict(outcome.overlay_run_timings),
            overlay_stage_timings=dict(outcome.overlay_stage_timings),
        )
    reason = "patch_failed" if bad.status is FileStatus.ABORTED_OVERLAP else bad.status.value
    return ShellResult(
        result=outcome.stdout,
        exit_code=outcome.exit_code,
        changed_paths=changed_paths,
        warnings=tuple(outcome.warnings),
        overlay_run_timings=dict(outcome.overlay_run_timings),
        overlay_stage_timings=dict(outcome.overlay_stage_timings),
        conflict=ConflictInfo(
            reason=reason,
            conflict_file=_absolutize(bad.path, workspace_root) if bad.path else None,
            message=bad.message or reason,
        ),
    )


def _absolutize(rel: str, workspace_root: str) -> str:
    if not rel:
        return rel
    if rel.startswith("/"):
        return rel
    root = workspace_root.rstrip("/")
    return f"{root}/{rel}" if root else rel


__all__ = ["shell_pipeline"]
