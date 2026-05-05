"""Shared local sandbox API load helpers.

The broad API load suite now runs as Daytona live E2E coverage. This module
keeps the local harness pieces used by narrower shell edge-case tests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from sandbox.api import (
    EditFileRequest,
    RawExecResult,
    ReadFileRequest,
    RequestActor,
    SearchReplaceEdit,
    ShellRequest,
    WriteFileRequest,
)
from sandbox.api.tool.edit import edit_file
from sandbox.api.tool.read import read_file
from sandbox.api.tool.shell import shell
from sandbox.api.tool.write import write_file
from sandbox.layer_stack import LayerChange, LayerStackManager
from sandbox.occ.changeset.intent import CommitIntent, PreparedChangeset
from sandbox.occ.changeset.types import Change, ChangesetResult
from sandbox.occ.client import dispose_occ_service, register_occ_service
from sandbox.occ.commit_transaction import OccCommitTransaction
from sandbox.occ.content.hashing import ContentHasher
from sandbox.occ.service import OccService
from sandbox.overlay.capture.changes import OverlayPathChange, content_hash
from sandbox.overlay.client import (
    OverlayClient,
    dispose_overlay_client,
    register_overlay_client,
)
from sandbox.overlay.runner.runtime_invoker import RuntimeInvoker
from sandbox.overlay.runner.snapshot_overlay_runner import (
    OverlayShellRequest,
    SnapshotOverlayRunner,
)
from sandbox.providers.registry import dispose_adapter, register_adapter
from sandbox.runtime.async_bridge import run_sync_in_executor
from sandbox.runtime.overlay_shell.result_envelope import OverlayCapture


LOGGER = logging.getLogger(__name__)
CONCURRENCY_LEVELS = (1, 5, 10, 20, 50)
INSTALL_PACKAGE_COUNT = 3
INSTALL_FILES_PER_PACKAGE = 8
VERBOSE_OP_TIMINGS = os.environ.get("EOS_SANDBOX_API_LOAD_VERBOSE_OPS") == "1"
VERBOSE_OP_LAYER_STACK = os.environ.get("EOS_SANDBOX_API_LOAD_VERBOSE_STACK") == "1"


class _Gitignore:
    def is_ignored(self, path: str) -> bool:
        return path.startswith("ignored/")


class _LayerReadAdapter:
    """Raw-exec adapter that projects ``read_file`` commands onto a layer stack."""

    name = "layer-read"

    def __init__(self, manager: LayerStackManager) -> None:
        self._manager = manager

    async def exec(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> RawExecResult:
        del sandbox_id, cwd, timeout
        await asyncio.sleep(0)
        path = shlex.split(command)[-1]
        content, exists = self._manager.read_text(path)
        return RawExecResult(
            exit_code=0,
            stdout=json.dumps({"exists": exists, "content": content}),
        )


class _AsyncBarrier:
    def __init__(self, parties: int) -> None:
        self._parties = max(1, int(parties))
        self._arrived = 0
        self._lock = asyncio.Lock()
        self._event = asyncio.Event()

    async def wait(self) -> None:
        async with self._lock:
            self._arrived += 1
            if self._arrived >= self._parties:
                self._event.set()
        await asyncio.wait_for(self._event.wait(), timeout=10)


class _BarrierOccService:
    """Prepare every concurrent changeset before any one can publish."""

    name = "barrier-occ"

    def __init__(
        self,
        inner: OccService,
        *,
        layer_stack: LayerStackManager,
        parties: int,
    ) -> None:
        self._inner = inner
        self._layer_stack = layer_stack
        self._barrier = _AsyncBarrier(parties)

    async def apply_changeset(
        self,
        changes: Sequence[Change],
        *,
        snapshot: Any = None,
        options: CommitIntent | None = None,
    ) -> ChangesetResult | PreparedChangeset:
        prepared = await self._inner.prepare_changeset(
            changes,
            snapshot=snapshot,
            options=options,
        )
        wait_start = time.perf_counter()
        await self._barrier.wait()
        wait_elapsed = time.perf_counter() - wait_start
        result = await run_sync_in_executor(
            OccCommitTransaction(self._layer_stack).revalidate_and_publish,
            prepared,
        )
        return ChangesetResult(
            files=result.files,
            timings={
                **prepared.timings,
                **result.timings,
                "test.occ.prepare_barrier_wait_s": wait_elapsed,
            },
            published_manifest_version=result.published_manifest_version,
        )


class _BarrierInvoker:
    """Hold shell invocations until every task has acquired its snapshot."""

    def __init__(self, *, storage_root: Path, parties: int) -> None:
        self._inner = RuntimeInvoker(storage_root=storage_root)
        self._barrier = _AsyncBarrier(parties)

    async def invoke(
        self,
        *,
        request: OverlayShellRequest,
        manifest: Any,
    ) -> OverlayCapture:
        await self._barrier.wait()
        return await self._inner.invoke(request=request, manifest=manifest)


class _FastShellInvoker:
    """Cheap shell-capture stand-in for the mixed load distribution test."""

    def __init__(self, *, storage_root: Path) -> None:
        self._run_root = storage_root / "runtime" / "fast-shell"

    async def invoke(
        self,
        *,
        request: OverlayShellRequest,
        manifest: Any,
    ) -> OverlayCapture:
        total_start = time.perf_counter()
        payload, path = _parse_fast_printf_redirect(request.command[-1])
        run_dir = self._run_root / request.request_id
        run_dir.mkdir(parents=True, exist_ok=True)
        stdout_ref = run_dir / "stdout.bin"
        stderr_ref = run_dir / "stderr.bin"
        content_path = run_dir / "content.bin"
        stdout_ref.write_bytes(b"")
        stderr_ref.write_bytes(b"")
        content_path.write_bytes(payload)
        return OverlayCapture(
            exit_code=0,
            stdout_ref=str(stdout_ref),
            stderr_ref=str(stderr_ref),
            snapshot_version=manifest.version,
            changes=(
                OverlayPathChange(
                    path=path,
                    kind="write",
                    content_path=str(content_path),
                    final_hash=content_hash(content_path),
                ),
            ),
            snapshot_manifest=manifest,
            timings={
                "overlay.mount_snapshot_s": 0.0,
                "overlay.run_command_s": 0.0,
                "overlay.capture_changes_s": 0.0,
                "overlay.total_s": time.perf_counter() - total_start,
            },
        )


def _parse_fast_printf_redirect(command: str) -> tuple[bytes, str]:
    tokens = shlex.split(command)
    if len(tokens) != 4 or tokens[0] != "printf" or tokens[2] != ">":
        raise ValueError(f"unsupported fast shell command: {command!r}")
    return tokens[1].encode("utf-8"), tokens[3]


@dataclass
class ApiLoadEnv:
    sandbox_id: str
    manager: LayerStackManager
    source_root: Path

    def actor(self, index: int) -> RequestActor:
        return RequestActor(agent_id=f"load-agent-{index}")

    def seed(self, path: str, content: str | bytes) -> None:
        payload = content if isinstance(content, bytes) else content.encode("utf-8")
        source = self.source_root / f"{uuid.uuid4().hex}.bin"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(payload)
        self.manager.publish_changes(
            [
                LayerChange(
                    path=path,
                    kind="write",
                    content_hash=ContentHasher().hash_bytes(payload),
                    source_path=str(source),
                )
            ]
        )

    def layer_stack_metrics(self) -> dict[str, int]:
        manifest = self.manager.read_active_manifest()
        layer_dirs = tuple((self.manager.storage_root / "layers").iterdir())
        staging_dirs = tuple((self.manager.storage_root / "staging").iterdir())
        total_bytes = 0
        for entry in self.manager.storage_root.rglob("*"):
            if entry.is_file() or entry.is_symlink():
                total_bytes += entry.lstat().st_size
        return {
            "manifest_version": manifest.version,
            "manifest_depth": manifest.depth,
            "active_leases": len(self.manager.lease_snapshots()),
            "pinned_layers": len(self.manager.pinned_layers()),
            "layer_dirs": len(layer_dirs),
            "staging_dirs": len(staging_dirs),
            "storage_bytes": total_bytes,
        }

    def layer_stack_progress_metrics(self) -> dict[str, int]:
        if VERBOSE_OP_LAYER_STACK:
            return self.layer_stack_metrics()
        manifest = self.manager.read_active_manifest()
        return {
            "manifest_version": manifest.version,
            "manifest_depth": manifest.depth,
            "active_leases": len(self.manager.lease_snapshots()),
        }


@dataclass(frozen=True)
class OperationSample:
    index: int
    operation: str
    success: bool
    status: str
    elapsed_s: float
    timings: Mapping[str, float]


@dataclass(frozen=True)
class LoadBatchReport:
    label: str
    concurrency: int
    wall_elapsed_s: float
    samples: tuple[OperationSample, ...]
    stats: Mapping[str, float]
    parallel_factor: float

    @property
    def successes(self) -> int:
        return sum(1 for sample in self.samples if sample.success)

    @property
    def failures(self) -> int:
        return len(self.samples) - self.successes


class LoadRecorder:
    def __init__(self, test_name: str) -> None:
        self.test_name = test_name
        self.events: list[dict[str, Any]] = []

    def emit(self, event: str, **payload: Any) -> None:
        entry = {
            "event": event,
            "test": self.test_name,
            **payload,
        }
        self.events.append(entry)
        LOGGER.info("sandbox_api_load %s", json.dumps(entry, sort_keys=True))


@pytest.fixture
def api_load_env(tmp_path: Path) -> ApiLoadEnv:
    sandbox_id = f"api-load-{uuid.uuid4().hex}"
    manager = LayerStackManager(tmp_path / "layer-stack")
    env = ApiLoadEnv(
        sandbox_id=sandbox_id,
        manager=manager,
        source_root=tmp_path / "sources",
    )
    register_adapter(sandbox_id, _LayerReadAdapter(manager))
    register_occ_service(
        sandbox_id,
        OccService(gitignore=_Gitignore(), layer_stack=manager),
    )
    register_overlay_client(
        sandbox_id,
        OverlayClient(runner=SnapshotOverlayRunner(manager)),
    )
    try:
        yield env
    finally:
        dispose_overlay_client(sandbox_id)
        dispose_occ_service(sandbox_id)
        dispose_adapter(sandbox_id)


async def _local_process_test_read_api_load_levels_1_5_10_20_50(api_load_env: ApiLoadEnv) -> None:
    recorder = LoadRecorder("read_api_load")
    seen = set()
    for level in CONCURRENCY_LEVELS:
        for index in range(level):
            api_load_env.seed(
                f"load/read/{level}/{index}.txt",
                f"read-{level}-{index}\n",
            )

        async def op(index: int):
            return await read_file(
                api_load_env.sandbox_id,
                ReadFileRequest(
                    path=f"load/read/{level}/{index}.txt",
                    actor=api_load_env.actor(index),
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="read",
            concurrency=level,
            operation=op,
        )
        _assert_all_success(report)
        _assert_timing_keys(report, ("api.read.raw_exec_s", "api.read.total_s"))
        seen.add(level)
    assert seen == set(CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def _local_process_test_write_api_load_levels_1_5_10_20_50(api_load_env: ApiLoadEnv) -> None:
    recorder = LoadRecorder("write_api_load")
    seen = set()
    for level in CONCURRENCY_LEVELS:

        async def op(index: int):
            path = f"load/write/{level}/{index}.txt"
            return await write_file(
                api_load_env.sandbox_id,
                WriteFileRequest(
                    path=path,
                    content=f"write-{level}-{index}\n",
                    actor=api_load_env.actor(index),
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="write",
            concurrency=level,
            operation=op,
        )
        _assert_all_success(report)
        _assert_timing_keys(
            report,
            (
                "api.write.total_s",
                "occ.prepare.total_s",
                "occ.commit.total_s",
                "occ.commit.publish_layer_s",
            ),
        )
        for index in range(level):
            assert api_load_env.manager.read_text(f"load/write/{level}/{index}.txt") == (
                f"write-{level}-{index}\n",
                True,
            )
        _compact_stack(api_load_env)
        seen.add(level)
    assert seen == set(CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def _local_process_test_edit_api_load_levels_1_5_10_20_50(api_load_env: ApiLoadEnv) -> None:
    recorder = LoadRecorder("edit_api_load")
    seen = set()
    for level in CONCURRENCY_LEVELS:
        for index in range(level):
            api_load_env.seed(
                f"load/edit/{level}/{index}.txt",
                f"name = 'base-{index}'\n",
            )

        async def op(index: int):
            path = f"load/edit/{level}/{index}.txt"
            return await edit_file(
                api_load_env.sandbox_id,
                EditFileRequest(
                    path=path,
                    edits=(
                        SearchReplaceEdit(
                            old_text=f"base-{index}",
                            new_text=f"edited-{index}",
                        ),
                    ),
                    actor=api_load_env.actor(index),
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="edit",
            concurrency=level,
            operation=op,
        )
        _assert_all_success(report)
        _assert_timing_keys(
            report,
            (
                "api.edit.total_s",
                "occ.prepare.total_s",
                "occ.commit.total_s",
                "occ.commit.publish_layer_s",
            ),
        )
        for index in range(level):
            assert api_load_env.manager.read_text(f"load/edit/{level}/{index}.txt") == (
                f"name = 'edited-{index}'\n",
                True,
            )
        _compact_stack(api_load_env)
        seen.add(level)
    assert seen == set(CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def _local_process_test_shell_api_load_levels_1_5_10_20_50(api_load_env: ApiLoadEnv) -> None:
    recorder = LoadRecorder("shell_api_load")
    seen = set()
    for level in CONCURRENCY_LEVELS:

        async def op(index: int):
            path = f"load/shell/{level}/{index}.txt"
            payload = f"shell-{level}-{index}\n"
            command = (
                f"mkdir -p {shlex.quote(str(Path(path).parent))}; "
                f"printf {shlex.quote(payload)} > {shlex.quote(path)}; "
                f"cat {shlex.quote(path)}"
            )
            return await shell(
                api_load_env.sandbox_id,
                ShellRequest(
                    command=command,
                    actor=api_load_env.actor(index),
                    timeout=10,
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="shell",
            concurrency=level,
            operation=op,
        )
        _assert_all_success(report)
        _assert_timing_keys(
            report,
            (
                "api.shell.total_s",
                "overlay.mount_snapshot_s",
                "overlay.run_command_s",
                "overlay.capture_changes_s",
                "occ.prepare.total_s",
                "occ.commit.total_s",
            ),
        )
        for index in range(level):
            assert api_load_env.manager.read_text(f"load/shell/{level}/{index}.txt") == (
                f"shell-{level}-{index}\n",
                True,
            )
        _compact_stack(api_load_env)
        seen.add(level)
    assert seen == set(CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def _local_process_test_mixed_edit_write_shell_load_levels_1_5_10_20_50(
    api_load_env: ApiLoadEnv,
) -> None:
    recorder = LoadRecorder("mixed_edit_write_shell_load")
    register_overlay_client(
        api_load_env.sandbox_id,
        OverlayClient(
            runner=SnapshotOverlayRunner(
                api_load_env.manager,
                invoker=_FastShellInvoker(
                    storage_root=api_load_env.manager.storage_root,
                ),
            )
        ),
    )
    seen = set()
    for level in CONCURRENCY_LEVELS:
        for index in range(level):
            if index % 3 == 0:
                api_load_env.seed(
                    f"load/mixed/{level}/{index}/edit.txt",
                    f"state = 'base-{index}'\n",
                )
        _compact_stack(api_load_env)

        async def op(index: int):
            operation_kind = index % 3
            if operation_kind == 0:
                edit_path = f"load/mixed/{level}/{index}/edit.txt"
                return await edit_file(
                    api_load_env.sandbox_id,
                    EditFileRequest(
                        path=edit_path,
                        edits=(
                            SearchReplaceEdit(
                                old_text=f"base-{index}",
                                new_text=f"edited-{index}",
                            ),
                        ),
                        actor=api_load_env.actor(index),
                    ),
                )
            if operation_kind == 1:
                write_path = f"load/mixed/{level}/{index}/write.txt"
                return await write_file(
                    api_load_env.sandbox_id,
                    WriteFileRequest(
                        path=write_path,
                        content=f"write-{level}-{index}\n",
                        actor=api_load_env.actor(index),
                    ),
                )
            shell_path = f"load/mixed/{level}/{index}/shell.txt"
            shell_payload = f"shell-{level}-{index}\n"
            return await shell(
                api_load_env.sandbox_id,
                ShellRequest(
                    command=(
                        f"printf {shlex.quote(shell_payload)} > "
                        f"{shlex.quote(shell_path)}"
                    ),
                    actor=api_load_env.actor(index),
                    timeout=10,
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="mixed",
            concurrency=level,
            operation=op,
        )
        _assert_all_success(report)
        _assert_any_timing_keys(
            report,
            _mixed_required_timing_keys(level),
        )
        for index in range(level):
            if index % 3 == 0:
                assert api_load_env.manager.read_text(
                    f"load/mixed/{level}/{index}/edit.txt"
                ) == (
                    f"state = 'edited-{index}'\n",
                    True,
                )
            elif index % 3 == 1:
                assert api_load_env.manager.read_text(
                    f"load/mixed/{level}/{index}/write.txt"
                ) == (
                    f"write-{level}-{index}\n",
                    True,
                )
            else:
                assert api_load_env.manager.read_text(
                    f"load/mixed/{level}/{index}/shell.txt"
                ) == (
                    f"shell-{level}-{index}\n",
                    True,
                )
        _compact_stack(api_load_env)
        seen.add(level)
    assert seen == set(CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def _local_process_test_current_write_conflict_detection_levels_1_5_10_20_50(
    api_load_env: ApiLoadEnv,
) -> None:
    recorder = LoadRecorder("current_write_conflicts")
    seen = set()
    for level in CONCURRENCY_LEVELS:
        register_occ_service(
            api_load_env.sandbox_id,
            _BarrierOccService(
                OccService(gitignore=_Gitignore(), layer_stack=api_load_env.manager),
                layer_stack=api_load_env.manager,
                parties=level,
            ),
        )
        path = f"conflict/write/{level}.txt"

        async def op(index: int):
            return await write_file(
                api_load_env.sandbox_id,
                WriteFileRequest(
                    path=path,
                    content=f"winner-{index}\n",
                    actor=api_load_env.actor(index),
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="write_conflict",
            concurrency=level,
            operation=op,
        )
        _assert_single_winner(report, conflict_status="aborted_version")
        content, exists = api_load_env.manager.read_text(path)
        assert exists is True
        assert content in {f"winner-{index}\n" for index in range(level)}
        _compact_stack(api_load_env)
        seen.add(level)
    assert seen == set(CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def _local_process_test_current_edit_conflict_detection_levels_1_5_10_20_50(
    api_load_env: ApiLoadEnv,
) -> None:
    recorder = LoadRecorder("current_edit_conflicts")
    seen = set()
    for level in CONCURRENCY_LEVELS:
        register_occ_service(
            api_load_env.sandbox_id,
            _BarrierOccService(
                OccService(gitignore=_Gitignore(), layer_stack=api_load_env.manager),
                layer_stack=api_load_env.manager,
                parties=level,
            ),
        )
        path = f"conflict/edit/{level}.txt"
        api_load_env.seed(path, "value = 'base'\n")

        async def op(index: int):
            return await edit_file(
                api_load_env.sandbox_id,
                EditFileRequest(
                    path=path,
                    edits=(
                        SearchReplaceEdit(
                            old_text="'base'",
                            new_text=f"'winner-{index}'",
                        ),
                    ),
                    actor=api_load_env.actor(index),
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="edit_conflict",
            concurrency=level,
            operation=op,
        )
        _assert_single_winner(report, conflict_status="aborted_overlap")
        content, exists = api_load_env.manager.read_text(path)
        assert exists is True
        assert content in {f"value = 'winner-{index}'\n" for index in range(level)}
        _compact_stack(api_load_env)
        seen.add(level)
    assert seen == set(CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def _local_process_test_shell_concurrent_update_conflict_detection_levels_1_5_10_20_50(
    api_load_env: ApiLoadEnv,
) -> None:
    recorder = LoadRecorder("shell_update_conflicts")
    seen = set()
    for level in CONCURRENCY_LEVELS:
        register_overlay_client(
            api_load_env.sandbox_id,
            OverlayClient(
                runner=SnapshotOverlayRunner(
                    api_load_env.manager,
                    invoker=_BarrierInvoker(
                        storage_root=api_load_env.manager.storage_root,
                        parties=level,
                    ),
                )
            ),
        )
        path = f"conflict/shell/{level}.txt"

        async def op(index: int):
            payload = f"shell-winner-{index}\n"
            command = (
                f"mkdir -p {shlex.quote(str(Path(path).parent))}; "
                f"printf {shlex.quote(payload)} > {shlex.quote(path)}"
            )
            return await shell(
                api_load_env.sandbox_id,
                ShellRequest(
                    command=command,
                    actor=api_load_env.actor(index),
                    timeout=10,
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="shell_conflict",
            concurrency=level,
            operation=op,
        )
        _assert_single_winner(report, conflict_status="aborted_version")
        content, exists = api_load_env.manager.read_text(path)
        assert exists is True
        assert content in {f"shell-winner-{index}\n" for index in range(level)}
        _compact_stack(api_load_env)
        seen.add(level)
    assert seen == set(CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def _local_process_test_layer_stack_squash_algorithm_levels_1_5_10_20_50(
    api_load_env: ApiLoadEnv,
) -> None:
    recorder = LoadRecorder("layer_stack_squash")
    seen = set()
    for level in CONCURRENCY_LEVELS:

        async def op(index: int):
            return await write_file(
                api_load_env.sandbox_id,
                WriteFileRequest(
                    path=f"squash/{level}/item-{index}.txt",
                    content=f"squash-{level}-{index}\n",
                    actor=api_load_env.actor(index),
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="squash_publish",
            concurrency=level,
            operation=op,
        )
        _assert_all_success(report)
        before = api_load_env.manager.read_active_manifest()
        lease = api_load_env.manager.acquire_snapshot_lease(f"squash-lease-{level}")
        target_depth = max(1, min(10, before.depth // 2 or 1))
        squashed = api_load_env.manager.squash(max_depth=target_depth)
        after = api_load_env.manager.read_active_manifest()
        if before.depth > target_depth:
            assert squashed is not None
            assert after.depth <= target_depth
            assert any(layer.layer_id.startswith("B") for layer in after.layers)
        else:
            assert squashed is None
            assert after == before
        for index in range(level):
            path = f"squash/{level}/item-{index}.txt"
            assert api_load_env.manager.read_text(path) == (
                f"squash-{level}-{index}\n",
                True,
            )
            assert api_load_env.manager.read_text(path, manifest=lease.manifest) == (
                f"squash-{level}-{index}\n",
                True,
            )
        assert api_load_env.manager.release_lease(lease.lease_id) is True
        api_load_env.manager.collect_garbage(young_staging_age_seconds=0)
        recorder.emit(
            "squash_done",
            concurrency=level,
            before_depth=before.depth,
            after_depth=after.depth,
            layer_stack=api_load_env.layer_stack_metrics(),
        )
        _compact_stack(api_load_env)
        seen.add(level)
    assert seen == set(CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def _local_process_test_occ_global_serial_merger_prepare_writes_levels_1_5_10_20_50(
    api_load_env: ApiLoadEnv,
) -> None:
    recorder = LoadRecorder("occ_global_serial_merger")
    seen = set()
    for level in CONCURRENCY_LEVELS:
        async def op(index: int):
            return await write_file(
                api_load_env.sandbox_id,
                WriteFileRequest(
                    path=f"serial/{level}/item-{index}.txt",
                    content=f"serial-{level}-{index}\n",
                    actor=api_load_env.actor(index),
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="serial_prepare_write",
            concurrency=level,
            operation=op,
        )
        _assert_all_success(report)
        _assert_timing_keys(
            report,
            (
                "occ.prepare.total_s",
                "occ.commit.total_s",
                "occ.commit.publish_layer_s",
                "occ.serial.batch_size",
            ),
        )
        if level > 1:
            assert max(
                sample.timings.get("occ.serial.batch_size", 0.0)
                for sample in report.samples
            ) > 1.0
        for index in range(level):
            assert api_load_env.manager.read_text(f"serial/{level}/item-{index}.txt") == (
                f"serial-{level}-{index}\n",
                True,
            )
        _compact_stack(api_load_env)
        seen.add(level)
    assert seen == set(CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def _run_load_batch(
    env: ApiLoadEnv,
    recorder: LoadRecorder,
    *,
    label: str,
    concurrency: int,
    operation: Callable[[int], Awaitable[Any]],
) -> LoadBatchReport:
    start_event = asyncio.Event()
    lock = asyncio.Lock()
    in_flight = 0
    completed = 0
    successes = 0
    failures = 0
    samples: list[OperationSample] = []

    recorder.emit(
        "batch_start",
        label=label,
        concurrency=concurrency,
        layer_stack=env.layer_stack_metrics(),
    )

    async def one(index: int) -> OperationSample:
        nonlocal in_flight, completed, successes, failures
        await start_event.wait()
        async with lock:
            in_flight += 1
            current_in_flight = in_flight
        recorder.emit(
            "op_start",
            label=label,
            concurrency=concurrency,
            index=index,
            in_flight=current_in_flight,
            **_op_layer_stack_payload(env),
        )
        start = time.perf_counter()
        result = await operation(index)
        elapsed = time.perf_counter() - start
        sample = OperationSample(
            index=index,
            operation=label,
            success=bool(getattr(result, "success", False)),
            status=str(getattr(result, "status", "ok" if getattr(result, "success", False) else "")),
            elapsed_s=elapsed,
            timings=dict(getattr(result, "timings", {}) or {}),
        )
        async with lock:
            in_flight -= 1
            completed += 1
            if sample.success:
                successes += 1
            else:
                failures += 1
            progress = {
                "completed": completed,
                "successes": successes,
                "failures": failures,
                "in_flight": in_flight,
            }
        recorder.emit(
            "op_done",
            label=label,
            concurrency=concurrency,
            index=index,
            elapsed_s=elapsed,
            status=sample.status,
            **_op_done_timing_payload(sample),
            **progress,
            **_op_layer_stack_payload(env),
        )
        return sample

    tasks = [asyncio.create_task(one(index)) for index in range(concurrency)]
    batch_start = time.perf_counter()
    start_event.set()
    samples = list(await asyncio.gather(*tasks))
    wall_elapsed = time.perf_counter() - batch_start
    stats = _elapsed_stats([sample.elapsed_s for sample in samples])
    parallel_factor = (
        sum(sample.elapsed_s for sample in samples) / wall_elapsed
        if wall_elapsed > 0
        else 0.0
    )
    report = LoadBatchReport(
        label=label,
        concurrency=concurrency,
        wall_elapsed_s=wall_elapsed,
        samples=tuple(samples),
        stats=stats,
        parallel_factor=parallel_factor,
    )
    recorder.emit(
        "batch_done",
        label=label,
        concurrency=concurrency,
        wall_elapsed_s=wall_elapsed,
        parallel_factor=parallel_factor,
        successes=report.successes,
        failures=report.failures,
        stats=dict(stats),
        timing_stats=_timing_stats(samples),
        layer_stack=env.layer_stack_metrics(),
    )
    assert report.parallel_factor > 0.0
    return report


def _assert_all_success(report: LoadBatchReport) -> None:
    assert report.failures == 0, _summary(report)
    assert report.successes == report.concurrency


def _op_done_timing_payload(sample: OperationSample) -> dict[str, Any]:
    if VERBOSE_OP_TIMINGS:
        return {"timings": sample.timings}
    return {"timing_keys": sorted(sample.timings)}


def _op_layer_stack_payload(env: ApiLoadEnv) -> dict[str, Any]:
    if VERBOSE_OP_LAYER_STACK:
        return {"layer_stack": env.layer_stack_progress_metrics()}
    return {}


def _assert_single_winner(
    report: LoadBatchReport,
    *,
    conflict_status: str,
) -> None:
    assert report.successes == 1, _summary(report)
    assert report.failures == report.concurrency - 1
    failed_statuses = [sample.status for sample in report.samples if not sample.success]
    assert all(status == conflict_status for status in failed_statuses), _summary(report)


def _assert_timing_keys(
    report: LoadBatchReport,
    required_keys: Sequence[str],
) -> None:
    for sample in report.samples:
        missing = [key for key in required_keys if key not in sample.timings]
        assert not missing, (
            f"missing timing keys for {report.label}#{sample.index}: {missing}; "
            f"available={sorted(sample.timings)}"
        )


def _assert_any_timing_keys(
    report: LoadBatchReport,
    required_keys: Sequence[str],
) -> None:
    available = {
        key
        for sample in report.samples
        for key in sample.timings
    }
    missing = [key for key in required_keys if key not in available]
    assert not missing, (
        f"missing timing keys for {report.label}: {missing}; "
        f"available={sorted(available)}"
    )


def _mixed_required_timing_keys(concurrency: int) -> tuple[str, ...]:
    keys = {"occ.commit.total_s"}
    operations = {index % 3 for index in range(concurrency)}
    if 0 in operations:
        keys.add("api.edit.total_s")
    if 1 in operations:
        keys.add("api.write.total_s")
    if 2 in operations:
        keys.update({"api.shell.total_s", "overlay.mount_snapshot_s"})
    return tuple(sorted(keys))


def _assert_logged_progress(recorder: LoadRecorder) -> None:
    event_names = {event["event"] for event in recorder.events}
    assert {"batch_start", "op_start", "op_done", "batch_done"} <= event_names
    assert any("layer_stack" in event for event in recorder.events)
    assert any("parallel_factor" in event for event in recorder.events)


def _compact_stack(env: ApiLoadEnv, *, max_depth: int = 4) -> None:
    env.manager.squash(max_depth=max_depth)
    env.manager.collect_garbage(young_staging_age_seconds=0)


def _elapsed_stats(samples: Sequence[float]) -> dict[str, float]:
    ordered = sorted(float(sample) for sample in samples)
    if not ordered:
        return {"min": 0.0, "max": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0}
    return {
        "min": ordered[0],
        "max": ordered[-1],
        "p50": _percentile(ordered, 0.50),
        "p95": _percentile(ordered, 0.95),
        "p99": _percentile(ordered, 0.99),
    }


def _timing_stats(samples: Sequence[OperationSample]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[float]] = {}
    for sample in samples:
        for key, value in sample.timings.items():
            grouped.setdefault(key, []).append(float(value))
    return {
        key: _elapsed_stats(values)
        for key, values in sorted(grouped.items())
    }


def _percentile(ordered: Sequence[float], percentile: float) -> float:
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _summary(report: LoadBatchReport) -> str:
    statuses = [
        {
            "index": sample.index,
            "success": sample.success,
            "status": sample.status,
            "elapsed_s": sample.elapsed_s,
        }
        for sample in report.samples
    ]
    return json.dumps(
        {
            "label": report.label,
            "concurrency": report.concurrency,
            "successes": report.successes,
            "failures": report.failures,
            "parallel_factor": report.parallel_factor,
            "statuses": statuses,
        },
        indent=2,
        sort_keys=True,
    )
