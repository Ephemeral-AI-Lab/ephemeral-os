"""Unit tests for service-level delete_file / move_file OCC facade ops."""

from __future__ import annotations

import pytest
from sandbox.code_intelligence.service import CodeIntelligenceService
from sandbox.code_intelligence.registry import dispose_all_code_intelligence
from sandbox.code_intelligence.core.types import MoveSpec


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    dispose_all_code_intelligence()
    yield
    dispose_all_code_intelligence()


def _svc(tmp_path) -> CodeIntelligenceService:
    return CodeIntelligenceService(
        sandbox_id=f"sandbox-operation-{tmp_path.name}",
        workspace_root=str(tmp_path),
    )


# ---------------------------------------------------------------------------
# Service-level delete_file / move_file (OCC-gated facade)
# ---------------------------------------------------------------------------


def test_delete_file_removes_existing_file(tmp_path) -> None:
    a = tmp_path / "d.py"
    a.write_text("x = 1\n", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.delete_file([str(a)])
    assert result.success is True
    assert result.status == "committed"
    assert not a.exists()


def test_delete_file_reports_not_found(tmp_path) -> None:
    svc = _svc(tmp_path)
    result = svc.delete_file([str(tmp_path / "missing.py")])
    assert result.success is False
    assert result.status == "failed"
    assert result.conflict_reason == "not_found"


def test_move_file_creates_new_destination(tmp_path) -> None:
    src = tmp_path / "src.py"
    dst = tmp_path / "dst.py"
    src.write_text("payload\n", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.move_file([MoveSpec(src_path=str(src), dst_path=str(dst))])
    assert result.success is True
    assert result.status == "committed"
    assert not src.exists()
    assert dst.read_text(encoding="utf-8") == "payload\n"


def test_move_file_rejects_existing_dst_without_overwrite(tmp_path) -> None:
    src = tmp_path / "src.py"
    dst = tmp_path / "dst.py"
    src.write_text("one\n", encoding="utf-8")
    dst.write_text("two\n", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.move_file([MoveSpec(src_path=str(src), dst_path=str(dst))])
    assert result.success is False
    assert result.conflict_reason == "dst_exists"
    # No partial move
    assert src.read_text(encoding="utf-8") == "one\n"
    assert dst.read_text(encoding="utf-8") == "two\n"


def test_move_file_overwrites_when_allowed(tmp_path) -> None:
    src = tmp_path / "src.py"
    dst = tmp_path / "dst.py"
    src.write_text("one\n", encoding="utf-8")
    dst.write_text("two\n", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.move_file(
        [MoveSpec(src_path=str(src), dst_path=str(dst), overwrite=True)],
    )
    assert result.success is True
    assert not src.exists()
    assert dst.read_text(encoding="utf-8") == "one\n"


def test_move_file_overwrite_aborts_on_dst_drift(tmp_path) -> None:
    """strict_base on the dst change forbids silent merges of concurrent dst edits."""
    src = tmp_path / "src.py"
    dst = tmp_path / "dst.py"
    src.write_text("one\n", encoding="utf-8")
    dst.write_text("two\n", encoding="utf-8")
    svc = _svc(tmp_path)

    original_read_many = svc._content.read_many

    def _drift_read_many(paths, *, allow_missing: bool = False):
        result = original_read_many(paths, allow_missing=allow_missing)
        # After move_file captures dst, corrupt it before commit acquires locks.
        if str(dst) in paths:
            dst.write_text("drift!\n", encoding="utf-8")
        return result

    svc._content.read_many = _drift_read_many  # type: ignore[assignment]
    try:
        result = svc.move_file(
            [MoveSpec(src_path=str(src), dst_path=str(dst), overwrite=True)],
        )
    finally:
        svc._content.read_many = original_read_many  # type: ignore[assignment]

    assert result.success is False
    assert result.status == "aborted_version"
    # Neither src nor dst mutated: src preserved, dst has the drifted content.
    assert src.read_text(encoding="utf-8") == "one\n"
    assert dst.read_text(encoding="utf-8") == "drift!\n"


def test_move_file_identical_paths_rejected(tmp_path) -> None:
    svc = _svc(tmp_path)
    a = tmp_path / "same.py"
    a.write_text("x\n", encoding="utf-8")
    result = svc.move_file([MoveSpec(src_path=str(a), dst_path=str(a))])
    assert result.success is False
    assert result.conflict_reason == "identical_paths"


def test_move_file_missing_src_reports_not_found(tmp_path) -> None:
    svc = _svc(tmp_path)
    result = svc.move_file(
        [
            MoveSpec(
                src_path=str(tmp_path / "missing.py"),
                dst_path=str(tmp_path / "dst.py"),
            ),
        ],
    )
    assert result.success is False
    assert result.conflict_reason == "not_found"
