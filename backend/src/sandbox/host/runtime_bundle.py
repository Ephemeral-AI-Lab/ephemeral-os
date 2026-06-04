"""Bundle helper + idempotent uploader for the sandbox-local runtime.

The runtime payload contains the project modules needed to import the deployed
plugin bridge and setup orchestrator contract inside a sandbox. This module is
host-side bootstrap code; upload uses the registered provider adapter's archive
primitive by sandbox id.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import logging
import shlex
import tarfile
import uuid
from pathlib import Path
from typing import Any, Protocol

from sandbox.host.paths import (
    BUNDLE_HASH_MARKER as _BUNDLE_HASH_MARKER,
    BUNDLE_REMOTE_DIR as _BUNDLE_REMOTE_DIR,
)
from sandbox.host.runtime_artifact import EOSD_SHA256
from sandbox.provider.registry import get_adapter

__all__ = [
    "bundle_hash",
    "clear_bundle_caches",
    "compute_bundle_hash",
    "ensure_runtime_uploaded",
    "_ensure_runtime_uploaded_with_exec",
    "_runtime_bundle_bytes",
]

logger = logging.getLogger(__name__)


class RawExecCallable(Protocol):
    async def __call__(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> Any: ...


class PutArchiveCallable(Protocol):
    async def __call__(
        self,
        sandbox_id: str,
        *,
        tar_stream: bytes,
        dest_dir: str,
    ) -> None: ...


def _src_root() -> Path:
    """Return the orchestrator's ``backend/src/`` directory."""
    return Path(__file__).resolve().parent.parent.parent


def _is_excluded(path: Path) -> bool:
    parts = set(path.parts)
    if "__pycache__" in parts or path.suffix in {".pyc", ".pyo"}:
        return True
    return path.name in {"runtime_bundle.py", "raw_exec.py"}


