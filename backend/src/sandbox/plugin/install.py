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
import os
import shlex
import subprocess
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
# Binary chunks are base64-encoded one at a time. Keep encoded commands below
# common argv limits while avoiding thousands of provider exec calls for large
# LSP runtime archives.
_BINARY_UPLOAD_CHUNK_SIZE = 48 * 1024
# 600s headroom for plugin setup scripts that pip-install Python deps over
# the network. Pure-Python servers like pylsp install in ~30-60s; this
# leaves slack for slow networks but stays within Daytona's exec timeout.
_DEFAULT_SETUP_TIMEOUT = 600
_LSP_NODE_VERSION = "22.13.1"
_LSP_PYRIGHT_VERSION = "1.1.409"


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
        setup_env = await _upload_setup_assets(
            exec_fn,
            sandbox_id=sandbox_id,
            manifest=manifest,
            install_dir=install_dir,
        )
        setup_exports = " ".join(
            f"export {name}={shlex.quote(value)} &&"
            for name, value in setup_env.items()
        )
        setup_cmd = (
            f"export EOS_PLUGIN_DIR={shlex.quote(install_dir)} && "
            f"{setup_exports} "
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


async def _upload_setup_assets(
    exec_fn: _RawExecCallable,
    *,
    sandbox_id: str,
    manifest: PluginManifest,
    install_dir: str,
) -> dict[str, str]:
    if manifest.name != "lsp":
        return {}
    env: dict[str, str] = {}
    if os.getenv("EOS_LSP_SKIP_HOST_NODE_UPLOAD") == "1":
        env.update(_lsp_sandbox_download_env())
    else:
        try:
            remote_archive = await _upload_lsp_node_archive(
                exec_fn,
                sandbox_id=sandbox_id,
                install_dir=install_dir,
            )
        except PluginInstallError as exc:
            logger.warning(
                "plugin install: lsp node archive unavailable; "
                "setup will download Node in sandbox: %s",
                exc,
            )
            env.update(_lsp_sandbox_download_env())
        else:
            env["EOS_NODE_ARCHIVE"] = remote_archive

    try:
        remote_package = await _upload_lsp_pyright_package(
            exec_fn,
            sandbox_id=sandbox_id,
            install_dir=install_dir,
        )
    except PluginInstallError as exc:
        logger.warning(
            "plugin install: pyright package unavailable; "
            "setup will download Pyright in sandbox: %s",
            exc,
        )
        env["EOS_LSP_ALLOW_DOWNLOAD"] = "1"
    else:
        env["EOS_PYRIGHT_PACKAGE"] = remote_package
    return env


def _lsp_sandbox_download_env() -> dict[str, str]:
    env = {"EOS_LSP_ALLOW_DOWNLOAD": "1"}
    download_urls = os.getenv("EOS_NODE_DOWNLOAD_URLS")
    if download_urls:
        env["EOS_NODE_DOWNLOAD_URLS"] = download_urls
    return env


async def _upload_lsp_node_archive(
    exec_fn: _RawExecCallable,
    *,
    sandbox_id: str,
    install_dir: str,
) -> str:
    arch = await _sandbox_node_arch(exec_fn, sandbox_id)
    archive = _ensure_lsp_node_archive(arch)
    remote_dir = f"{install_dir}/vendor/node"
    remote_archive = f"{remote_dir}/{archive.name}"
    await _upload_file(
        exec_fn,
        sandbox_id=sandbox_id,
        local_path=archive,
        remote_path=remote_archive,
        timeout=60,
    )
    return remote_archive


async def _upload_lsp_pyright_package(
    exec_fn: _RawExecCallable,
    *,
    sandbox_id: str,
    install_dir: str,
) -> str:
    package = _ensure_lsp_pyright_package()
    remote_dir = f"{install_dir}/vendor/pyright"
    remote_package = f"{remote_dir}/{package.name}"
    await _upload_file(
        exec_fn,
        sandbox_id=sandbox_id,
        local_path=package,
        remote_path=remote_package,
        timeout=60,
    )
    return remote_package


async def _sandbox_node_arch(
    exec_fn: _RawExecCallable,
    sandbox_id: str,
) -> str:
    result = await exec_fn(sandbox_id, "uname -m", timeout=10)
    if getattr(result, "exit_code", 1) != 0:
        raise PluginInstallError(
            "plugin lsp install: failed to detect sandbox architecture: "
            f"{getattr(result, 'stderr', '') or getattr(result, 'stdout', '')}"
        )
    machine = (getattr(result, "stdout", "") or "").strip()
    if machine == "x86_64":
        return "x64"
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    raise PluginInstallError(
        f"plugin lsp install: unsupported sandbox architecture {machine!r}"
    )


def _ensure_lsp_node_archive(node_arch: str) -> Path:
    override = os.getenv("EOS_LSP_NODE_ARCHIVE")
    if override:
        archive = Path(override).expanduser().resolve()
        if not archive.is_file():
            raise PluginInstallError(
                f"EOS_LSP_NODE_ARCHIVE does not exist: {archive}"
            )
        return archive

    version = os.getenv("EOS_NODE_VERSION", _LSP_NODE_VERSION)
    archive_name = f"node-v{version}-linux-{node_arch}.tar.xz"
    cache_root = Path(
        os.getenv(
            "EOS_LSP_NODE_CACHE_DIR",
            str(Path.home() / ".cache" / "ephemeralos" / "lsp-node"),
        )
    ).expanduser()
    archive = cache_root / archive_name
    if archive.is_file() and archive.stat().st_size > 0:
        return archive

    download_urls = os.getenv("EOS_LSP_NODE_DOWNLOAD_URLS")
    if download_urls:
        urls = download_urls.split()
    else:
        urls = [
            f"https://nodejs.org/dist/v{version}/{archive_name}",
            f"https://registry.npmmirror.com/-/binary/node/v{version}/{archive_name}",
        ]
    _download_lsp_node_archive(urls, archive)
    return archive


def _ensure_lsp_pyright_package() -> Path:
    override = os.getenv("EOS_LSP_PYRIGHT_PACKAGE")
    if override:
        package = Path(override).expanduser().resolve()
        if not package.is_file():
            raise PluginInstallError(
                f"EOS_LSP_PYRIGHT_PACKAGE does not exist: {package}"
            )
        return package

    version = os.getenv("EOS_PYRIGHT_VERSION", _LSP_PYRIGHT_VERSION)
    package_name = f"pyright-{version}.tgz"
    cache_root = Path(
        os.getenv(
            "EOS_LSP_PYRIGHT_CACHE_DIR",
            str(Path.home() / ".cache" / "ephemeralos" / "lsp-pyright"),
        )
    ).expanduser()
    package = cache_root / package_name
    if package.is_file() and package.stat().st_size > 0:
        return package

    _pack_lsp_pyright_package(version, cache_root, package_name)
    return package


def _pack_lsp_pyright_package(
    version: str,
    cache_root: Path,
    package_name: str,
) -> None:
    cache_root.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            "npm",
            "pack",
            f"pyright@{version}",
            "--silent",
            "--pack-destination",
            str(cache_root),
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )
    package = cache_root / package_name
    if completed.returncode == 0 and package.is_file() and package.stat().st_size > 0:
        return
    detail = completed.stderr.strip() or completed.stdout.strip()
    raise PluginInstallError(
        f"failed to pack Pyright npm package {version}: {detail or 'empty package'}"
    )


