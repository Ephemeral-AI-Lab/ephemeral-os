"""OCC changeset preparation and commit service."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import replace
from typing import cast

from sandbox.layer_stack.manifest import Manifest
from sandbox.occ.changeset.prepared import CommitOptions, PreparedChangeset
from sandbox.occ.changeset.types import Change, ChangesetResult
from sandbox.occ.commit_transaction import OccCommitTransaction
from sandbox.occ.content.gitignore_oracle import GitignoreMatcher
from sandbox.occ.routing.orchestrator import OccOrchestrator
from sandbox.occ.ports import OccLayerStackPorts
from sandbox.occ.routing.runtime_ops import infer_manifest_base_hash
from sandbox.occ.merge.serial import OccSerialMerger
from sandbox.runtime.async_bridge import run_sync_in_executor


AUTO_SQUASH_MAX_DEPTH = 32


class OccService:
    """Prepare typed OCC changesets and commit them through the layer stack."""

    def __init__(
        self,
        *,
        gitignore: GitignoreMatcher,
        layer_stack: OccLayerStackPorts,
    ) -> None:
        self._layer_stack = layer_stack
        self._orchestrator = OccOrchestrator(gitignore)
        self._transaction = OccCommitTransaction(
            snapshot_reader=layer_stack,
            staging=layer_stack,
            publisher=layer_stack,
        )
        self._serial_merger = OccSerialMerger(self._transaction)

    async def apply_changeset(
        self,
        changes: Sequence[Change],
        *,
        snapshot: Manifest | None = None,
        options: CommitOptions | None = None,
    ) -> ChangesetResult:
        """Prepare a changeset and commit it through the layer stack."""
        total_start = time.perf_counter()
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
        total_start = _total_start if _total_start is not None else time.perf_counter()
        commit_start = time.perf_counter()
        result = await self._serial_merger.apply(prepared)
        commit_elapsed = time.perf_counter() - commit_start
        auto_squash_timings = await run_sync_in_executor(
            self._auto_squash_after_publish_sync,
            result,
        )
        return self._wrap_commit_result(
            result,
            prepared=prepared,
            total_start=total_start,
            commit_elapsed=commit_elapsed,
            sync=False,
            extra_timings=auto_squash_timings,
        )

    def apply_changeset_sync(
        self,
        changes: Sequence[Change],
        *,
        snapshot: Manifest | None = None,
        options: CommitOptions | None = None,
    ) -> ChangesetResult:
        total_start = time.perf_counter()
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
        total_start = _total_start if _total_start is not None else time.perf_counter()
        commit_start = time.perf_counter()
        result = self._serial_merger.apply_sync(prepared)
        commit_elapsed = time.perf_counter() - commit_start
        auto_squash_timings = self._auto_squash_after_publish_sync(result)
        return self._wrap_commit_result(
            result,
            prepared=prepared,
            total_start=total_start,
            commit_elapsed=commit_elapsed,
            sync=True,
            extra_timings=auto_squash_timings,
        )

    def _auto_squash_after_publish_sync(
        self,
        result: ChangesetResult,
    ) -> dict[str, float]:
        if result.published_manifest_version is None:
            return {}
        squash = getattr(self._layer_stack, "squash", None)
        if not callable(squash):
            return {}

        active = self._layer_stack.read_active_manifest()
        if active.depth <= AUTO_SQUASH_MAX_DEPTH:
            return {}

        squash_start = time.perf_counter()
        squashed = squash(max_depth=AUTO_SQUASH_MAX_DEPTH)
        elapsed = time.perf_counter() - squash_start
        timings = {
            "layer_stack.auto_squash.total_s": elapsed,
            "layer_stack.auto_squash.max_depth": float(AUTO_SQUASH_MAX_DEPTH),
            "layer_stack.auto_squash.depth_before": float(active.depth),
        }
        if squashed is None:
            timings["layer_stack.auto_squash.raced"] = 1.0
            return timings
        timings["layer_stack.auto_squash.depth_after"] = float(squashed.depth)
        timings["layer_stack.auto_squash.manifest_version"] = float(squashed.version)
        return timings

    def _wrap_commit_result(
        self,
        result: ChangesetResult,
        *,
        prepared: PreparedChangeset,
        total_start: float,
        commit_elapsed: float,
        sync: bool,
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
            "occ.apply.commit_resume_wait_s": 0.0 if sync else resume_wait,
            "occ.apply.commit_s": commit_elapsed,
            "occ.apply.total_s": time.perf_counter() - total_start,
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
        total_start = time.perf_counter()
        timings: dict[str, float] = {}
        commit_options = options or CommitOptions()
        effective_snapshot = snapshot
        if effective_snapshot is None:
            snapshot_start = time.perf_counter()
            effective_snapshot = self._layer_stack.read_active_manifest()
            timings["occ.prepare.current_snapshot_s"] = (
                time.perf_counter() - snapshot_start
            )
        assert effective_snapshot is not None
        layer_stack = self._layer_stack

        def base_hash_reader(path: str) -> str | None:
            return infer_manifest_base_hash(
                snapshot_reader=layer_stack,
                manifest=effective_snapshot,
                path=path,
            )

        prepare_start = time.perf_counter()
        prepared = self._orchestrator.prepare_sync(
            changes,
            snapshot=effective_snapshot,
            options=commit_options,
            base_hash_reader=base_hash_reader,
        )
        timings["occ.prepare.route_and_base_hash_s"] = (
            time.perf_counter() - prepare_start
        )
        timings["occ.prepare.total_s"] = time.perf_counter() - total_start
        return replace(prepared, timings={**prepared.timings, **timings})


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
    return timings, max(0.0, time.perf_counter() - ready_at)


__all__ = ["AUTO_SQUASH_MAX_DEPTH", "OccService"]
