"""Bundle helper + idempotent uploader for the in-sandbox CI runtime.

The bundle is a tar.gz containing the minimal set of project + vendored
modules needed to run ``python -m sandbox.code_intelligence.in_sandbox.ci_index``
inside a sandbox: the entire ``sandbox/code_intelligence/`` tree, the
transitive ``sandbox.api``/``sandbox.client.async_bridge``/``sandbox.lifecycle.commit``
imports it pulls in, plus a vendored pure-Python ``msgpack/`` so the
sandbox image does not need ``pip install``.

Phase 0 already added ``msgpack>=1.0.0`` to ``[project.dependencies]`` so the
vendored copy is sourced from the orchestrator's own venv at bundle-build
time.

The companion :func:`ensure_runtime_uploaded` extracts the bundle under
``/tmp/eos-ci-runtime/`` once per ``(transport, sandbox_id)`` pair; subsequent
calls no-op when the previously-recorded ``.bundle-hash`` marker matches.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import logging
import shlex
import tarfile
from pathlib import Path

from sandbox.api.transport import SandboxTransport

__all__ = [
    "BUNDLE_REMOTE_DIR",
    "ensure_runtime_uploaded",
    "_ci_runtime_bundle_bytes",
]

logger = logging.getLogger(__name__)

BUNDLE_REMOTE_DIR = "/tmp/eos-ci-runtime"
"""Remote directory the bundle is extracted into."""

_BUNDLE_HASH_MARKER = f"{BUNDLE_REMOTE_DIR}/.bundle-hash"


def _src_root() -> Path:
    """Return the orchestrator's ``backend/src/`` directory.

    ``__file__`` is at
    ``backend/src/sandbox/code_intelligence/rpc/launcher.py``, so four
    ``.parent`` hops climb back up to ``backend/src/``.
    """
    return Path(__file__).resolve().parent.parent.parent.parent


def _msgpack_dir() -> Path:
    """Locate the orchestrator's installed ``msgpack/`` package."""
    import msgpack  # noqa: PLC0415 — lazy: only used at bundle-build time.

    return Path(msgpack.__file__).resolve().parent


def _is_excluded(path: Path) -> bool:
    parts = set(path.parts)
    return "__pycache__" in parts or path.suffix in {".pyc", ".pyo"}


