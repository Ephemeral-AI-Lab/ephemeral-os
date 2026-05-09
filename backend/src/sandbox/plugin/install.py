"""Idempotent host-side plugin bundle uploader + setup.sh runner.

Mirrors :mod:`sandbox.host.runtime_bundle` but per-plugin: bundles
``plugin.md`` + ``tools/`` + optional ``runtime/`` + ``setup.sh`` from the
host catalog into a gzip tarball, uploads it to
``/tmp/eos-sandbox-runtime/plugins/catalog/<name>/`` on first call, runs
``setup.sh`` once, and writes a ``.installed-<hash>`` marker so subsequent
calls are cheap.

Concurrency: per ``(sandbox_id, plugin_name)`` async lock so concurrent
first-callers share a single upload + setup cycle.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import io
import logging
import shlex
import tarfile
from pathlib import Path
from typing import Any, Protocol

from plugins.core.manifest import PluginManifest
from sandbox.host.runtime_bundle import BUNDLE_REMOTE_DIR
from sandbox.models import RawExecResult
from sandbox.provider.registry import get_adapter

__all__ = [
    "PLUGIN_BUNDLE_REMOTE_ROOT",
    "PluginInstallError",
    "ensure_installed",
    "plugin_install_dir",
    "plugin_marker_path",
]


logger = logging.getLogger(__name__)

# All plugins land under /tmp/eos-sandbox-runtime/plugins/catalog/<name>/.
# /tmp/eos-sandbox-runtime/ is already on the daemon's sys.path (that's how
# sandbox.runtime.daemon imports the runtime bundle). plugins/ and
# plugins/catalog/ are implicit namespace packages — no __init__.py is uploaded
# — so ``import plugins.catalog.<name>.runtime.server`` resolves naturally.
PLUGIN_BUNDLE_REMOTE_ROOT = f"{BUNDLE_REMOTE_DIR}/plugins/catalog"

_CHUNK_SIZE = 32 * 1024
# 600s headroom for plugin setup scripts that pip-install Python deps over
# the network. Pure-Python servers like pylsp install in ~30-60s; this
# leaves slack for slow networks but stays within Daytona's exec timeout.
_DEFAULT_SETUP_TIMEOUT = 600


class PluginInstallError(RuntimeError):
    """Raised when plugin install fails (upload, setup.sh, or marker write)."""


class _RawExecCallable(Protocol):
    async def __call__(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> RawExecResult: ...


_locks: dict[tuple[str, str], asyncio.Lock] = {}
_installed_marker_cache: dict[tuple[str, str], str] = {}


def plugin_install_dir(plugin_name: str) -> str:
    return f"{PLUGIN_BUNDLE_REMOTE_ROOT}/{plugin_name}"


def plugin_marker_path(plugin_name: str, digest: str) -> str:
    return f"{plugin_install_dir(plugin_name)}/.installed-{digest}"


async def ensure_installed(
    sandbox_id: str,
    manifest: PluginManifest,
    *,
    setup_timeout: int = _DEFAULT_SETUP_TIMEOUT,
    exec_fn: _RawExecCallable | None = None,
) -> str:
    """Ensure *manifest*'s plugin bundle is installed on *sandbox_id*."""
    key = (sandbox_id, manifest.name)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        executor = exec_fn or get_adapter(sandbox_id).exec
        digest = _bundle_hash(manifest)
        if _installed_marker_cache.get(key) == digest:
            return digest
        if await _marker_present(executor, sandbox_id, manifest.name, digest):
            _installed_marker_cache[key] = digest
            return digest
        await _upload_and_run_setup(
            executor,
            sandbox_id=sandbox_id,
            manifest=manifest,
            digest=digest,
            setup_timeout=setup_timeout,
        )
        _installed_marker_cache[key] = digest
        return digest


