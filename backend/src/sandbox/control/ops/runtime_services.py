"""Process-local sandbox runtime service binding helpers.

This module owns the service-management side of local sandbox API setups:
layer-stack storage, OCC service registration, overlay client registration,
and the provider adapter needed by structured reads.
"""

from __future__ import annotations

import json
import shlex
import uuid
import asyncio
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn

from sandbox.api import (
    ConflictInfo,
    EditFileResult,
    RawExecResult,
    ReadFileResult,
    RequestActor,
    SearchReplaceEdit,
    ShellResult,
    WriteFileResult,
)
from sandbox.control.daemon.bundle import ensure_runtime_uploaded
from sandbox.control.daemon.command import _call_runtime_server
from sandbox.layer_stack import LayerStackManager
from sandbox.occ.client import dispose_occ_service, register_occ_service
from sandbox.occ.content.gitignore_oracle import GitignoreOracle
from sandbox.occ.service import OccService
from sandbox.overlay.client import (
    OverlayClient,
    dispose_overlay_client,
    register_overlay_client,
)
from sandbox.overlay.runner.snapshot_overlay_runner import SnapshotOverlayRunner
from sandbox.providers.protocol import ProviderAdapter
from sandbox.providers.registry import dispose_adapter, get_adapter, register_adapter
from sandbox.runtime.overlay_shell.testing import BarrierRuntimeInvoker


class MutableGitignore(GitignoreOracle):
    """In-memory gitignore oracle for process-local runtime bindings."""

    def __init__(self, ignored_paths: set[str] | None = None) -> None:
        self.ignored_paths: set[str] = set(ignored_paths or ())

    def is_ignored(self, path: str) -> bool:
        normalized = _normalize_ignored_path(path)
        return any(
            normalized == ignored or normalized.startswith(f"{ignored}/")
            for ignored in self.ignored_paths
        )

    def mark_ignored(self, paths: Iterable[str]) -> None:
        self.ignored_paths.update(_normalize_ignored_path(path) for path in paths)

    def filter_ignored(self, paths: Iterable[str]) -> set[str]:
        return {path for path in paths if self.is_ignored(path)}


class LayerReadAdapter:
    """Provider adapter for ``sandbox.api.tool.read_file`` over a layer stack."""

    name = "layer-stack-read"

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
        path = shlex.split(command)[-1]
        content, exists = self._manager.read_text(path)
        return RawExecResult(
            exit_code=0,
            stdout=json.dumps({"exists": exists, "content": content}),
        )

    def get_health(self) -> dict[str, object]:
        return {"status": "ok"}

    def list_snapshots(self) -> list[dict[str, object]]:
        return []

    def create(
        self,
        *,
        name: str,
        snapshot: str | None = None,
        image: str | None = None,
        language: str = "python",
        env_vars: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, object]:
        return _unsupported("create")

    def get(self, sandbox_id: str) -> dict[str, object]:
        return _unsupported("get")

    def list(self) -> list[dict[str, object]]:
        return _unsupported("list")

    def start(self, sandbox_id: str) -> dict[str, object]:
        return _unsupported("start")

    def stop(self, sandbox_id: str) -> dict[str, object]:
        return _unsupported("stop")

    def delete(self, sandbox_id: str) -> None:
        _unsupported("delete")

    def set_labels(self, sandbox_id: str, labels: dict[str, str]) -> dict[str, object]:
        return _unsupported("set_labels")

    def get_signed_preview_url(
        self,
        sandbox_id: str,
        port: int,
    ) -> dict[str, object]:
        return _unsupported("get_signed_preview_url")

    def get_build_logs_url(self, sandbox_id: str) -> str | None:
        return _unsupported("get_build_logs_url")


