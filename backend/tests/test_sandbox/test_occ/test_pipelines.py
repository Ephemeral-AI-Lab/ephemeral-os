"""Tests for OCC runtime pipelines."""

from __future__ import annotations

from pathlib import Path

from sandbox.occ.commit.coordinator import WriteCoordinator
from sandbox.occ.patching.patcher import SearchReplaceEdit
from sandbox.occ.state.ledger_store import LedgerStore, state_dir
from sandbox.occ.types import EditSpec, WriteSpec
from sandbox.runtime.pipelines import edit_pipeline, write_pipeline


def test_write_pipeline_writes_and_commits_in_process(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "ws"
    workspace.mkdir()

    result = write_pipeline(
        WriteSpec(file_path=str(workspace / "a.txt"), content="hello\n"),
        workspace_root=str(workspace),
        agent_id="agent-a",
    )

    assert result.success is True
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "hello\n"


def test_edit_pipeline_batches_multiple_edits_into_one_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("alpha\n", encoding="utf-8")
    (workspace / "b.txt").write_text("bravo\n", encoding="utf-8")
    original = WriteCoordinator.commit_operation_against_base
    calls: list[int] = []

    def spy(self, changes, **kwargs):
        calls.append(len(changes))
        return original(self, changes, **kwargs)

    monkeypatch.setattr(WriteCoordinator, "commit_operation_against_base", spy)

    result = edit_pipeline(
        [
            EditSpec(
                file_path=str(workspace / "a.txt"),
                edits=(SearchReplaceEdit("alpha", "ALPHA"),),
            ),
            EditSpec(
                file_path=str(workspace / "b.txt"),
                edits=(SearchReplaceEdit("bravo", "BRAVO"),),
            ),
        ],
        workspace_root=str(workspace),
        agent_id="agent-a",
    )

    assert result.success is True
    assert calls == [2]
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "ALPHA\n"
    assert (workspace / "b.txt").read_text(encoding="utf-8") == "BRAVO\n"


def test_edit_pipeline_conflict_leaves_ledger_and_file_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("alpha\n", encoding="utf-8")

    result = edit_pipeline(
        EditSpec(
            file_path=str(target),
            edits=(SearchReplaceEdit("missing", "new"),),
        ),
        workspace_root=str(workspace),
        agent_id="agent-a",
    )

    ledger = LedgerStore(state_dir(str(workspace)))
    try:
        assert result.success is False
        assert result.conflict_reason == "patch_failed"
        assert result.conflict_file == str(target)
        assert target.read_text(encoding="utf-8") == "alpha\n"
        assert ledger.changes_since(0) == []
    finally:
        ledger.close()
