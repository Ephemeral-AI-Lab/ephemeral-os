"""Tests for the typed batch mutation APIs on :class:`CodeIntelligenceService`.

These exercise the shape contract (single tool call → one OCC batch →
one :class:`OperationResult`) for ``svc.write_file``, ``svc.edit_file``,
``svc.rename_symbol``, and the broadened ``svc.delete_file`` /
``svc.move_file``. Low-level commit semantics (drift, strict-base,
sorted locking) stay covered by ``test_write_coordinator_batch.py``;
here we check how the service layer feeds the coordinator.
"""

from __future__ import annotations

import pytest
from code_intelligence.editing.patcher import SearchReplaceEdit
from code_intelligence.routing.service import (
    CodeIntelligenceService,
    CommitSpecRequest,
    RenamePlanRequest,
    dispose_all_code_intelligence,
)
from code_intelligence.types import EditSpec, MoveSpec, ReferenceInfo, WriteSpec


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


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


def test_write_file_creates_new_file(tmp_path) -> None:
    target = tmp_path / "new.py"
    svc = _svc(tmp_path)

    result = svc.write_file(
        [WriteSpec(file_path=str(target), content="x = 1\n", overwrite=False)],
    )

    assert result.success
    assert result.status == "committed"
    assert target.read_text(encoding="utf-8") == "x = 1\n"