def _normalize_tarinfo(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Strip per-environment metadata so the bundle hashes deterministically."""
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o644
    return info


def _add_if_exists(tar: tarfile.TarFile, path: Path, *, arcname: str) -> None:
    if path.exists():
        tar.add(path, arcname=arcname, filter=_normalize_tarinfo)


_BUNDLE_TAR_CACHE: bytes | None = None


def _runtime_bundle_tar_bytes() -> bytes:
    """Build the Rust-daemon plugin bridge payload as a plain tar archive."""
    global _BUNDLE_TAR_CACHE
    if _BUNDLE_TAR_CACHE is not None:
        return _BUNDLE_TAR_CACHE

    src = _src_root()
    sandbox_dir = src / "sandbox"
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        _add_if_exists(tar, sandbox_dir / "__init__.py", arcname="sandbox/__init__.py")

        shared_dir = sandbox_dir / "_shared"
        for name in ("__init__.py", "models.py", "command_exec_contract.py"):
            _add_if_exists(tar, shared_dir / name, arcname=f"sandbox/_shared/{name}")

        runtime_bridge_dir = src / "plugins" / "runtime_bridge"
        for name in (
            "__init__.py",
            "op_context.py",
            "op_registry.py",
            "ppc_service.py",
        ):
            _add_if_exists(
                tar,
                runtime_bridge_dir / name,
                arcname=f"plugins/runtime_bridge/{name}",
            )

        plugins_dir = src / "plugins"
        _add_if_exists(tar, plugins_dir / "__init__.py", arcname="plugins/__init__.py")
        lsp_runtime_dir = plugins_dir / "catalog" / "lsp" / "runtime"
        for name in (
            "__init__.py",
            "apply.py",
            "lsp_jsonrpc.py",
            "pyright_session.py",
            "server.py",
            "session_manager.py",
        ):
            _add_if_exists(
                tar,
                lsp_runtime_dir / name,
                arcname=f"plugins/catalog/lsp/runtime/{name}",
            )

    _BUNDLE_TAR_CACHE = raw.getvalue()
    return _BUNDLE_TAR_CACHE


_BUNDLE_CACHE: bytes | None = None


def _runtime_bundle_bytes() -> bytes:
    """Build the Rust-daemon plugin bridge payload as a gzip tarball."""
    global _BUNDLE_CACHE
    if _BUNDLE_CACHE is not None:
        return _BUNDLE_CACHE

    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", mtime=0) as gz:
        gz.write(_runtime_bundle_tar_bytes())
    _BUNDLE_CACHE = compressed.getvalue()
    return _BUNDLE_CACHE


_BUNDLE_HASH_CACHE: str | None = None


def compute_bundle_hash(bundle: bytes) -> str:
    """Pure stable hex digest helper for a concrete runtime bundle."""
    return hashlib.sha256(bundle).hexdigest()


def bundle_hash() -> str:
    """Cached stable hex digest of the default runtime bundle."""
    global _BUNDLE_HASH_CACHE
    if _BUNDLE_HASH_CACHE is not None:
        return _BUNDLE_HASH_CACHE
    _BUNDLE_HASH_CACHE = compute_bundle_hash(_runtime_bundle_bytes())
    return _BUNDLE_HASH_CACHE


def clear_bundle_caches() -> None:
    """Clear process-local runtime bundle caches. Test seam."""
    global _BUNDLE_CACHE, _BUNDLE_HASH_CACHE, _BUNDLE_TAR_CACHE
    _BUNDLE_CACHE = None
    _BUNDLE_HASH_CACHE = None
    _BUNDLE_TAR_CACHE = None


async def ensure_runtime_uploaded(sandbox_id: str) -> str:
    """Upload the runtime bundle through the registered provider if needed."""
    adapter = get_adapter(sandbox_id)
    put_archive = getattr(adapter, "put_archive", None)
    if not callable(put_archive):
        raise RuntimeError("sandbox runtime upload requires provider put_archive")
    digest = await _ensure_runtime_uploaded_with_exec(
        sandbox_id,
        adapter.exec,
        put_archive,
    )
    await _ensure_eosd_uploaded(sandbox_id, adapter)
    return digest


async def _ensure_runtime_uploaded_with_exec(
    sandbox_id: str,
    exec_fn: RawExecCallable,
    put_archive: PutArchiveCallable,
) -> str:
    """Upload the runtime bundle using the provided un-guarded host primitives."""
    digest = bundle_hash()
    marker_check = await exec_fn(
        sandbox_id,
        f"test -f {shlex.quote(_BUNDLE_HASH_MARKER)} && cat {shlex.quote(_BUNDLE_HASH_MARKER)}",
    )
    existing = (getattr(marker_check, "stdout", "") or "").strip()
    if _exit_code(marker_check) == 0 and existing == digest:
        logger.debug("sandbox runtime bundle already uploaded (%s) on %s", digest[:8], sandbox_id)
        return digest

    bundle = _runtime_bundle_tar_bytes()
    staging_dir = f"{_BUNDLE_REMOTE_DIR}/.runtime-staging-{uuid.uuid4().hex}"

    setup = await exec_fn(
        sandbox_id,
        f"rm -rf {shlex.quote(staging_dir)} && mkdir -p {shlex.quote(staging_dir)} {shlex.quote(_BUNDLE_REMOTE_DIR)}",
        timeout=30,
    )
    if _exit_code(setup) != 0:
        raise RuntimeError(
            f"runtime bundle staging setup failed (sandbox={sandbox_id!r}): "
            f"{getattr(setup, 'stdout', '')}"
        )

    await put_archive(
        sandbox_id,
        tar_stream=bundle,
        dest_dir=staging_dir,
    )

    finalize_cmd = (
        f"cp -a {shlex.quote(staging_dir)}/. {shlex.quote(_BUNDLE_REMOTE_DIR)}/ && "
        f"rm -rf {shlex.quote(staging_dir)} && "
        f"printf %s {shlex.quote(digest)} > {shlex.quote(_BUNDLE_HASH_MARKER)}"
    )
    result = await exec_fn(sandbox_id, finalize_cmd, timeout=60)
    if _exit_code(result) != 0:
        raise RuntimeError(
            f"runtime bundle upload failed (sandbox={sandbox_id!r}): "
            f"{getattr(result, 'stdout', '')}"
        )
    logger.info(
        "sandbox runtime bundle uploaded (%d bytes, sha=%s) to %s",
        len(bundle),
        digest[:8],
        sandbox_id,
    )
    return digest


async def _ensure_eosd_uploaded(sandbox_id: str, adapter: object) -> None:
    exec_fn = getattr(adapter, "exec")
    arch = _artifact_arch(await _exec_stdout(exec_fn, sandbox_id, "uname -m", timeout=15))
    artifact = _repo_root() / "sandbox" / "dist" / f"eosd-linux-{arch}"
    if not artifact.exists():
        raise RuntimeError(f"missing eosd artifact for {arch}: {artifact}")
    payload = artifact.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    expected = EOSD_SHA256.get(arch)
    if digest != expected:
        raise RuntimeError(
            f"eosd artifact hash mismatch for {arch}: got {digest}, expected {expected}"
        )

    marker = f"{_BUNDLE_REMOTE_DIR}/.eosd-sha256"
    remote = f"{_BUNDLE_REMOTE_DIR}/eosd"
    marker_check = await exec_fn(
        sandbox_id,
        (
            f"test -x {shlex.quote(remote)} && "
            f"test -f {shlex.quote(marker)} && cat {shlex.quote(marker)}"
        ),
        timeout=15,
    )
    if _exit_code(marker_check) == 0 and (getattr(marker_check, "stdout", "") or "").strip() == digest:
        return

    await _check_exec(
        exec_fn,
        sandbox_id,
        f"mkdir -p {shlex.quote(_BUNDLE_REMOTE_DIR)}",
        timeout=30,
        message="eosd runtime directory setup failed",
    )
    put_archive = getattr(adapter, "put_archive", None)
    if not callable(put_archive):
        raise RuntimeError("eosd upload requires provider put_archive")

    staging_dir = f"{_BUNDLE_REMOTE_DIR}/.eosd-upload-{uuid.uuid4().hex}"
    staging_file = f"{staging_dir}/eosd"
    await _check_exec(
        exec_fn,
        sandbox_id,
        f"mkdir -p {shlex.quote(staging_dir)}",
        timeout=30,
        message="eosd staging directory setup failed",
    )
    await put_archive(
        sandbox_id,
        tar_stream=_tar_file_at_path("eosd", payload, mode=0o755),
        dest_dir=staging_dir,
    )
    await _check_exec(
        exec_fn,
        sandbox_id,
        (
            f"cat {shlex.quote(staging_file)} > {shlex.quote(remote)} && "
            f"chmod 755 {shlex.quote(remote)} && "
            f"rm -rf {shlex.quote(staging_dir)}"
        ),
        timeout=30,
        message="eosd finalize failed",
    )

    await _check_exec(
        exec_fn,
        sandbox_id,
        (
            f"printf %s {shlex.quote(digest)} > {shlex.quote(marker)} && "
            f"{shlex.quote(remote)} --version >/dev/null"
        ),
        timeout=30,
        message="eosd upload verification failed",
    )


async def _exec_stdout(
    exec_fn: RawExecCallable,
    sandbox_id: str,
    command: str,
    *,
    timeout: int,
) -> str:
    result = await exec_fn(sandbox_id, command, timeout=timeout)
    if _exit_code(result) != 0:
        raise RuntimeError(f"runtime probe failed: {getattr(result, 'stdout', '')}")
    return (getattr(result, "stdout", "") or "").strip()


async def _check_exec(
    exec_fn: RawExecCallable,
    sandbox_id: str,
    command: str,
    *,
    timeout: int,
    message: str,
) -> None:
    result = await exec_fn(sandbox_id, command, timeout=timeout)
    if _exit_code(result) != 0:
        raise RuntimeError(f"{message} (sandbox={sandbox_id!r}): {getattr(result, 'stdout', '')}")


def _artifact_arch(machine: str) -> str:
    normalized = machine.strip().lower()
    if normalized in {"x86_64", "amd64"}:
        return "amd64"
    if normalized in {"aarch64", "arm64"}:
        return "arm64"
    raise RuntimeError(f"unsupported sandbox architecture for eosd artifact: {machine!r}")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _tar_file_at_path(path: str, payload: bytes, *, mode: int) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        info = tarfile.TarInfo(path.strip("/"))
        info.size = len(payload)
        info.mtime = 0
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mode = mode
        tar.addfile(info, io.BytesIO(payload))
    return raw.getvalue()


def _exit_code(result: object) -> int:
    raw = getattr(result, "exit_code", None)
    if raw is None:
        raise RuntimeError(
            f"runtime bundle exec result is missing exit_code: {type(result).__name__}"
        )
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"runtime bundle exec result has invalid exit_code: {raw!r}") from exc
