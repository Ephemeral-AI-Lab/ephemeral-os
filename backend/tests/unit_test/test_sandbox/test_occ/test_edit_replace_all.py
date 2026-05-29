"""Site A (OCC) ``replace_all`` behavior for ``_apply_edit_content``."""

from __future__ import annotations

from pathlib import Path

from sandbox.layer_stack.changes import LayerChange, WriteLayerChange
from sandbox.layer_stack.stack import LayerStack
from sandbox.occ.changeset import (
    EditChange,
    FileResult,
    FileStatus,
    PreparedPathGroup,
    RouteDecision,
)
from sandbox.occ.content_hashing import ContentHasher
from sandbox.occ.path_staging import DirectStager, _apply_edit_content


def _edit(old: str, new: str, *, replace_all: bool = False) -> EditChange:
    return EditChange(path="f.txt", old_text=old, new_text=new, replace_all=replace_all)


def _publish(stack: LayerStack, tmp_path: Path, rel: str, content: bytes) -> None:
    source = tmp_path / "sources" / rel.replace("/", "-")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)
    stack.publish_changes(
        [
            WriteLayerChange(
                path=rel,
                content_hash=ContentHasher().hash_bytes(content),
                source_path=str(source),
            )
        ]
    )


def _stage_write(tmp_path: Path):
    counter = 0

    def stage(path: str, content: bytes) -> LayerChange:
        nonlocal counter
        counter += 1
        source = tmp_path / "sources" / f"staged-{counter}.bin"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content)
        return WriteLayerChange(
            path=path,
            content_hash=ContentHasher().hash_bytes(content),
            source_path=str(source),
        )

    return stage


def test_replace_all_replaces_every_occurrence_returns_bytes() -> None:
    content = b"a a a\n"
    result = _apply_edit_content("f.txt", content, _edit("a", "b", replace_all=True))
    assert result == b"b b b\n"


def test_replace_all_anchor_absent_aborts_overlap() -> None:
    result = _apply_edit_content(
        "f.txt", b"alpha\n", _edit("missing", "x", replace_all=True)
    )
    assert isinstance(result, FileResult)
    assert result.status is FileStatus.ABORTED_OVERLAP
    assert result.message == "anchor not found"


def test_default_mode_occurrence_mismatch_unchanged_message() -> None:
    # replace_all omitted: the default unique-match path still aborts an
    # over-count with the existing message.
    result = _apply_edit_content("f.txt", b"a a\n", _edit("a", "b"))
    assert isinstance(result, FileResult)
    assert result.status is FileStatus.ABORTED_OVERLAP
    assert result.message == "anchor occurrence count mismatch"


def test_default_mode_unique_match_replaces_once() -> None:
    result = _apply_edit_content("f.txt", b"a foo b\n", _edit("foo", "bar"))
    assert result == b"a bar b\n"


def test_multi_edit_group_aborts_atomically_when_second_edit_fails(
    tmp_path: Path,
) -> None:
    """A path group whose 2nd EditChange fails leaves nothing staged."""
    stack = LayerStack(tmp_path / "stack")
    _publish(stack, tmp_path, "f.txt", b"x x\n")
    stager = DirectStager(stack)
    group = PreparedPathGroup(
        path="f.txt",
        route=RouteDecision.DIRECT,
        changes=(
            EditChange(path="f.txt", old_text="x", new_text="y", replace_all=True),
            EditChange(path="f.txt", old_text="missing", new_text="z"),
        ),
    )

    result, delta = stager.stage_group(
        group,
        active_manifest=stack.read_active_manifest(),
        stage_write=_stage_write(tmp_path),
    )

    assert result.status is FileStatus.ABORTED_OVERLAP
    assert result.message == "anchor not found"
    assert delta is None
    # First edit's replace_all result was never published.
    assert stack.read_text("f.txt") == ("x x\n", True)