@dataclass(frozen=True)
class RuntimeServiceBinding:
    """Registered OCC/layer-stack/overlay services for one sandbox id."""

    sandbox_id: str
    manager: LayerStackManager
    gitignore: MutableGitignore
    source_root: Path

    def actor(self, label: str) -> RequestActor:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in label)
        return RequestActor(agent_id=f"sandbox-api-{safe or uuid.uuid4().hex}")

    def mark_ignored(self, paths: Iterable[str]) -> None:
        self.gitignore.mark_ignored(paths)

    def bind_default_overlay(self) -> None:
        register_overlay_client(
            self.sandbox_id,
            OverlayClient(runner=SnapshotOverlayRunner(self.manager)),
        )

    @contextmanager
    def barrier_overlay(self, *, parties: int) -> Iterator[None]:
        register_overlay_client(
            self.sandbox_id,
            OverlayClient(
                runner=SnapshotOverlayRunner(
                    self.manager,
                    invoker=BarrierRuntimeInvoker(
                        storage_root=self.manager.storage_root,
                        parties=parties,
                    ),
                )
            ),
        )
        try:
            yield
        finally:
            self.bind_default_overlay()

    def dispose(self) -> None:
        dispose_overlay_client(self.sandbox_id)
        dispose_occ_service(self.sandbox_id)
        dispose_adapter(self.sandbox_id)


@dataclass(frozen=True)
class ShellBatchCall:
    """One shell request sent as part of a provider-backed runtime batch."""

    command: str
    timeout: int | None
    cwd: str
    actor: RequestActor
    description: str


