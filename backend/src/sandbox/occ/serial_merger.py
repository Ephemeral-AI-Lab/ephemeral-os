"""Global serial merger for prepared OCC commits."""

from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import threading
import time
from dataclasses import dataclass

from sandbox.occ.changeset.intent import PreparedChangeset
from sandbox.occ.changeset.types import ChangesetResult
from sandbox.occ.commit_transaction import OccCommitTransaction

_RESULT_READY_AT = "_occ.serial.result_ready_at_s"


@dataclass(frozen=True)
class _WorkItem:
    prepared: PreparedChangeset
    future: concurrent.futures.Future[ChangesetResult]
    enqueued_at: float


class OccSerialMerger:
    """Serialize OCC publish while batching disjoint prepared changesets."""

    def __init__(
        self,
        transaction: OccCommitTransaction,
        *,
        max_batch_size: int = 64,
        batch_window_s: float = 0.002,
    ) -> None:
        self._transaction = transaction
        self._max_batch_size = max(1, int(max_batch_size))
        self._batch_window_s = max(0.0, float(batch_window_s))
        self._queue: queue.Queue[_WorkItem] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run,
            name="occ-serial-merger",
            daemon=True,
        )
        self._thread.start()

    def submit(
        self,
        prepared: PreparedChangeset,
    ) -> concurrent.futures.Future[ChangesetResult]:
        future: concurrent.futures.Future[ChangesetResult] = (
            concurrent.futures.Future()
        )
        self._queue.put(
            _WorkItem(
                prepared=prepared,
                future=future,
                enqueued_at=time.perf_counter(),
            )
        )
        return future

    async def apply(self, prepared: PreparedChangeset) -> ChangesetResult:
        return await asyncio.wrap_future(self.submit(prepared))

    def apply_sync(self, prepared: PreparedChangeset) -> ChangesetResult:
        return self.submit(prepared).result()

    def _run(self) -> None:
        while True:
            first = self._queue.get()
            items = [first]
            if self._batch_window_s > 0:
                time.sleep(self._batch_window_s)
            while len(items) < self._max_batch_size:
                try:
                    items.append(self._queue.get_nowait())
                except queue.Empty:
                    break

            pending = [item for item in items if not item.future.cancelled()]
            for batch in _disjoint_batches(pending):
                self._commit_batch(batch)

    def _commit_batch(self, batch: list[_WorkItem]) -> None:
        if not batch:
            return
        commit_start = time.perf_counter()
        try:
            combined = _combine_prepared([item.prepared for item in batch])
            result = self._transaction.revalidate_and_publish(combined)
            commit_elapsed = time.perf_counter() - commit_start
            ready_at = time.perf_counter()
            for item in batch:
                if item.future.cancelled():
                    continue
                paths = _path_set(item.prepared)
                files = tuple(file for file in result.files if file.path in paths)
                item.future.set_result(
                    ChangesetResult(
                        files=files,
                        timings={
                            **item.prepared.timings,
                            **result.timings,
                            "occ.serial.queue_wait_s": commit_start
                            - item.enqueued_at,
                            "occ.serial.batch_size": float(len(batch)),
                            "occ.serial.commit_s": commit_elapsed,
                            _RESULT_READY_AT: ready_at,
                        },
                        published_manifest_version=result.published_manifest_version,
                    )
                )
        except BaseException as exc:
            for item in batch:
                if not item.future.cancelled():
                    item.future.set_exception(exc)


def _disjoint_batches(items: list[_WorkItem]) -> list[list[_WorkItem]]:
    batches: list[list[_WorkItem]] = []
    pending = list(items)
    while pending:
        used_paths: set[str] = set()
        batch: list[_WorkItem] = []
        rest: list[_WorkItem] = []
        for item in pending:
            paths = _path_set(item.prepared)
            if item.prepared.atomic or used_paths.intersection(paths):
                rest.append(item)
                continue
            batch.append(item)
            used_paths.update(paths)
        if batch:
            batches.append(batch)
            pending = rest
            continue
        batches.append([pending.pop(0)])
    return batches


def _combine_prepared(items: list[PreparedChangeset]) -> PreparedChangeset:
    first = items[0]
    return PreparedChangeset(
        snapshot=first.snapshot,
        path_groups=tuple(
            group for prepared in items for group in prepared.path_groups
        ),
        atomic=any(prepared.atomic for prepared in items),
    )


def _path_set(prepared: PreparedChangeset) -> set[str]:
    return {group.path for group in prepared.path_groups}


__all__ = ["OccSerialMerger"]
