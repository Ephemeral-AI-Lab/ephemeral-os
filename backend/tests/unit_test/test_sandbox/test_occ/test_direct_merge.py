"""Direct merge staging for Phase 04 OCC commits."""

from __future__ import annotations

from pathlib import Path

from tests.occ_change_helpers import write_change

from sandbox.layer_stack.changes import LayerChange, WriteLayerChange
from sandbox.layer_stack.stack import LayerStack
from sandbox.occ.changeset import (
    EditChange,
    FileStatus,
    OpaqueDirChange,
    PreparedPathGroup,
    RouteDecision,
    SymlinkChange,
    build_overlay_write_change,
)
from sandbox.occ.content_hashing import ContentHasher
from sandbox.occ.path_staging import DirectStager


def _source(tmp_path: Path, name: str, content: bytes) -> Path:
    path = tmp_path / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _publish(stack: LayerStack, tmp_path: Path, rel: str, content: bytes) -> None:
    source = _source(tmp_path, rel.replace("/", "-"), content)
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
        source = _source(tmp_path, f"direct-{counter}.bin", content)
        return WriteLayerChange(
            path=path,
            content_hash=ContentHasher().hash_bytes(content),
            source_path=str(source),
        )

    return stage


def test_direct_write_stages_last_writer_wins_content(tmp_path: Path) -> None:
    stack = LayerStack(tmp_path / "stack")
    merge = DirectStager(stack)
    group = PreparedPathGroup(
        path="dist/app.js",
        route=RouteDecision.DIRECT,
        changes=(
            write_change(path="dist/app.js", final_content=b"first"),
            write_change(path="dist/app.js", final_content=b"second"),
        ),
    )

    result, delta = merge.stage_group(
        group,
        active_manifest=stack.read_active_manifest(),
        stage_write=_stage_write(tmp_path),
    )

    assert result.status is FileStatus.ACCEPTED
    assert delta is not None
    [change] = delta
    assert Path(change.source_path or "").read_bytes() == b"second"


def test_direct_edit_rejects_missing_anchor(tmp_path: Path) -> None:
    """occ BL-01: DirectStager.EditChange must REJECT on anchor miss, matching
    GatedStager. The pre-fix `continue` silently accepted bogus edits on
    gitignored paths while tracked paths got rejected — a contract violation.
    """
    stack = LayerStack(tmp_path / "stack")
    _publish(stack, tmp_path, "dist/app.js", b"alpha\n")
    merge = DirectStager(stack)
    group = PreparedPathGroup(
        path="dist/app.js",
        route=RouteDecision.DIRECT,
        changes=(EditChange(path="dist/app.js", old_text="missing", new_text="X"),),
    )

    result, delta = merge.stage_group(
        group,
        active_manifest=stack.read_active_manifest(),
        stage_write=_stage_write(tmp_path),
    )

    assert result.status is FileStatus.ABORTED_OVERLAP
    assert result.message == "anchor not found"
    assert delta is None


def test_direct_symlink_and_opaque_dir_stage_storage_changes(tmp_path: Path) -> None:
    stack = LayerStack(tmp_path / "stack")
    merge = DirectStager(stack)
    symlink_group = PreparedPathGroup(
        path="link",
        route=RouteDecision.DIRECT,
        changes=(SymlinkChange(path="link", target="../target"),),
    )
    opaque_group = PreparedPathGroup(
        path="cache",
        route=RouteDecision.DIRECT,
        changes=(OpaqueDirChange(path="cache"),),
    )

    symlink_result, symlink_delta = merge.stage_group(
        symlink_group,
        active_manifest=stack.read_active_manifest(),
        stage_write=_stage_write(tmp_path),
    )
    opaque_result, opaque_delta = merge.stage_group(
        opaque_group,
        active_manifest=stack.read_active_manifest(),
        stage_write=_stage_write(tmp_path),
    )

    assert symlink_result.status is FileStatus.ACCEPTED
    assert symlink_delta is not None
    assert symlink_delta[0].kind == "symlink"
    assert symlink_delta[0].source_path == "../target"
    assert opaque_result.status is FileStatus.ACCEPTED
    assert opaque_delta is not None
    assert opaque_delta[0].kind == "opaque_dir"


def test_direct_same_path_opaque_dir_respects_later_write(
    tmp_path: Path,
) -> None:
    stack = LayerStack(tmp_path / "stack")
    merge = DirectStager(stack)
    group = PreparedPathGroup(
        path="cache",
        route=RouteDecision.DIRECT,
        changes=(
            OpaqueDirChange(path="cache"),
            write_change(path="cache", final_content=b"file wins"),
        ),
    )

    result, delta = merge.stage_group(
        group,
        active_manifest=stack.read_active_manifest(),
        stage_write=_stage_write(tmp_path),
    )

    assert result.status is FileStatus.ACCEPTED
    assert delta is not None
    [change] = delta
    assert change.kind == "write"
    assert Path(change.source_path or "").read_bytes() == b"file wins"


def test_direct_disk_backed_write_stages_without_materializing_bytes(
    tmp_path: Path,
) -> None:
    stack = LayerStack(tmp_path / "stack")
    merge = DirectStager(stack)
    content_path = _source(tmp_path, "large.bin", b"content")
    staged_cached_bytes: list[bytes | None] = []

    def stage_from_path(
        path: str,
        source_path: str,
        precomputed_hash: str,
        cached_bytes: bytes | None,
    ) -> LayerChange:
        staged_cached_bytes.append(cached_bytes)
        return WriteLayerChange(
            path=path,
            source_path=source_path,
            content_hash=precomputed_hash,
        )

    group = PreparedPathGroup(
        path="dist/large.bin",
        route=RouteDecision.DIRECT,
        changes=(
            build_overlay_write_change(
                path="dist/large.bin",
                content_path=str(content_path),
                precomputed_hash=ContentHasher().hash_bytes(b"content"),
            ),
        ),
    )

    result, delta = merge.stage_group(
        group,
        active_manifest=stack.read_active_manifest(),
        stage_write=_stage_write(tmp_path),
        stage_write_from_path=stage_from_path,
    )

    assert result.status is FileStatus.ACCEPTED
    assert delta is not None
    assert staged_cached_bytes == [None]
