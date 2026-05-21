"""Single-path shell captures opt out of cross-path atomicity.

When the workspace upperdir capture from a guarded shell call yields
exactly one distinct path, ``CommitOptions.atomic`` is set to ``False``
so ``CommitQueue._disjoint_batches`` can coalesce concurrent shell
commits into a single revalidate-and-publish round-trip. Multi-path
captures keep ``atomic=True`` to preserve all-or-nothing semantics for
real workloads (e.g. ``make build``).
"""

from __future__ import annotations

from tests.occ_change_helpers import write_change

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from sandbox.daemon.service import sandbox_overlay
from sandbox.daemon.service.sandbox_overlay import SandboxOverlay
from sandbox.execution.contract import CommandExecRequest
from sandbox.occ.changeset import CommitOptions
from sandbox.occ.changeset import ChangesetResult, WriteChange

_CAPTURED_PATHS: list[str] = []


@dataclass
class _Manifest:
    version: int = 1


class _StubOccClient:
    """Captures the ``CommitOptions`` passed to ``apply_changeset``."""

    def __init__(self) -> None:
        self.captured_options: list[CommitOptions] = []

    async def apply_changeset(
        self,
        typed_changes: Any,
        *,
        snapshot: Any = None,
        options: CommitOptions | None = None,
        workspace_ref: str | None = None,
        run_maintenance: bool = True,
    ) -> ChangesetResult:
        del typed_changes, snapshot, workspace_ref, run_maintenance
        assert options is not None
        self.captured_options.append(options)
        return ChangesetResult(
            files=(),
            timings={},
            published_manifest_version=1,
        )


def _request() -> CommandExecRequest:
    return CommandExecRequest(
        request_id="atomic-by-path-test",
        workspace_ref="/tmp/eos-test-atomic",
        workspace_root="/testbed",
        command=("true",),
        actor_id="t",
        description="atomic-by-path",
    )


@pytest.fixture(autouse=True)
def _patch_workspace_to_occ(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass on-disk content readers; emit one ``WriteChange`` per path."""

    def fake_walk_upperdir(*args: Any, **kwargs: Any) -> list[str]:
        del args, kwargs
        return list(_CAPTURED_PATHS)

    def fake(path_changes: Any) -> tuple[WriteChange, ...]:
        return tuple(
            write_change(
                path=path,
                final_content=b"x",
                source="overlay_capture",
            )
            for path in path_changes
        )

    monkeypatch.setattr(sandbox_overlay, "walk_upperdir", fake_walk_upperdir)
    monkeypatch.setattr(sandbox_overlay, "overlay_path_changes_to_occ_changes", fake)


def _apply(client: _StubOccClient, paths: list[str]) -> None:
    _CAPTURED_PATHS[:] = paths
    overlay = SandboxOverlay(
        occ_client=client,  # type: ignore[arg-type]
        workspace_ref=_request().workspace_ref,
    )
    asyncio.run(
        overlay.publish_cycle(
            request=_request(),
            upperdir="/tmp/unused-upperdir",
            snapshot=_Manifest(),
        )
    )


def test_single_path_capture_passes_atomic_false() -> None:
    client = _StubOccClient()
    _apply(client, ["only/file.txt"])
    assert len(client.captured_options) == 1
    assert client.captured_options[0].atomic is False


def test_multi_path_capture_keeps_atomic_true() -> None:
    client = _StubOccClient()
    _apply(client, ["build/out.o", "build/out.so"])
    assert len(client.captured_options) == 1
    assert client.captured_options[0].atomic is True


def test_repeated_writes_to_one_path_are_single_path() -> None:
    """Two changes touching the same path → one distinct path → atomic=False."""
    client = _StubOccClient()
    _apply(client, ["dup/file.txt", "dup/file.txt"])
    assert client.captured_options[0].atomic is False