def test_write_file_refuses_to_clobber_when_overwrite_false(tmp_path) -> None:
    target = tmp_path / "exists.py"
    target.write_text("old\n", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.write_file(
        [WriteSpec(file_path=str(target), content="new\n", overwrite=False)],
    )

    assert not result.success
    assert result.status == "aborted_version"
    assert target.read_text(encoding="utf-8") == "old\n"


def test_write_file_overwrites_existing_with_strict_base(tmp_path) -> None:
    target = tmp_path / "update.py"
    target.write_text("old\n", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.write_file(
        [WriteSpec(file_path=str(target), content="new\n", overwrite=True)],
    )

    assert result.success
    assert target.read_text(encoding="utf-8") == "new\n"


def test_write_file_accepts_bare_spec(tmp_path) -> None:
    """Ergonomic: a single WriteSpec may be passed without wrapping in a list."""
    target = tmp_path / "solo.py"
    svc = _svc(tmp_path)

    result = svc.write_file(
        WriteSpec(file_path=str(target), content="ok\n", overwrite=False),
    )

    assert result.success
    assert target.read_text(encoding="utf-8") == "ok\n"


def test_write_file_batch_is_atomic(tmp_path) -> None:
    """Existing file in a create-only batch aborts every slot."""
    new_path = tmp_path / "new.py"
    clobber = tmp_path / "clobber.py"
    clobber.write_text("original\n", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.write_file(
        [
            WriteSpec(file_path=str(new_path), content="x\n", overwrite=False),
            WriteSpec(file_path=str(clobber), content="y\n", overwrite=False),
        ],
    )

    assert not result.success
    assert result.status == "aborted_version"
    assert not new_path.exists(), "first slot must not land when second aborts"
    assert clobber.read_text(encoding="utf-8") == "original\n"


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------


def test_edit_file_applies_search_replace(tmp_path) -> None:
    target = tmp_path / "config.py"
    target.write_text("debug = False\n", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.edit_file(
        [
            EditSpec(
                file_path=str(target),
                edits=[SearchReplaceEdit(old_text="False", new_text="True")],
            ),
        ],
    )

    assert result.success
    assert target.read_text(encoding="utf-8") == "debug = True\n"


def test_edit_file_not_found_is_surfaced(tmp_path) -> None:
    svc = _svc(tmp_path)

    result = svc.edit_file(
        [
            EditSpec(
                file_path=str(tmp_path / "missing.py"),
                edits=[SearchReplaceEdit(old_text="a", new_text="b")],
            ),
        ],
    )

    assert not result.success
    assert result.status == "failed"
    assert result.conflict_reason == "not_found"


def test_edit_file_missing_search_text_aborts_before_commit(tmp_path) -> None:
    target = tmp_path / "c.py"
    target.write_text("alpha\n", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.edit_file(
        [
            EditSpec(
                file_path=str(target),
                edits=[SearchReplaceEdit(old_text="beta", new_text="gamma")],
            ),
        ],
    )

    assert not result.success
    assert result.conflict_reason == "patch_failed"
    assert target.read_text(encoding="utf-8") == "alpha\n"


def test_edit_file_batch_is_atomic_across_specs(tmp_path) -> None:
    """One spec with unfindable text aborts; the other file stays untouched."""
    good = tmp_path / "good.py"
    bad = tmp_path / "bad.py"
    good.write_text("apple\n", encoding="utf-8")
    bad.write_text("banana\n", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.edit_file(
        [
            EditSpec(
                file_path=str(good),
                edits=[SearchReplaceEdit(old_text="apple", new_text="apricot")],
            ),
            EditSpec(
                file_path=str(bad),
                edits=[SearchReplaceEdit(old_text="cherry", new_text="blueberry")],
            ),
        ],
    )

    assert not result.success
    assert good.read_text(encoding="utf-8") == "apple\n"
    assert bad.read_text(encoding="utf-8") == "banana\n"


# ---------------------------------------------------------------------------
# delete_file (batch + legacy shim)
# ---------------------------------------------------------------------------


def test_delete_file_single_item_list(tmp_path) -> None:
    """Single-item list is the canonical shape for one-file delete."""
    target = tmp_path / "gone.py"
    target.write_text("goodbye\n", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.delete_file([str(target)])

    assert result.success
    assert not target.exists()


def test_delete_file_batch_is_atomic(tmp_path) -> None:
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("1", encoding="utf-8")
    b.write_text("2", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.delete_file([str(a), str(b)])

    assert result.success
    assert not a.exists()
    assert not b.exists()


def test_delete_file_batch_aborts_on_missing_sibling(tmp_path) -> None:
    """A missing path in a batch doesn't half-delete the surviving paths."""
    present = tmp_path / "present.py"
    present.write_text("1", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.delete_file([str(present), str(tmp_path / "missing.py")])

    assert not result.success
    assert result.conflict_reason == "not_found"
    assert present.exists(), "surviving path must not be deleted"


# ---------------------------------------------------------------------------
# move_file (batch + legacy shim)
# ---------------------------------------------------------------------------


def test_move_file_single_spec(tmp_path) -> None:
    """A single :class:`MoveSpec` is accepted without wrapping in a list."""
    src = tmp_path / "src.py"
    dst = tmp_path / "dst.py"
    src.write_text("content\n", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.move_file(MoveSpec(src_path=str(src), dst_path=str(dst)))

    assert result.success
    assert not src.exists()
    assert dst.read_text(encoding="utf-8") == "content\n"


def test_move_file_batch_accepts_movespec_list(tmp_path) -> None:
    src_a = tmp_path / "a.py"
    src_b = tmp_path / "b.py"
    dst_a = tmp_path / "moved_a.py"
    dst_b = tmp_path / "moved_b.py"
    src_a.write_text("A", encoding="utf-8")
    src_b.write_text("B", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.move_file(
        [
            MoveSpec(src_path=str(src_a), dst_path=str(dst_a)),
            MoveSpec(src_path=str(src_b), dst_path=str(dst_b)),
        ],
    )

    assert result.success
    assert not src_a.exists() and not src_b.exists()
    assert dst_a.read_text(encoding="utf-8") == "A"
    assert dst_b.read_text(encoding="utf-8") == "B"


def test_move_file_overwrite_uses_strict_base(tmp_path) -> None:
    src = tmp_path / "src.py"
    dst = tmp_path / "dst.py"
    src.write_text("NEW\n", encoding="utf-8")
    dst.write_text("OLD\n", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.move_file(
        [MoveSpec(src_path=str(src), dst_path=str(dst), overwrite=True)],
    )

    assert result.success
    assert dst.read_text(encoding="utf-8") == "NEW\n"


def test_move_file_batch_rejects_identical_paths(tmp_path) -> None:
    src = tmp_path / "self.py"
    src.write_text("x", encoding="utf-8")
    svc = _svc(tmp_path)

    result = svc.move_file([MoveSpec(src_path=str(src), dst_path=str(src))])

    assert not result.success
    assert result.conflict_reason == "identical_paths"
    assert src.exists()


# ---------------------------------------------------------------------------
# rename_symbol (service-level wrapper)
# ---------------------------------------------------------------------------


def test_rename_symbol_returns_empty_committed_when_plan_has_no_changes(
    tmp_path,
    monkeypatch,
) -> None:
    svc = _svc(tmp_path)
    from code_intelligence.types import SemanticRenamePlan

    empty_plan = SemanticRenamePlan(
        new_name="new",
        origin=(str(tmp_path / "f.py"), 1, 0),
        changes=(),
    )
    monkeypatch.setattr(svc, "rename_symbol_plan", lambda *a, **k: empty_plan)

    result = svc.rename_symbol(str(tmp_path / "f.py"), 1, 0, "new")

    assert result.success
    assert result.status == "committed"
    assert result.files == ()


def test_rename_symbol_submits_plan_changes_as_one_batch(tmp_path, monkeypatch) -> None:
    svc = _svc(tmp_path)
    from code_intelligence.hashing import content_hash
    from code_intelligence.types import (
        SemanticFileChange,
        SemanticRenamePlan,
    )

    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("use foo()\n", encoding="utf-8")
    b.write_text("def foo(): pass\n", encoding="utf-8")

    plan = SemanticRenamePlan(
        new_name="bar",
        origin=(str(b), 1, 4),
        changes=(
            SemanticFileChange(
                file_path=str(a),
                base_content="use foo()\n",
                base_hash=content_hash("use foo()\n"),
                final_content="use bar()\n",
            ),
            SemanticFileChange(
                file_path=str(b),
                base_content="def foo(): pass\n",
                base_hash=content_hash("def foo(): pass\n"),
                final_content="def bar(): pass\n",
            ),
        ),
    )
    monkeypatch.setattr(svc, "rename_symbol_plan", lambda *a, **k: plan)

    result = svc.rename_symbol(str(b), 1, 4, "bar")

    assert result.success
    assert result.status == "committed"
    assert a.read_text(encoding="utf-8") == "use bar()\n"
    assert b.read_text(encoding="utf-8") == "def bar(): pass\n"


def test_commit_rename_plan_does_not_recompute_jedi_plan(tmp_path, monkeypatch) -> None:
    svc = _svc(tmp_path)
    from code_intelligence.hashing import content_hash
    from code_intelligence.types import SemanticFileChange, SemanticRenamePlan

    target = tmp_path / "rename.py"
    target.write_text("def old(): pass\n", encoding="utf-8")
    plan = SemanticRenamePlan(
        new_name="new",
        origin=(str(target), 1, 4),
        changes=(
            SemanticFileChange(
                file_path=str(target),
                base_content="def old(): pass\n",
                base_hash=content_hash("def old(): pass\n"),
                final_content="def new(): pass\n",
            ),
        ),
    )
    monkeypatch.setattr(
        svc,
        "rename_symbol_plan",
        lambda *a, **k: pytest.fail("rename plan should not be recomputed"),
    )

    result = svc.commit_rename_plan(plan)

    assert result.success
    assert target.read_text(encoding="utf-8") == "def new(): pass\n"


def test_rename_symbol_plans_many_uses_same_file_fast_path(
    tmp_path,
    monkeypatch,
) -> None:
    one = tmp_path / "one.py"
    two = tmp_path / "two.py"
    one.write_text(
        "def alpha(value):\n    return alpha(value - 1)\n",
        encoding="utf-8",
    )
    two.write_text(
        "def beta(value):\n    return beta(value - 1)\n",
        encoding="utf-8",
    )
    svc = _svc(tmp_path)
    monkeypatch.setattr(
        svc.lsp_client,
        "find_references_many",
        lambda requests: pytest.fail("reference query should not run"),
    )
    monkeypatch.setattr(
        svc.lsp_client,
        "rename_symbols",
        lambda requests: pytest.fail("full Jedi rename should not run"),
    )

    plans = svc.rename_symbol_plans_many(
        [
            RenamePlanRequest(str(one), 1, 4, "renamed_alpha"),
            RenamePlanRequest(str(two), 1, 4, "renamed_beta"),
        ]
    )

    assert len(plans) == 2
    assert plans[0].changes[0].final_content == (
        "def renamed_alpha(value):\n    return renamed_alpha(value - 1)\n"
    )
    assert plans[1].changes[0].final_content == (
        "def renamed_beta(value):\n    return renamed_beta(value - 1)\n"
    )


def test_rename_symbol_plans_many_falls_back_when_reference_leaves_file(
    tmp_path,
    monkeypatch,
) -> None:
    core = tmp_path / "core.py"
    use = tmp_path / "use.py"
    local = tmp_path / "local.py"
    core.write_text("def alpha(value):\n    return value\n", encoding="utf-8")
    use.write_text("from core import alpha\nresult = alpha(1)\n", encoding="utf-8")
    local.write_text("def beta(value):\n    return beta(value - 1)\n", encoding="utf-8")
    svc = _svc(tmp_path)
    monkeypatch.setattr(
        svc.lsp_client,
        "find_references_many",
        lambda requests: [
            [
                ReferenceInfo(file_path=str(core), line=1, character=4),
                ReferenceInfo(file_path=str(use), line=1, character=17),
                ReferenceInfo(file_path=str(use), line=2, character=9),
            ],
            [
                ReferenceInfo(file_path=str(local), line=1, character=4),
                ReferenceInfo(file_path=str(local), line=2, character=11),
            ],
        ],
    )
    monkeypatch.setattr(
        svc.lsp_client,
        "rename_symbols",
        lambda requests: pytest.fail("full Jedi rename should not run"),
    )

    plans = svc.rename_symbol_plans_many(
        [
            RenamePlanRequest(str(core), 1, 4, "renamed_alpha"),
            RenamePlanRequest(str(local), 1, 4, "renamed_beta"),
        ]
    )

    assert len(plans) == 2
    final_by_path = {change.file_path: change.final_content for change in plans[0].changes}
    assert final_by_path[str(core)] == "def renamed_alpha(value):\n    return value\n"
    assert final_by_path[str(use)] == (
        "from core import renamed_alpha\nresult = renamed_alpha(1)\n"
    )
    assert plans[1].changes[0].final_content == (
        "def renamed_beta(value):\n    return renamed_beta(value - 1)\n"
    )


def test_commit_specs_many_batches_mixed_disjoint_ops(tmp_path) -> None:
    edit_target = tmp_path / "edit.py"
    delete_target = tmp_path / "delete.py"
    move_src = tmp_path / "move_src.py"
    move_dst = tmp_path / "move_dst.py"
    write_target = tmp_path / "write.py"
    edit_target.write_text("flag = False\n", encoding="utf-8")
    delete_target.write_text("remove me\n", encoding="utf-8")
    move_src.write_text("move me\n", encoding="utf-8")
    svc = _svc(tmp_path)

    results = svc.commit_specs_many(
        [
            CommitSpecRequest(
                op="write",
                specs=[
                    WriteSpec(file_path=str(write_target), content="created\n"),
                ],
                agent_id="writer",
            ),
            CommitSpecRequest(
                op="edit",
                specs=[
                    EditSpec(
                        file_path=str(edit_target),
                        edits=[SearchReplaceEdit(old_text="False", new_text="True")],
                    ),
                ],
                agent_id="editor",
            ),
            CommitSpecRequest(
                op="delete",
                specs=[str(delete_target)],
                agent_id="deleter",
            ),
            CommitSpecRequest(
                op="move",
                specs=[MoveSpec(src_path=str(move_src), dst_path=str(move_dst))],
                agent_id="mover",
            ),
        ]
    )

    assert [result.success for result in results] == [True, True, True, True]
    assert write_target.read_text(encoding="utf-8") == "created\n"
    assert edit_target.read_text(encoding="utf-8") == "flag = True\n"
    assert not delete_target.exists()
    assert not move_src.exists()
    assert move_dst.read_text(encoding="utf-8") == "move me\n"