def _download_lsp_node_archive(urls: list[str], archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = archive.with_suffix(archive.suffix + ".tmp")
    errors: list[str] = []
    for url in urls:
        completed = subprocess.run(
            [
                "curl",
                "-fL",
                "--retry",
                "3",
                "--connect-timeout",
                "20",
                "--max-time",
                "600",
                url,
                "-o",
                str(tmp_path),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode == 0 and tmp_path.is_file() and tmp_path.stat().st_size > 0:
            tmp_path.replace(archive)
            return
        error = completed.stderr.strip() or completed.stdout.strip()
        errors.append(f"{url}: {error or 'empty download'}")
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    detail = "; ".join(errors) if errors else "no URLs configured"
    raise PluginInstallError(f"failed to download LSP Node archive: {detail}")


async def _upload_file(
    exec_fn: _RawExecCallable,
    *,
    sandbox_id: str,
    local_path: Path,
    remote_path: str,
    timeout: int,
) -> None:
    remote_dir = str(Path(remote_path).parent)
    prepare = await exec_fn(
        sandbox_id,
        f"mkdir -p {shlex.quote(remote_dir)} && : > {shlex.quote(remote_path)}",
        timeout=timeout,
    )
    _check(prepare, f"plugin install: failed to prepare {remote_path}")

    offset = 0
    with local_path.open("rb") as src:
        while True:
            chunk = src.read(_BINARY_UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            encoded = base64.b64encode(chunk).decode("ascii")
            write = await exec_fn(
                sandbox_id,
                (
                    f"printf %s {shlex.quote(encoded)} | base64 -d "
                    f">> {shlex.quote(remote_path)}"
                ),
                timeout=timeout,
            )
            _check(write, f"plugin install: asset write failed at offset {offset}")
            offset += len(chunk)


def _check(result: Any, message: str) -> None:
    if getattr(result, "exit_code", 1) != 0:
        raise PluginInstallError(
            f"{message}: {getattr(result, 'stderr', '') or getattr(result, 'stdout', '')}"
        )
