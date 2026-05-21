"""OCC commit queue batching regressions."""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.occ_change_helpers import write_change

from sandbox.layer_stack.changes import WriteLayerChange
from sandbox.layer_stack.stack import LayerStack
from sandbox.occ.changeset import CommitOptions, FileStatus
from sandbox.occ.commit_queue import CommitQueue
from sandbox.occ.commit_transaction import CommitTransaction
from sandbox.occ.content_hashing import ContentHasher
from sandbox.occ.service import OccService


class _Gitignore:
    def is_ignored(self, path: str) -> bool:
        del path
        return False

    def is_ignored_in_snapshot(self, path: str, _snapshot: object) -> bool:
        return self.is_ignored(path)


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


def test_overlay_capture_conflict_does_not_drop_batched_api_write(
    tmp_path: Path,
) -> None:
    stack = LayerStack(tmp_path / "stack")
    _publish(stack, tmp_path, "src/app.py", b"leased\n")
    stale_snapshot = stack.read_active_manifest()
    _publish(stack, tmp_path, "src/app.py", b"active\n")
    active_snapshot = stack.read_active_manifest()

    transaction = CommitTransaction(
        snapshot_reader=stack,
        staging=stack,
        publisher=stack,
    )
    queue = CommitQueue(transaction, batch_window_s=0.05)
    queue.start()
    service = OccService(
        gitignore=_Gitignore(),
        layer_stack=stack,
        transaction=transaction,
        commit_queue=queue,
    )

    overlay_prepared = service.prepare_changeset_sync(
        [
            write_change(
                path="src/app.py",
                source="overlay_capture",
                final_content=b"tracked shell\n",
            )
        ],
        snapshot=stale_snapshot,
        options=CommitOptions(atomic=False),
    )
    api_prepared = service.prepare_changeset_sync(
        [write_change(path="matrix/disjoint-a.txt", final_content=b"a\n")],
        snapshot=active_snapshot,
        options=CommitOptions(atomic=False),
    )

    async def _commit_both() -> tuple[object, object]:
        return await asyncio.gather(
            service.commit_prepared(overlay_prepared),
            service.commit_prepared(api_prepared),
        )

    try:
        overlay_result, api_result = asyncio.run(_commit_both())
    finally:
        queue.close()

    assert [file.status for file in overlay_result.files] == [
        FileStatus.ABORTED_VERSION
    ]
    assert [file.status for file in api_result.files] == [FileStatus.ACCEPTED]
    assert api_result.timings["occ.serial.batch_size"] == 1.0
    assert stack.read_bytes("src/app.py") == (b"active\n", True)
    assert stack.read_bytes("matrix/disjoint-a.txt") == (b"a\n", True)
