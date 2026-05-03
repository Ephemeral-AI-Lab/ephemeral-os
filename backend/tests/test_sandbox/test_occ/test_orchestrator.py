"""Tests for ChangesetOrchestrator routing (Step 2 of the OCC gate simplification)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from sandbox.occ.changeset.types import (
    BinaryChange,
    Change,
    FileResult,
    FileStatus,
    OpaqueDirChange,
    SymlinkChange,
    WriteChange,
)
from sandbox.occ.orchestrator import ChangesetOrchestrator


class _StubGitignore:
    def __init__(self, ignored: set[str] | None = None) -> None:
        self._ignored = ignored or set()
        self.calls: list[str] = []

    def is_ignored(self, path: str) -> bool:
        self.calls.append(path)
        return path in self._ignored


class _StubCoordinator:
    """Records the changes routed to it and returns COMMITTED FileResults."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.received: list[Change] = []

    async def apply(self, changes: Sequence[Change]) -> list[FileResult]:
        self.received.extend(changes)
        return [
            FileResult(path=change.path, status=FileStatus.COMMITTED, message=self.label)
            for change in changes
        ]


def _orchestrator(
    *, ignored: set[str] | None = None
) -> tuple[ChangesetOrchestrator, _StubCoordinator, _StubCoordinator]:
    direct = _StubCoordinator("direct")
    gated = _StubCoordinator("gated")
    orch = ChangesetOrchestrator(
        gitignore=_StubGitignore(ignored or set()),
        direct=direct,
        gated=gated,
    )
    return orch, direct, gated


def test_dotgit_changes_are_silently_dropped() -> None:
    orch, direct, gated = _orchestrator()
    changes: list[Change] = [
        WriteChange(path=".git/config", base_hash="", base_existed=False, final_content="x"),
        WriteChange(path=".git", base_hash="", base_existed=False, final_content="x"),
        WriteChange(path=".gitignore", base_hash="", base_existed=False, final_content="x"),
    ]
    result = asyncio.run(orch.apply(changes))
    # .git/config and .git are dropped; .gitignore is a regular file and stays.
    assert {f.path for f in result.files} == {".gitignore"}
    assert direct.received == []
    assert len(gated.received) == 1


def test_direct_change_kinds_always_route_to_direct() -> None:
    orch, direct, gated = _orchestrator()
    changes: list[Change] = [
        SymlinkChange(path="link", target="/x"),
        OpaqueDirChange(path="dir", kept_children=frozenset()),
        BinaryChange(path="bin/data.dat", final_bytes=b"\xff"),
    ]
    asyncio.run(orch.apply(changes))
    assert {c.path for c in direct.received} == {"link", "dir", "bin/data.dat"}
    assert gated.received == []


def test_gitignored_gated_change_routes_to_direct() -> None:
    orch, direct, gated = _orchestrator(ignored={"build/out.o"})
    change = WriteChange(
        path="build/out.o",
        base_hash="",
        base_existed=False,
        final_content="content",
    )
    asyncio.run(orch.apply([change]))
    assert direct.received == [change]
    assert gated.received == []


def test_external_path_routes_to_direct() -> None:
    orch, direct, gated = _orchestrator()
    abs_change = WriteChange(
        path="/etc/passwd",
        base_hash="",
        base_existed=False,
        final_content="x",
    )
    parent_change = WriteChange(
        path="../escape.txt",
        base_hash="",
        base_existed=False,
        final_content="x",
    )
    asyncio.run(orch.apply([abs_change, parent_change]))
    assert direct.received == [abs_change, parent_change]
    assert gated.received == []


def test_workspace_relative_gated_change_routes_to_gated() -> None:
    orch, direct, gated = _orchestrator()
    change = WriteChange(
        path="src/a.py",
        base_hash="",
        base_existed=False,
        final_content="hi",
    )
    asyncio.run(orch.apply([change]))
    assert gated.received == [change]
    assert direct.received == []


def test_orchestrator_returns_combined_result_set() -> None:
    orch, direct, gated = _orchestrator(ignored={"build/out.o"})
    changes: list[Change] = [
        SymlinkChange(path="link", target="/x"),  # direct (kind)
        WriteChange(  # direct (gitignored)
            path="build/out.o",
            base_hash="",
            base_existed=False,
            final_content="ignored",
        ),
        WriteChange(  # gated
            path="src/a.py",
            base_hash="",
            base_existed=False,
            final_content="ok",
        ),
    ]
    result = asyncio.run(orch.apply(changes))
    assert all(f.status is FileStatus.COMMITTED for f in result.files)
    paths = {f.path for f in result.files}
    assert paths == {"link", "build/out.o", "src/a.py"}
    assert result.success is True


def test_empty_changeset_returns_empty_success() -> None:
    orch, *_ = _orchestrator()
    result = asyncio.run(orch.apply([]))
    assert result.files == ()
    assert result.success is True
