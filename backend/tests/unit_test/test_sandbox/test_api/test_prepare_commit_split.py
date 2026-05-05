"""Phase 1 prepare-then-commit split for the runtime sandbox API."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sandbox.layer_stack import LayerChange, LayerStackManager
from sandbox.occ.changeset.builders import build_api_write_change
from sandbox.occ.changeset.prepared import CommitOptions, PreparedChangeset
from sandbox.occ.changeset.types import FileStatus
from sandbox.occ.content.hashing import ContentHasher
from sandbox.runtime import api_handlers


async def test_commit_prepared_publishes_when_no_intervening_commit(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    _, occ_service, _ = api_handlers._services(
        {"layer_stack_root": str(manager.storage_root)}
    )

    prepared = await occ_service.prepare_changeset(
        [build_api_write_change(path="src/a.py", final_content="hello\n")],
        options=CommitOptions(caller_id="agent", description="write a"),
    )
    assert isinstance(prepared, PreparedChangeset)

    result = await occ_service.commit_prepared(prepared)

    assert result.files[0].path == "src/a.py"
    assert result.files[0].status is FileStatus.ACCEPTED
    assert result.published_manifest_version is not None
    assert manager.read_text("src/a.py") == ("hello\n", True)
    assert "occ.apply.commit_s" in result.timings
    assert "occ.apply.total_s" in result.timings


async def test_commit_prepared_with_stale_snapshot_succeeds_on_disjoint_path(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    _publish(manager, tmp_path, "config.yaml", b"v1\n")
    _, occ_service, _ = api_handlers._services(
        {"layer_stack_root": str(manager.storage_root)}
    )

    prepared = await occ_service.prepare_changeset(
        [build_api_write_change(path="src/new.py", final_content="x\n")],
        options=CommitOptions(caller_id="agent", description="disjoint write"),
    )
    snapshot_at_prepare = prepared.snapshot
    assert snapshot_at_prepare is not None

    # Simulate an intervening commit on an *unrelated* path between prepare
    # and commit. The serial merger validates against the live active
    # manifest under the lock, so the disjoint path must still ACCEPT.
    _publish(manager, tmp_path, "unrelated/file.txt", b"intervening\n")
    assert manager.read_active_manifest().version > snapshot_at_prepare.version

    result = await occ_service.commit_prepared(prepared)

    assert result.files[0].status is FileStatus.ACCEPTED
    assert manager.read_text("src/new.py") == ("x\n", True)
    assert result.timings.get("occ.apply.manifest_lag", 0) >= 1


async def test_commit_prepared_with_stale_snapshot_aborts_on_overlap(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    _publish(manager, tmp_path, "src/shared.py", b"base\n")
    _, occ_service, _ = api_handlers._services(
        {"layer_stack_root": str(manager.storage_root)}
    )

    prepared = await occ_service.prepare_changeset(
        [build_api_write_change(path="src/shared.py", final_content="staged\n")],
        options=CommitOptions(caller_id="agent", description="overlap write"),
    )

    # Concurrent commit on the same path advances the active manifest.
    _publish(manager, tmp_path, "src/shared.py", b"intervening\n")

    result = await occ_service.commit_prepared(prepared)

    assert result.files[0].status is FileStatus.ABORTED_VERSION
    assert manager.read_text("src/shared.py") == ("intervening\n", True)


async def test_commit_prepared_routes_gitignored_paths_through_skipped_merge(
    tmp_path: Path,
) -> None:
    manager = _new_manager(tmp_path)
    _publish(manager, tmp_path, ".gitignore", b"dist/\n")
    _, occ_service, _ = api_handlers._services(
        {"layer_stack_root": str(manager.storage_root)}
    )

    tracked = await occ_service.prepare_changeset(
        [build_api_write_change(path="src/keep.py", final_content="kept\n")],
        options=CommitOptions(caller_id="agent", description="tracked"),
    )
    ignored = await occ_service.prepare_changeset(
        [build_api_write_change(path="dist/build.js", final_content="built\n")],
        options=CommitOptions(caller_id="agent", description="ignored"),
    )

    tracked_result = await occ_service.commit_prepared(tracked)
    ignored_result = await occ_service.commit_prepared(ignored)

    assert tracked_result.files[0].status is FileStatus.ACCEPTED
    assert ignored_result.files[0].status is FileStatus.ACCEPTED
    # The gated/skipped routes record different per-path timing keys.
    assert any(
        key.startswith("occ.gated.") for key in tracked_result.files[0].timings
    )
    assert any(
        key.startswith("occ.direct.") for key in ignored_result.files[0].timings
    )
    assert manager.read_text("src/keep.py") == ("kept\n", True)
    assert manager.read_text("dist/build.js") == ("built\n", True)


def _new_manager(tmp_path: Path) -> LayerStackManager:
    return LayerStackManager(tmp_path / f"stack-{uuid4().hex}")


def _publish(
    manager: LayerStackManager,
    tmp_path: Path,
    rel: str,
    content: bytes,
) -> None:
    source = tmp_path / "sources" / rel.replace("/", "-")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)
    manager.publish_changes(
        [
            LayerChange(
                path=rel,
                kind="write",
                content_hash=ContentHasher().hash_bytes(content),
                source_path=str(source),
            )
        ]
    )
