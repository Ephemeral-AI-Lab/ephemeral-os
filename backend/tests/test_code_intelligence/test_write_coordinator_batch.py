"""Unit tests for WriteCoordinator.commit_many_against_base (atomic batch)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from code_intelligence.editing.write_coordinator import content_hash
from code_intelligence.routing.service import (
    CodeIntelligenceService,
    dispose_all_code_intelligence,
)
from code_intelligence.types import SemanticFileChange


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    dispose_all_code_intelligence()
    yield
    dispose_all_code_intelligence()


def _svc(tmp_path) -> CodeIntelligenceService:
    return CodeIntelligenceService(
        sandbox_id=f"sandbox-batch-{tmp_path.name}",
        workspace_root=str(tmp_path),
    )


def _change(path: str, base: str, final: str) -> SemanticFileChange:
    return SemanticFileChange(
        file_path=path,
        base_content=base,
        base_hash=content_hash(base),
        final_content=final,
    )


def test_commits_full_batch_on_clean_bases(tmp_path) -> None:
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("x = 1\n", encoding="utf-8")
    b.write_text("y = 2\n", encoding="utf-8")

    svc = _svc(tmp_path)
    result = svc.commit_many_against_base(
        [
            _change(str(a), "x = 1\n", "x = 11\n"),
            _change(str(b), "y = 2\n", "y = 22\n"),
        ],
        edit_type="rename",
        description="test",
    )
    assert result.success is True
    assert result.status == "committed"
    assert a.read_text(encoding="utf-8") == "x = 11\n"
    assert b.read_text(encoding="utf-8") == "y = 22\n"


def test_aborts_on_overlapping_concurrent_edit(tmp_path) -> None:
    a = tmp_path / "a.py"
    a.write_text("def foo():\n    return 1\n", encoding="utf-8")
    svc = _svc(tmp_path)

    base = "def foo():\n    return 1\n"
    final = "def bar():\n    return 1\n"
    # Concurrent drift: the same first line got edited.
    a.write_text("def foo_drift():\n    return 1\n", encoding="utf-8")

    result = svc.commit_many_against_base(
        [_change(str(a), base, final)],
        edit_type="rename",
    )
    assert result.success is False
    assert result.status in {"aborted_overlap", "aborted_version"}
    # Concurrent edit preserved, rename not applied.
    assert "foo_drift" in a.read_text(encoding="utf-8")


def test_merges_non_overlapping_concurrent_edit(tmp_path) -> None:
    a = tmp_path / "a.py"
    base = "def foo():\n    return 1\n\nZ = 0\n"
    a.write_text(base, encoding="utf-8")
    svc = _svc(tmp_path)

    # Jedi renamed foo → bar at the top; someone else appended an
    # unrelated line at the bottom after Jedi's snapshot.
    final = "def bar():\n    return 1\n\nZ = 0\n"
    a.write_text(base + "NEW = 1\n", encoding="utf-8")

    result = svc.commit_many_against_base(
        [_change(str(a), base, final)],
        edit_type="rename",
    )
    assert result.success is True, result.conflict_reason
    text = a.read_text(encoding="utf-8")
    assert "def bar()" in text
    assert "NEW = 1" in text  # concurrent edit preserved


def test_lsp_invalidate_and_symbol_index_refresh_per_committed_path(tmp_path) -> None:
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("x=1\n", encoding="utf-8")
    b.write_text("y=2\n", encoding="utf-8")

    svc = _svc(tmp_path)
    svc.lsp_client = MagicMock()
    svc.symbol_index = MagicMock()
    svc._write_coordinator._lsp_client = svc.lsp_client
    svc._write_coordinator._symbol_index = svc.symbol_index

    result = svc.commit_many_against_base(
        [
            _change(str(a), "x=1\n", "x=10\n"),
            _change(str(b), "y=2\n", "y=20\n"),
        ],
        edit_type="rename",
    )
    assert result.success
    invalidated = sorted(
        call.args[0] for call in svc.lsp_client.invalidate.call_args_list
    )
    refreshed = sorted(
        call.args[0] for call in svc.symbol_index.refresh.call_args_list
    )
    assert invalidated == sorted([str(a), str(b)])
    assert refreshed == sorted([str(a), str(b)])


def test_locks_acquired_in_sorted_order(tmp_path) -> None:
    a = tmp_path / "zzz.py"
    b = tmp_path / "aaa.py"
    c = tmp_path / "mmm.py"
    for p in (a, b, c):
        p.write_text("x=1\n", encoding="utf-8")

    svc = _svc(tmp_path)
    order: list[str] = []
    real_acquire = svc.arbiter.acquire_file_lock

    def _spy(path, *args, **kwargs):
        order.append(path)
        return real_acquire(path, *args, **kwargs)

    svc.arbiter.acquire_file_lock = _spy  # type: ignore[assignment]

    result = svc.commit_many_against_base(
        [
            _change(str(a), "x=1\n", "x=2\n"),
            _change(str(b), "x=1\n", "x=3\n"),
            _change(str(c), "x=1\n", "x=4\n"),
        ],
        edit_type="rename",
    )
    assert result.success
    assert order == sorted([str(a), str(b), str(c)])


def test_empty_changes_returns_committed_no_op(tmp_path) -> None:
    svc = _svc(tmp_path)
    result = svc.commit_many_against_base([], edit_type="rename")
    assert result.success is True
    assert result.status == "committed"
    assert result.files == ()