@dataclass
class RemoteRuntimeServiceBinding:
    """Provider-backed runtime services with state stored inside the sandbox."""

    sandbox_id: str
    layer_stack_root: str
    ignored_paths: set[str]
    _barrier: tuple[str, int] | None = None
    _initialized: bool = False
    _init_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def actor(self, label: str) -> RequestActor:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in label)
        return RequestActor(agent_id=f"sandbox-api-{safe or uuid.uuid4().hex}")

    def mark_ignored(self, paths: Iterable[str]) -> None:
        self.ignored_paths.update(_normalize_ignored_path(path) for path in paths)

    @contextmanager
    def barrier_overlay(self, *, parties: int) -> Iterator[None]:
        previous = self._barrier
        self._barrier = (uuid.uuid4().hex, max(1, int(parties)))
        try:
            yield
        finally:
            self._barrier = previous

    async def shell(
        self,
        *,
        command: str,
        timeout: int | None,
        cwd: str,
        actor: RequestActor,
        description: str,
    ) -> ShellResult:
        await self.ensure_initialized()
        args: dict[str, object] = {
            "layer_stack_root": self.layer_stack_root,
            "command": command,
            "cwd": cwd,
            "timeout_seconds": timeout,
            "actor_id": actor.agent_id,
            "description": description,
            "ignored_paths": sorted(self.ignored_paths),
        }
        if self._barrier is not None:
            args["barrier_id"] = self._barrier[0]
            args["barrier_parties"] = self._barrier[1]
        raw = await _call_runtime_server(
            exec_fn=get_adapter(self.sandbox_id).exec,
            sandbox_id=self.sandbox_id,
            op="api.shell",
            args=args,
            timeout=(timeout or 60) + 30,
        )
        return _shell_result_from_payload(raw)

    async def shell_batch(
        self,
        calls: Sequence[ShellBatchCall],
        *,
        max_concurrency: int = 32,
        timeout: int | None = None,
    ) -> tuple[ShellResult, ...]:
        if not calls:
            return ()
        init_start = time.perf_counter()
        await self.ensure_initialized()
        initialized_at = time.perf_counter()
        max_call_timeout = max((call.timeout or 60) for call in calls)
        waves = (len(calls) + max(1, max_concurrency) - 1) // max(1, max_concurrency)
        raw = await _call_runtime_server(
            exec_fn=get_adapter(self.sandbox_id).exec,
            sandbox_id=self.sandbox_id,
            op="api.shell_batch",
            args={
                "layer_stack_root": self.layer_stack_root,
                "ignored_paths": sorted(self.ignored_paths),
                "max_concurrency": max_concurrency,
                "items": [
                    {
                        "command": call.command,
                        "cwd": call.cwd,
                        "timeout_seconds": call.timeout,
                        "actor_id": call.actor.agent_id,
                        "description": call.description,
                    }
                    for call in calls
                ],
            },
            timeout=timeout or int(max_call_timeout * waves + 60),
        )
        decoded_at = time.perf_counter()
        batch_timings = _timings(raw.get("timings"))
        results = tuple(
            _shell_result_from_payload(item)
            for item in _payload_items(raw.get("results"))
        )
        host_timings = {
            "host.ensure_initialized_s": initialized_at - init_start,
            "host.runtime_dispatch_s": decoded_at - initialized_at,
            "host.total_s": decoded_at - init_start,
        }
        for result in results:
            result.timings.update(batch_timings)
            result.timings.update(host_timings)
        return results

    async def write_file(
        self,
        *,
        path: str,
        content: str,
        actor: RequestActor,
        description: str,
        overwrite: bool = True,
    ) -> WriteFileResult:
        await self.ensure_initialized()
        raw = await _call_runtime_server(
            exec_fn=get_adapter(self.sandbox_id).exec,
            sandbox_id=self.sandbox_id,
            op="api.write_file",
            args={
                "layer_stack_root": self.layer_stack_root,
                "path": path,
                "content": content,
                "actor_id": actor.agent_id,
                "description": description,
                "overwrite": overwrite,
                "ignored_paths": sorted(self.ignored_paths),
            },
            timeout=60,
        )
        return _write_result_from_payload(raw)

    async def edit_file(
        self,
        *,
        path: str,
        edits: Sequence[SearchReplaceEdit],
        actor: RequestActor,
        description: str,
    ) -> EditFileResult:
        await self.ensure_initialized()
        raw = await _call_runtime_server(
            exec_fn=get_adapter(self.sandbox_id).exec,
            sandbox_id=self.sandbox_id,
            op="api.edit_file",
            args={
                "layer_stack_root": self.layer_stack_root,
                "path": path,
                "edits": [
                    {"old_text": edit.old_text, "new_text": edit.new_text}
                    for edit in edits
                ],
                "actor_id": actor.agent_id,
                "description": description,
                "ignored_paths": sorted(self.ignored_paths),
            },
            timeout=60,
        )
        return _edit_result_from_payload(raw)

    async def read_file(self, *, path: str, actor: RequestActor) -> ReadFileResult:
        del actor
        await self.ensure_initialized()
        raw = await _call_runtime_server(
            exec_fn=get_adapter(self.sandbox_id).exec,
            sandbox_id=self.sandbox_id,
            op="api.read_file",
            args={
                "layer_stack_root": self.layer_stack_root,
                "path": path,
                "ignored_paths": sorted(self.ignored_paths),
            },
            timeout=60,
        )
        return ReadFileResult(
            success=bool(raw.get("success", False)),
            exists=bool(raw.get("exists", False)),
            content=str(raw.get("content", "")),
            encoding=str(raw.get("encoding", "utf-8")),
            timings=_timings(raw.get("timings")),
        )

    async def pinned_layers(self) -> tuple[str, ...]:
        await self.ensure_initialized()
        raw = await _call_runtime_server(
            exec_fn=get_adapter(self.sandbox_id).exec,
            sandbox_id=self.sandbox_id,
            op="api.pinned_layers",
            args={
                "layer_stack_root": self.layer_stack_root,
                "ignored_paths": sorted(self.ignored_paths),
            },
            timeout=60,
        )
        return tuple(str(path) for path in raw.get("pinned_layers", ()))

    async def layer_metrics(self) -> dict[str, object]:
        await self.ensure_initialized()
        return await _call_runtime_server(
            exec_fn=get_adapter(self.sandbox_id).exec,
            sandbox_id=self.sandbox_id,
            op="api.layer_metrics",
            args={
                "layer_stack_root": self.layer_stack_root,
                "ignored_paths": sorted(self.ignored_paths),
            },
            timeout=60,
        )

    async def compact(self, *, max_depth: int = 4) -> dict[str, object]:
        await self.ensure_initialized()
        return await _call_runtime_server(
            exec_fn=get_adapter(self.sandbox_id).exec,
            sandbox_id=self.sandbox_id,
            op="api.compact",
            args={
                "layer_stack_root": self.layer_stack_root,
                "max_depth": max_depth,
                "ignored_paths": sorted(self.ignored_paths),
            },
            timeout=60,
        )

    async def ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await ensure_runtime_uploaded(self.sandbox_id)
            self._initialized = True

    def dispose(self) -> None:
        dispose_adapter(self.sandbox_id)


