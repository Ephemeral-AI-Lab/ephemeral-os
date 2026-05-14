"""OCC changeset preparation and commit service."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import cast

from sandbox.layer_stack.manifest import Manifest
from sandbox.occ.changeset.prepared import CommitOptions, PreparedChangeset
from sandbox.occ.changeset.types import Change, ChangesetResult
from sandbox.occ.commit_transaction import OccCommitTransaction
from sandbox.occ.content.gitignore_oracle import GitignoreMatcher
from sandbox.occ.content.hashing import infer_manifest_base_hash
from sandbox.occ.maintenance import (
    AutoSquashMaintenancePolicy,
    MaintenancePolicy,
    NoopMaintenancePolicy,
    SquashPort,
)
from sandbox.occ.merge.serial import OccSerialMerger
from sandbox.occ.ports import CommitPublisher, CommitStagingStore, SnapshotReader
from sandbox.occ.routing.orchestrator import OccOrchestrator
from sandbox.async_bridge import run_sync_in_executor
from sandbox.timing import monotonic_now

AUTO_SQUASH_MAX_DEPTH = 32


class OccService:
    """Prepare typed OCC changesets and commit them through the layer stack."""

    def __init__(
        self,
        *,
        gitignore: GitignoreMatcher,
        snapshot_reader: SnapshotReader | None = None,
        staging: CommitStagingStore | None = None,
        publisher: CommitPublisher | None = None,
        layer_stack: object | None = None,
        orchestrator: OccOrchestrator | None = None,
        transaction: OccCommitTransaction | None = None,
        serial_merger: OccSerialMerger | None = None,
        maintenance: MaintenancePolicy | None = None,
        auto_squash_max_depth: int = AUTO_SQUASH_MAX_DEPTH,
    ) -> None:
        if layer_stack is not None:
            snapshot_reader = snapshot_reader or cast(SnapshotReader, layer_stack)
            staging = staging or cast(CommitStagingStore, layer_stack)
            publisher = publisher or cast(CommitPublisher, layer_stack)
        if snapshot_reader is None or staging is None or publisher is None:
            raise TypeError(
                "OccService requires snapshot_reader, staging, and publisher ports"
            )
        self._snapshot_reader = snapshot_reader
        self._orchestrator = orchestrator or OccOrchestrator(gitignore)
        self._transaction = transaction or OccCommitTransaction(
            snapshot_reader=snapshot_reader,
            staging=staging,
            publisher=publisher,
        )
        self._serial_merger = serial_merger or OccSerialMerger(self._transaction)
        if serial_merger is None:
            self._serial_merger.start()
        self._maintenance = maintenance or _default_maintenance(
            layer_stack=layer_stack,
            snapshot_reader=snapshot_reader,
            max_depth=auto_squash_max_depth,
        )

    async def apply_changeset(
        self,
        changes: Sequence[Change],
        *,
        snapshot: Manifest | None = None,
        options: CommitOptions | None = None,
    ) -> ChangesetResult:
        """Prepare a changeset and commit it through the layer stack."""
        total_start = monotonic_now()
        prepared = await self.prepare_changeset(
            changes,
            snapshot=snapshot,
            options=options,
        )
        return await self.commit_prepared(prepared, _total_start=total_start)

    async def commit_prepared(
        self,
        prepared: PreparedChangeset,
        *,
        _total_start: float | None = None,
    ) -> ChangesetResult:
        """Commit an already-prepared changeset through the serial merger.

        The merger's transaction calls ``transaction.snapshot()`` under the
        commit lock and revalidates against the *live* active manifest, so a
        prepared changeset whose ``snapshot`` lags the active manifest is
        validated like any concurrent commit: gated paths whose base hash no
        longer matches receive a normal OCC rejection. Callers may therefore
        run :meth:`prepare_changeset` lock-free and only serialize this call.
        """
        total_start = _total_start if _total_start is not None else monotonic_now()
        commit_start = monotonic_now()
        result = await self._serial_merger.apply(prepared)
        commit_elapsed = monotonic_now() - commit_start
        auto_squash_timings = await self._auto_squash_after_publish(result)
        return self._wrap_commit_result(
            result,
            prepared=prepared,
            total_start=total_start,
            commit_elapsed=commit_elapsed,
            sync_call=False,
            extra_timings=auto_squash_timings,
        )

    def apply_changeset_sync(
        self,
        changes: Sequence[Change],
        *,
        snapshot: Manifest | None = None,
        options: CommitOptions | None = None,
    ) -> ChangesetResult:
        total_start = monotonic_now()
        prepared = self.prepare_changeset_sync(
            changes,
            snapshot=snapshot,
            options=options,
        )
        return self.commit_prepared_sync(prepared, _total_start=total_start)

    def commit_prepared_sync(
        self,
        prepared: PreparedChangeset,
        *,
        _total_start: float | None = None,
    ) -> ChangesetResult:
        """Synchronous twin of :meth:`commit_prepared`."""
        total_start = _total_start if _total_start is not None else monotonic_now()
        commit_start = monotonic_now()
        result = self._serial_merger.apply_sync(prepared)
        commit_elapsed = monotonic_now() - commit_start
        auto_squash_timings = self._auto_squash_after_publish_sync(result)
        return self._wrap_commit_result(
            result,
            prepared=prepared,
            total_start=total_start,
            commit_elapsed=commit_elapsed,
            sync_call=True,
            extra_timings=auto_squash_timings,
        )

    async def _auto_squash_after_publish(
        self,
        result: ChangesetResult,
    ) -> dict[str, float]:
        return cast(
            dict[str, float],
            await run_sync_in_executor(
                self._maintenance.after_publish_sync,
                result,
            ),
        )

    def _auto_squash_after_publish_sync(
        self,
        result: ChangesetResult,
    ) -> dict[str, float]:
        return self._maintenance.after_publish_sync(result)

    def _wrap_commit_result(
        self,
        result: ChangesetResult,
        *,
        prepared: PreparedChangeset,
        total_start: float,
        commit_elapsed: float,
        sync_call: bool,
        extra_timings: dict[str, float] | None = None,
    ) -> ChangesetResult:
        result_timings, resume_wait = _result_timings_with_resume(result)
        timings = {
            **result_timings,
            **(extra_timings or {}),
            "occ.apply.commit_queue_wait_s": result_timings.get(
                "occ.serial.queue_wait_s",
                0.0,
            ),
            "occ.apply.commit_worker_s": result_timings.get(
                "occ.commit.total_s",
                0.0,
            ),
            "occ.apply.commit_resume_wait_s": 0.0 if sync_call else resume_wait,
            "occ.apply.commit_s": commit_elapsed,
            "occ.apply.total_s": monotonic_now() - total_start,
        }
        manifest_lag = _manifest_lag(prepared.snapshot, result.published_manifest_version)
        if manifest_lag is not None:
            timings["occ.apply.manifest_lag"] = manifest_lag
        return ChangesetResult(
            files=result.files,
            timings=timings,
            published_manifest_version=result.published_manifest_version,
        )

    async def prepare_changeset(
        self,
        changes: Sequence[Change],
        *,
        snapshot: Manifest | None = None,
        options: CommitOptions | None = None,
    ) -> PreparedChangeset:
        """Route changes and infer leased-snapshot base hashes."""
        return cast(
            PreparedChangeset,
            await run_sync_in_executor(
                self.prepare_changeset_sync,
                changes,
                snapshot=snapshot,
                options=options,
            ),
        )

    def prepare_changeset_sync(
        self,
        changes: Sequence[Change],
        *,
        snapshot: Manifest | None = None,
        options: CommitOptions | None = None,
    ) -> PreparedChangeset:
        """Route changes and infer leased-snapshot base hashes synchronously."""
        total_start = monotonic_now()
        timings: dict[str, float] = {}
        commit_options = options or CommitOptions()
        effective_snapshot = snapshot
        if effective_snapshot is None:
            snapshot_start = monotonic_now()
            effective_snapshot = self._snapshot_reader.read_active_manifest()
            timings["occ.prepare.current_snapshot_s"] = (
                monotonic_now() - snapshot_start
            )
        assert effective_snapshot is not None
        snapshot_reader = self._snapshot_reader

        def base_hash_reader(path: str) -> str | None:
            return infer_manifest_base_hash(
                snapshot_reader=snapshot_reader,
                manifest=effective_snapshot,
                path=path,
            )

        prepare_start = monotonic_now()
        prepared = self._orchestrator.prepare_sync(
            changes,
            snapshot=effective_snapshot,
            options=commit_options,
            base_hash_reader=base_hash_reader,
        )
        timings["occ.prepare.route_and_base_hash_s"] = (
            monotonic_now() - prepare_start
        )
        timings["occ.prepare.total_s"] = monotonic_now() - total_start
        return replace(prepared, timings={**prepared.timings, **timings})

    def close(self) -> None:
        """Stop owned background resources."""
        self._serial_merger.close()


def _default_maintenance(
    *,
    layer_stack: object | None,
    snapshot_reader: SnapshotReader,
    max_depth: int,
) -> MaintenancePolicy:
    if isinstance(layer_stack, SquashPort):
        return AutoSquashMaintenancePolicy(
            snapshot_reader=snapshot_reader,
            squasher=layer_stack,
            max_depth=max_depth,
        )
    return NoopMaintenancePolicy()


def _manifest_lag(
    snapshot: Manifest | None, published_version: int | None
) -> int | None:
    if snapshot is None or published_version is None:
        return None
    delta = published_version - snapshot.version - 1
    return max(0, delta)


def _result_timings_with_resume(result: ChangesetResult) -> tuple[dict[str, float], float]:
    timings = dict(result.timings)
    ready_at = timings.pop("_occ.serial.result_ready_at_s", None)
    if ready_at is None:
        return timings, 0.0
    return timings, max(0.0, monotonic_now() - ready_at)


def _default_maintenance(
    *,
    layer_stack: object | None,
    snapshot_reader: SnapshotReader,
    max_depth: int,
) -> MaintenancePolicy:
    if isinstance(layer_stack, SquashPort):
        return AutoSquashMaintenancePolicy(
            snapshot_reader=snapshot_reader,
            squasher=layer_stack,
            max_depth=max_depth,
        )
    return NoopMaintenancePolicy()


def _merge_auto_squash_timings(
    first: dict[str, float],
    second: dict[str, float],
) -> dict[str, float]:
    if not first:
        return dict(second)
    if not second:
        return dict(first)
    merged = {**first, **second}
    if (
        "layer_stack.auto_squash.total_s" in first
        or "layer_stack.auto_squash.total_s" in second
    ):
        merged["layer_stack.auto_squash.total_s"] = first.get(
            "layer_stack.auto_squash.total_s",
            0.0,
        ) + second.get("layer_stack.auto_squash.total_s", 0.0)
    if "layer_stack.auto_squash.depth_before" in first:
        merged["layer_stack.auto_squash.depth_before"] = first[
            "layer_stack.auto_squash.depth_before"
        ]
    return merged


__all__ = [
    "AUTO_SQUASH_MAX_DEPTH",
    "OccService",
]