def _normalize_tarinfo(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Strip per-environment metadata so the bundle hashes deterministically."""
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    # Mode normalization keeps the bundle hash stable across orchestrators
    # with different umasks / filesystem ACLs. We bundle .py source files
    # only, so 0o644 is the right canonical mode.
    info.mode = 0o644
    return info


_BUNDLE_CACHE: bytes | None = None


def _ci_runtime_bundle_bytes() -> bytes:
    """Build the in-sandbox runtime bundle as a gzip tarball.

    The result is memoized per orchestrator process — the bundle builds
    deterministically from on-disk source, and rebuilding on every call
    dominates the warm-bundle-upload SLO (the marker check completes in
    ~300 ms but the tarball build is multiple seconds of disk + gzip).

    Layout (inside the tarball):

    * ``msgpack/**/*.py``                                    (vendored, pure Python)
    * ``sandbox/__init__.py`` + ``sandbox/errors.py``
    * ``sandbox/api/**/*.py``
    * ``sandbox/client/__init__.py`` + ``sandbox/client/async_bridge.py``
    * ``sandbox/lifecycle/__init__.py`` + ``sandbox/lifecycle/commit.py``
    * ``sandbox/code_intelligence/**/*.py``                  (full tree)
    """
    global _BUNDLE_CACHE
    if _BUNDLE_CACHE is not None:
        return _BUNDLE_CACHE

    src = _src_root()
    sandbox_dir = src / "sandbox"
    msgpack_dir = _msgpack_dir()

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        # --- Vendored msgpack (pure-Python only, skip compiled .so) -----------
        msgpack_parent = msgpack_dir.parent
        for path in sorted(msgpack_dir.rglob("*.py")):
            if _is_excluded(path):
                continue
            tar.add(
                path,
                arcname=path.relative_to(msgpack_parent).as_posix(),
                filter=_normalize_tarinfo,
            )

        # --- sandbox/ root --------------------------------------------------
        for filename in ("__init__.py", "errors.py"):
            p = sandbox_dir / filename
            if p.exists():
                tar.add(p, arcname=f"sandbox/{filename}", filter=_normalize_tarinfo)

        # --- sandbox/api -----------------------------------------------------
        for path in sorted((sandbox_dir / "api").rglob("*.py")):
            if _is_excluded(path):
                continue
            tar.add(
                path,
                arcname=f"sandbox/{path.relative_to(sandbox_dir).as_posix()}",
                filter=_normalize_tarinfo,
            )

        # --- sandbox/client (only async_bridge + __init__) -------------------
        client_dir = sandbox_dir / "client"
        for filename in ("__init__.py", "async_bridge.py"):
            p = client_dir / filename
            if p.exists():
                tar.add(
                    p,
                    arcname=f"sandbox/client/{filename}",
                    filter=_normalize_tarinfo,
                )

        # --- sandbox/lifecycle (only __init__ + commit) ----------------------
        lifecycle_dir = sandbox_dir / "lifecycle"
        for filename in ("__init__.py", "commit.py"):
            p = lifecycle_dir / filename
            if p.exists():
                tar.add(
                    p,
                    arcname=f"sandbox/lifecycle/{filename}",
                    filter=_normalize_tarinfo,
                )

        # --- sandbox/code_intelligence (full tree) ---------------------------
        ci_dir = sandbox_dir / "code_intelligence"
        for path in sorted(ci_dir.rglob("*.py")):
            if _is_excluded(path):
                continue
            tar.add(
                path,
                arcname=f"sandbox/{path.relative_to(sandbox_dir).as_posix()}",
                filter=_normalize_tarinfo,
            )

    # Gzip with a fixed mtime so the bundle hash is deterministic — required
    # for the .bundle-hash idempotency check.
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    _BUNDLE_CACHE = compressed.getvalue()
    return _BUNDLE_CACHE


_BUNDLE_HASH_CACHE: str | None = None


def bundle_hash(bundle: bytes | None = None) -> str:
    """Stable hex digest of the runtime bundle (used for upload idempotency).

    Memoized per-process when the caller doesn't pass an explicit bundle —
    the bundle is itself memoized via :data:`_BUNDLE_CACHE`, so the hash is
    only computed once per orchestrator lifecycle. This collapses the
    ``ensure_runtime_uploaded`` warm path from ~5 s of SHA-256-on-100KB
    work to a dictionary lookup.
    """
    global _BUNDLE_HASH_CACHE
    if bundle is None:
        if _BUNDLE_HASH_CACHE is not None:
            return _BUNDLE_HASH_CACHE
        bundle = _ci_runtime_bundle_bytes()
        _BUNDLE_HASH_CACHE = hashlib.sha256(bundle).hexdigest()
        return _BUNDLE_HASH_CACHE
    return hashlib.sha256(bundle).hexdigest()


_BUNDLE_REMOTE_TARBALL = f"{BUNDLE_REMOTE_DIR}/bundle.tar.gz"
_BUNDLE_REMOTE_B64 = f"{BUNDLE_REMOTE_DIR}/bundle.tar.gz.b64"

# Each base64 chunk we ship via a single ``exec`` call. Daytona's exec
# pathway rejects very large argv strings; 32 KB per chunk fits inside
# every observed limit and keeps the upload to <10 round-trips for a
# ~100 KB bundle. The matching memory:
# `'checked batch apply failed' = argv E2BIG`.
_CHUNK_SIZE = 32 * 1024


async def read_remote_file_via_exec(
    transport: SandboxTransport, sandbox_id: str, path: str
) -> bytes:
    """Download ``path`` from the sandbox via chunked base64 over ``exec``.

    Daytona's ``fs.download_file`` (the ``bulk-download`` endpoint) returns
    intermittent 502 Bad Gateway errors at ~30 KB+ payloads. ``transport.exec``
    is the most reliable verb on the same proxy, so we lean on it for
    binary-file reads as well as writes.

    Strategy:

    * ``wc -c <path>`` resolves the size up front.
    * For each chunk window we run ``tail -c +<start> | head -c <n> | base64 -w0``
      and decode the stdout. Chunks are 32 KB so they fit comfortably under
      any stdout-capture limit.
    """
    import base64

    size_result = await transport.exec(
        sandbox_id,
        f"wc -c < {shlex.quote(path)}",
        timeout=30,
    )
    if getattr(size_result, "exit_code", 1) != 0:
        raise FileNotFoundError(path)
    try:
        size = int((getattr(size_result, "stdout", "") or "").strip())
    except ValueError as exc:
        raise RuntimeError(
            f"could not read remote size for {path!r}: "
            f"{getattr(size_result, 'stdout', '')!r}"
        ) from exc

    if size == 0:
        return b""

    # ``tail -c +N | head -c M`` would be the natural pattern, but
    # ``wrap_bash_command`` enables ``set -o pipefail`` and ``head`` closes
    # its stdin once it has read M bytes, sending SIGPIPE to ``tail``
    # (exit 141). Pipefail then poisons the whole pipeline. ``dd`` reads
    # exactly ``bs`` bytes per record without truncating its output, so
    # it pairs cleanly with ``base64 -w0``.
    chunks: list[bytes] = []
    chunk_index = 0
    while chunk_index * _CHUNK_SIZE < size:
        chunk_cmd = (
            f"dd if={shlex.quote(path)} bs={_CHUNK_SIZE} count=1 "
            f"skip={chunk_index} status=none | base64 -w0"
        )
        result = await transport.exec(sandbox_id, chunk_cmd, timeout=60)
        if getattr(result, "exit_code", 1) != 0:
            raise RuntimeError(
                f"chunked read failed at chunk {chunk_index} for {path!r} "
                f"(sandbox={sandbox_id!r}): {getattr(result, 'stdout', '')}"
            )
        encoded = (getattr(result, "stdout", "") or "").strip()
        chunks.append(base64.b64decode(encoded))
        chunk_index += 1
    return b"".join(chunks)


async def ensure_runtime_uploaded(
    transport: SandboxTransport, sandbox_id: str
) -> str:
    """Upload the runtime bundle to ``/tmp/eos-ci-runtime/`` if needed.

    Idempotent: when ``.bundle-hash`` already exists with a matching digest,
    no upload occurs. Returns the bundle hash so callers can correlate logs.

    Implementation note — the bundle is streamed as **chunked base64 over
    repeated ``transport.exec`` calls**. Two earlier attempts were tried
    and rejected:

    1. Inlining base64 in a single argv string blew past ``ARG_MAX`` once
       the bundle plus shell escaping crossed ~100 KB.
    2. ``transport.write_bytes`` (Daytona ``fs.upload_file``) returned
       ``502 Bad Gateway`` from Daytona's proxy on every attempt.

    Chunked-base64 is the third approach: each chunk is small, the upload
    is incremental (so partial failures are recoverable), and it depends
    only on ``transport.exec``, which is the most reliable verb.
    """
    # Hash before bytes: hash is memoized per-process, so the warm path can
    # short-circuit on a hit without rebuilding the tarball at all. Only
    # rebuild bytes when we're actually going to upload.
    digest = bundle_hash()
    marker_check = await transport.exec(
        sandbox_id,
        f"test -f {shlex.quote(_BUNDLE_HASH_MARKER)} && cat {shlex.quote(_BUNDLE_HASH_MARKER)}",
    )
    existing = (getattr(marker_check, "stdout", "") or "").strip()
    if getattr(marker_check, "exit_code", 1) == 0 and existing == digest:
        logger.debug(
            "ci runtime bundle already uploaded (%s) on %s", digest[:8], sandbox_id
        )
        return digest

    import base64

    bundle = _ci_runtime_bundle_bytes()
    encoded = base64.b64encode(bundle).decode("ascii")

    # Stage: ensure dir + truncate prior b64 staging file.
    setup = await transport.exec(
        sandbox_id,
        (
            f"mkdir -p {shlex.quote(BUNDLE_REMOTE_DIR)} && "
            f": > {shlex.quote(_BUNDLE_REMOTE_B64)}"
        ),
        timeout=30,
    )
    if getattr(setup, "exit_code", 1) != 0:
        raise RuntimeError(
            f"runtime bundle staging mkdir failed (sandbox={sandbox_id!r}): "
            f"{getattr(setup, 'stdout', '')}"
        )

    # Stream the base64 string in chunks via repeated `printf >> file`.
    for i in range(0, len(encoded), _CHUNK_SIZE):
        chunk = encoded[i : i + _CHUNK_SIZE]
        write_cmd = (
            f"printf %s {shlex.quote(chunk)} >> {shlex.quote(_BUNDLE_REMOTE_B64)}"
        )
        result = await transport.exec(sandbox_id, write_cmd, timeout=60)
        if getattr(result, "exit_code", 1) != 0:
            raise RuntimeError(
                f"runtime bundle chunk write failed at offset {i} "
                f"(sandbox={sandbox_id!r}): {getattr(result, 'stdout', '')}"
            )

    # Decode + extract + atomically install hash marker.
    finalize_cmd = (
        f"cd {shlex.quote(BUNDLE_REMOTE_DIR)} && "
        f"base64 -d {shlex.quote(_BUNDLE_REMOTE_B64)} > {shlex.quote(_BUNDLE_REMOTE_TARBALL)} && "
        f"tar -xzf {shlex.quote(_BUNDLE_REMOTE_TARBALL)} && "
        f"rm -f {shlex.quote(_BUNDLE_REMOTE_TARBALL)} {shlex.quote(_BUNDLE_REMOTE_B64)} && "
        f"printf %s {shlex.quote(digest)} > {shlex.quote(_BUNDLE_HASH_MARKER)}"
    )
    result = await transport.exec(sandbox_id, finalize_cmd, timeout=60)
    if getattr(result, "exit_code", 1) != 0:
        raise RuntimeError(
            f"runtime bundle upload failed (sandbox={sandbox_id!r}): "
            f"{getattr(result, 'stdout', '')}"
        )
    logger.info(
        "ci runtime bundle uploaded (%d bytes, %d chunks, sha=%s) to %s",
        len(bundle),
        (len(encoded) + _CHUNK_SIZE - 1) // _CHUNK_SIZE,
        digest[:8],
        sandbox_id,
    )
    return digest
