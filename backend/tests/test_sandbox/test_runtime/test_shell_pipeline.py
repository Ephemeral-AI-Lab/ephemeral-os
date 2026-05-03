"""Tests for shell pipeline overlay/OCC composition."""

from __future__ import annotations

from sandbox.occ.changeset.types import (
    ChangesetResult,
    FileResult,
    FileStatus,
)
from sandbox.overlay.types import OverlayRunOutcome, UpperChange
from sandbox.runtime.pipelines import shell_pipeline


class _Overlay:
    def __init__(self, outcome: OverlayRunOutcome) -> None:
        self.outcome = outcome
        self.calls: list[str] = []

    async def execute(self, command: str, **kwargs):
        del kwargs
        self.calls.append(command)
        return self.outcome


class _Orchestrator:
    """Fake orchestrator returning a canned :class:`ChangesetResult`."""

    def __init__(self, result: ChangesetResult) -> None:
        self.result = result
        self.received: list[tuple[object, ...]] = []

    async def apply(self, changes):
        self.received.append(tuple(changes))
        return self.result


def _success_outcome() -> OverlayRunOutcome:
    return OverlayRunOutcome(
        exit_code=0,
        stdout="ran\n",
        upper_changes=(
            UpperChange(
                rel="app.py",
                kind="regular",
                base_bytes=b"old\n",
                upper_bytes=b"new\n",
                base_existed=True,
            ),
        ),
    )


async def test_shell_pipeline_projects_committed_paths_through_orchestrator() -> None:
    overlay = _Overlay(_success_outcome())
    orchestrator = _Orchestrator(
        ChangesetResult(
            files=(
                FileResult(path="app.py", status=FileStatus.COMMITTED),
            ),
        )
    )

    result = await shell_pipeline(
        command="printf ok",
        overlay_engine=overlay,
        orchestrator=orchestrator,
        agent_id="agent-a",
    )

    assert orchestrator.received  # the orchestrator received the typed changes
    assert result.changed_paths == ("/workspace/app.py",)
    assert result.conflict is None


async def test_shell_pipeline_surfaces_conflict_from_orchestrator() -> None:
    overlay = _Overlay(_success_outcome())
    orchestrator = _Orchestrator(
        ChangesetResult(
            files=(
                FileResult(
                    path="app.py",
                    status=FileStatus.ABORTED_VERSION,
                    message="content changed",
                ),
            ),
        )
    )

    result = await shell_pipeline(
        command="printf ok",
        overlay_engine=overlay,
        orchestrator=orchestrator,
    )

    assert result.changed_paths == ()
    assert result.conflict is not None
    assert result.conflict.reason == "aborted_version"
    assert result.conflict.conflict_file == "/workspace/app.py"
    assert result.conflict.message == "content changed"


async def test_shell_pipeline_aborted_overlap_maps_to_patch_failed() -> None:
    overlay = _Overlay(_success_outcome())
    orchestrator = _Orchestrator(
        ChangesetResult(
            files=(
                FileResult(
                    path="app.py",
                    status=FileStatus.ABORTED_OVERLAP,
                    message="anchor not found",
                ),
            ),
        )
    )

    result = await shell_pipeline(
        command="printf ok",
        overlay_engine=overlay,
        orchestrator=orchestrator,
    )

    assert result.conflict is not None
    assert result.conflict.reason == "patch_failed"
    assert result.conflict.message == "anchor not found"