def _bundle_hash(manifest: PluginManifest) -> str:
    hasher = hashlib.sha256()
    for label, path in _hash_inputs(manifest):
        hasher.update(label.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def _hash_inputs(manifest: PluginManifest) -> list[tuple[str, Path]]:
    """Every regular file under the plugin's source_dir, sorted by relpath.

    Mirrors :func:`_build_tar` so the hash invalidates exactly when the
    bundle does. Skips ``__pycache__`` and dotfiles starting with ``.``
    (e.g. an editor-leftover ``.DS_Store``).
    """
    inputs: list[tuple[str, Path]] = []
    for path in sorted(manifest.source_dir.rglob("*")):
        if not _bundle_includes(path):
            continue
        rel = path.relative_to(manifest.source_dir).as_posix()
        inputs.append((rel, path))
    return inputs


def _bundle_includes(path: Path) -> bool:
    if not path.is_file():
        return False
    parts = path.parts
    if "__pycache__" in parts:
        return False
    if any(part.startswith(".") for part in parts):
        return False
    return True


def _build_tar(manifest: PluginManifest) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for path in sorted(manifest.source_dir.rglob("*")):
            if not _bundle_includes(path):
                continue
            rel = path.relative_to(manifest.source_dir).as_posix()
            tar.add(
                path,
                arcname=rel,
                filter=_normalize_tarinfo,
            )
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    return compressed.getvalue()


def _normalize_tarinfo(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o644
    return info


async def _marker_present(
    exec_fn: _RawExecCallable,
    sandbox_id: str,
    plugin_name: str,
    digest: str,
) -> bool:
    marker = plugin_marker_path(plugin_name, digest)
    result = await exec_fn(
        sandbox_id,
        f"test -f {shlex.quote(marker)}",
        timeout=10,
    )
    return getattr(result, "exit_code", 1) == 0


async def _upload_and_run_setup(
    exec_fn: _RawExecCallable,
    *,
    sandbox_id: str,
    manifest: PluginManifest,
    digest: str,
    setup_timeout: int,
) -> None:
    install_dir = plugin_install_dir(manifest.name)
    marker = plugin_marker_path(manifest.name, digest)
    tar_path = f"{install_dir}/.bundle.tar.gz"

    bundle = _build_tar(manifest)
    encoded = base64.b64encode(bundle).decode("ascii")

    setup_dir = await exec_fn(
        sandbox_id,
        (
            f"rm -rf {shlex.quote(install_dir)} && "
            f"mkdir -p {shlex.quote(install_dir)} && "
            f": > {shlex.quote(tar_path)}"
        ),
        timeout=30,
    )
    _check(setup_dir, f"plugin install: failed to prepare {install_dir}")

    for offset in range(0, len(encoded), _CHUNK_SIZE):
        chunk = encoded[offset : offset + _CHUNK_SIZE]
        write = await exec_fn(
            sandbox_id,
            (
                f"printf %s {shlex.quote(chunk)} | base64 -d "
                f">> {shlex.quote(tar_path)}"
            ),
            timeout=60,
        )
        _check(write, f"plugin install: chunk write failed at offset {offset}")

    extract = await exec_fn(
        sandbox_id,
        (
            f"cd {shlex.quote(install_dir)} && "
            f"tar -xzf {shlex.quote(tar_path)} && "
            f"rm -f {shlex.quote(tar_path)}"
        ),
        timeout=60,
    )
    _check(extract, "plugin install: bundle extract failed")

    if manifest.setup is not None:
        setup_cmd = (
            f"export EOS_PLUGIN_DIR={shlex.quote(install_dir)} && "
            f"chmod +x {shlex.quote(install_dir)}/setup.sh && "
            f"{shlex.quote(install_dir)}/setup.sh"
        )
        setup_run = await exec_fn(
            sandbox_id,
            setup_cmd,
            timeout=setup_timeout,
        )
        if getattr(setup_run, "exit_code", 1) != 0:
            raise PluginInstallError(
                f"plugin {manifest.name!r} setup.sh failed "
                f"(exit_code={getattr(setup_run, 'exit_code', 1)}): "
                f"{getattr(setup_run, 'stderr', '') or getattr(setup_run, 'stdout', '')}"
            )

    write_marker = await exec_fn(
        sandbox_id,
        f"printf %s {shlex.quote(digest)} > {shlex.quote(marker)}",
        timeout=10,
    )
    _check(write_marker, f"plugin install: marker write failed for {marker}")
    logger.info(
        "plugin install: %s sha=%s on %s",
        manifest.name,
        digest[:8],
        sandbox_id,
    )


def _check(result: Any, message: str) -> None:
    if getattr(result, "exit_code", 1) != 0:
        raise PluginInstallError(
            f"{message}: {getattr(result, 'stderr', '') or getattr(result, 'stdout', '')}"
        )
