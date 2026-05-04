"""OCC changeset preparation and commit service."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import replace

from sandbox.layer_stack.manifest import Manifest
from sandbox.layer_stack.stack_manager import LayerStackManager
from sandbox.occ.changeset.intent import CommitIntent, PreparedChangeset
from sandbox.occ.changeset.types import Change, ChangesetResult
from sandbox.occ.commit_transaction import OccCommitTransaction
from sandbox.occ.content.gitignore_oracle import GitignoreOracle
from sandbox.occ.orchestrator import OccOrchestrator
from sandbox.occ.runtime_ops import infer_manifest_base_hash


class OccService:
    """Prepare typed OCC changesets and commit them through the layer stack."""

    def __init__(
        self,
        *,
        gitignore: GitignoreOracle,
        layer_stack: LayerStackManager | None = None,
    ) -> None:
        self._layer_stack = layer_stack
        self._orchestrator = OccOrchestrator(gitignore)
        self._transaction = (
            OccCommitTransaction(layer_stack) if layer_stack is not None else None
        )

    async def apply_changeset(
        self,
        changes: Sequence[Change],
        *,
        snapshot: Manifest | None = None,
        options: CommitIntent | None = None,
    ) -> ChangesetResult | PreparedChangeset:
        """Prepare a changeset and commit it when a layer stack is configured."""
        total_start = time.perf_counter()
        prepared = await self.prepare_changeset(
            changes,
            snapshot=snapshot,
            options=options,
        )
        if self._transaction is None:
            return replace(
                prepared,
                timings={
                    **prepared.timings,
                    "occ.apply.total_s": time.perf_counter() - total_start,
                },
            )
        commit_start = time.perf_counter()
        result, worker_start, worker_elapsed = await asyncio.to_thread(
            _revalidate_and_publish_with_timings,
            self._transaction,
            prepared,
        )
        commit_elapsed = time.perf_counter() - commit_start
        return ChangesetResult(
            files=result.files,
            timings={
                **prepared.timings,
                **result.timings,
                "occ.apply.commit_queue_wait_s": worker_start - commit_start,
                "occ.apply.commit_worker_s": worker_elapsed,
                "occ.apply.commit_resume_wait_s": max(
                    0.0,
                    commit_elapsed - (worker_start - commit_start) - worker_elapsed,
                ),
                "occ.apply.commit_s": commit_elapsed,
                "occ.apply.total_s": time.perf_counter() - total_start,
            },
            published_manifest_version=result.published_manifest_version,
        )

    async def prepare_changeset(
        self,
        changes: Sequence[Change],
        *,
        snapshot: Manifest | None = None,
        options: CommitIntent | None = None,
    ) -> PreparedChangeset:
        """Route changes and infer leased-snapshot base hashes."""
        total_start = time.perf_counter()
        timings: dict[str, float] = {}
        intent = options or CommitIntent()
        effective_snapshot = snapshot
        if effective_snapshot is None and self._layer_stack is not None:
            snapshot_start = time.perf_counter()
            effective_snapshot = self._layer_stack.read_active_manifest()
            timings["occ.prepare.current_snapshot_s"] = (
                time.perf_counter() - snapshot_start
            )
        base_hash_reader = None
        if effective_snapshot is not None and self._layer_stack is not None:
            layer_stack = self._layer_stack

            def base_hash_reader(path: str) -> str | None:
                return infer_manifest_base_hash(
                    layer_stack=layer_stack,
                    manifest=effective_snapshot,
                    path=path,
                )

        prepare_start = time.perf_counter()
        prepared = await self._orchestrator.prepare(
            changes,
            snapshot=effective_snapshot,
            intent=intent,
            base_hash_reader=base_hash_reader,
        )
        timings["occ.prepare.route_and_base_hash_s"] = (
            time.perf_counter() - prepare_start
        )
        timings["occ.prepare.total_s"] = time.perf_counter() - total_start
        return replace(prepared, timings={**prepared.timings, **timings})


def _revalidate_and_publish_with_timings(
    transaction: OccCommitTransaction,
    prepared: PreparedChangeset,
) -> tuple[ChangesetResult, float, float]:
    worker_start = time.perf_counter()
    result = transaction.revalidate_and_publish(prepared)
    return result, worker_start, time.perf_counter() - worker_start


__all__ = ["OccService"]