def create_runtime_services(
    *,
    sandbox_id: str,
    storage_root: str | Path,
    source_root: str | Path | None = None,
    ignored_paths: Iterable[str] = (),
) -> RuntimeServiceBinding:
    """Create and register process-local sandbox API services."""
    manager = LayerStackManager(storage_root)
    gitignore = MutableGitignore(
        {_normalize_ignored_path(path) for path in ignored_paths}
    )
    binding = RuntimeServiceBinding(
        sandbox_id=sandbox_id,
        manager=manager,
        gitignore=gitignore,
        source_root=(
        Path(source_root)
            if source_root is not None
            else Path(storage_root) / "sources"
        ),
    )
    register_adapter(sandbox_id, LayerReadAdapter(manager))
    register_occ_service(
        sandbox_id,
        OccService(gitignore=gitignore, layer_stack=manager),
    )
    binding.bind_default_overlay()
    return binding


def create_remote_runtime_services(
    *,
    sandbox_id: str,
    layer_stack_root: str,
    ignored_paths: Iterable[str] = (),
) -> RemoteRuntimeServiceBinding:
    """Create a provider-backed runtime API binding for an existing sandbox."""
    return RemoteRuntimeServiceBinding(
        sandbox_id=sandbox_id,
        layer_stack_root=layer_stack_root,
        ignored_paths={_normalize_ignored_path(path) for path in ignored_paths},
    )


def _normalize_ignored_path(path: str) -> str:
    return str(path).strip().strip("/")


def _unsupported(operation: str) -> NoReturn:
    raise NotImplementedError(f"LayerReadAdapter does not support {operation}")


def _shell_result_from_payload(raw: dict[str, object]) -> ShellResult:
    conflict = _conflict_from_payload(raw.get("conflict"))
    return ShellResult(
        success=bool(raw.get("success", False)),
        exit_code=_int(raw.get("exit_code"), default=1),
        stdout=str(raw.get("stdout", "")),
        stderr=str(raw.get("stderr", "")),
        changed_paths=_paths(raw.get("changed_paths")),
        status=str(raw.get("status", "")),
        conflict=conflict,
        conflict_reason=(
            str(raw.get("conflict_reason"))
            if raw.get("conflict_reason") is not None
            else None
        ),
        warnings=_paths(raw.get("warnings")),
        timings=_timings(raw.get("timings")),
    )


def _write_result_from_payload(raw: dict[str, object]) -> WriteFileResult:
    conflict = _conflict_from_payload(raw.get("conflict"))
    return WriteFileResult(
        success=bool(raw.get("success", False)),
        changed_paths=_paths(raw.get("changed_paths")),
        status=str(raw.get("status", "")),
        conflict=conflict,
        conflict_reason=(
            str(raw.get("conflict_reason"))
            if raw.get("conflict_reason") is not None
            else None
        ),
        timings=_timings(raw.get("timings")),
    )


def _edit_result_from_payload(raw: dict[str, object]) -> EditFileResult:
    conflict = _conflict_from_payload(raw.get("conflict"))
    return EditFileResult(
        success=bool(raw.get("success", False)),
        changed_paths=_paths(raw.get("changed_paths")),
        applied_edits=_int(raw.get("applied_edits"), default=0),
        status=str(raw.get("status", "")),
        conflict=conflict,
        conflict_reason=(
            str(raw.get("conflict_reason"))
            if raw.get("conflict_reason") is not None
            else None
        ),
        timings=_timings(raw.get("timings")),
    )


def _conflict_from_payload(raw: object) -> ConflictInfo | None:
    if not isinstance(raw, dict):
        return None
    return ConflictInfo(
        reason=str(raw.get("reason", "")),
        conflict_file=(
            str(raw.get("conflict_file"))
            if raw.get("conflict_file") is not None
            else None
        ),
        message=str(raw.get("message", "")),
    )


def _paths(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(str(path) for path in raw if str(path or "").strip())


def _timings(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): float(value) for key, value in raw.items()}


def _payload_items(raw: object) -> tuple[dict[str, object], ...]:
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(item for item in raw if isinstance(item, dict))


def _int(value: object, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, (str, int, float)):
        return int(value)
    raise TypeError(f"expected integer value, got {type(value).__name__}")


_provider_adapter_check: type[ProviderAdapter] = LayerReadAdapter


__all__ = [
    "LayerReadAdapter",
    "MutableGitignore",
    "RemoteRuntimeServiceBinding",
    "RuntimeServiceBinding",
    "create_remote_runtime_services",
    "create_runtime_services",
]
