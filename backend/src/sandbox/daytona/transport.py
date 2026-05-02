"""SandboxTransport implementation backed by the AsyncDaytona SDK.

This is the single point of provider coupling for tools and CI internals
(after Step 5). Every call routes through the AsyncDaytona client
acquired via :func:`sandbox.client.async_.get_async_sandbox`.

* ``apply_diff_batch_checked`` supports deletes (``CheckedWriteSpec.content
  is None``) and stages large payloads via tmp-file chunked uploads so it
  does not regress on ``ContentManager``-scale batches.
* ``read_bytes_batch`` issues one Daytona ``download_files`` call when
  the SDK exposes it, falling back to per-path reads otherwise. Mitigates
  the indexing perf regression risk called out in the migration plan.
"""

from __future__ import annotations

import base64
import json
import logging
import shlex
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, ClassVar

from sandbox.api.errors import SandboxTransportError
from sandbox.api.models import (
    CheckedWriteResult,
    CheckedWriteSpec,
    RawExecResult,
    SearchMatch,
)
from sandbox.api.bash import extract_exit_code, wrap_bash_command
from sandbox.api.file_commands import REMOTE_WRITE_CHUNK_BYTES
from sandbox.client.async_ import get_async_sandbox
from sandbox.daytona.search_commands import build_glob_command, build_grep_command

logger = logging.getLogger(__name__)


_INLINE_PAYLOAD_LIMIT_BYTES = REMOTE_WRITE_CHUNK_BYTES


class DaytonaTransport:
    """Daytona-backed implementation of :class:`SandboxTransport`."""

    name: ClassVar[str] = "daytona"

    def __init__(
        self,
        *,
        sandbox_resolver: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        self._resolver = sandbox_resolver or get_async_sandbox

    # -- exec / process ------------------------------------------------

    async def exec(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> RawExecResult:
        sandbox = await self._resolve(sandbox_id)
        wrapped = wrap_bash_command(command, cwd=cwd)
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            response = await sandbox.process.exec(wrapped, **kwargs)
        except Exception as exc:
            raise SandboxTransportError(
                f"daytona exec failed (sandbox={sandbox_id}): {exc}"
            ) from exc
        stdout, exit_code = extract_exit_code(
            getattr(response, "result", "") or "",
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        return RawExecResult(exit_code=exit_code, stdout=stdout)

    # -- bytes I/O -----------------------------------------------------

    async def read_bytes(self, sandbox_id: str, path: str) -> bytes:
        sandbox = await self._resolve(sandbox_id)
        fs = getattr(sandbox, "fs", None)
        download = getattr(fs, "download_file", None)
        if not callable(download):
            raise SandboxTransportError(
                "daytona read_bytes requires sandbox.fs.download_file"
            )
        try:
            payload = await download(path)
        except FileNotFoundError:
            raise
        except Exception as exc:
            raise SandboxTransportError(
                f"daytona read_bytes failed for {path}: {exc}"
            ) from exc
        if isinstance(payload, bytes):
            return payload
        if isinstance(payload, str):
            return payload.encode("utf-8")
        return bytes(payload)

    async def read_bytes_batch(
        self,
        sandbox_id: str,
        paths: Sequence[str],
    ) -> dict[str, bytes | None]:
        if not paths:
            return {}
        sandbox = await self._resolve(sandbox_id)
        fs = getattr(sandbox, "fs", None)
        download_files = getattr(fs, "download_files", None)
        if callable(download_files):
            try:
                from daytona_sdk.common.filesystem import (
                    FileDownloadRequest,  # type: ignore[import-not-found]
                )
            except ImportError:
                return await self._read_bytes_batch_fallback(sandbox_id, paths)
            try:
                requests = [FileDownloadRequest(source=p) for p in paths]
                responses = await download_files(requests)
            except Exception as exc:
                raise SandboxTransportError(
                    f"daytona read_bytes_batch failed: {exc}"
                ) from exc
            result: dict[str, bytes | None] = {}
            for resp in responses or ():
                source = getattr(resp, "source", None)
                if not isinstance(source, str):
                    continue
                error = getattr(resp, "error", None)
                payload = getattr(resp, "result", None)
                if error or payload is None:
                    result[source] = None
                    continue
                if isinstance(payload, bytes):
                    result[source] = payload
                elif isinstance(payload, str):
                    result[source] = payload.encode("utf-8")
                else:
                    result[source] = bytes(payload)
            for path in paths:
                result.setdefault(path, None)
            return result
        return await self._read_bytes_batch_fallback(sandbox_id, paths)

    async def _read_bytes_batch_fallback(
        self,
        sandbox_id: str,
        paths: Sequence[str],
    ) -> dict[str, bytes | None]:
        """Per-path fallback when the SDK exposes no batch download."""
        result: dict[str, bytes | None] = {}
        for path in paths:
            try:
                result[path] = await self.read_bytes(sandbox_id, path)
            except FileNotFoundError:
                result[path] = None
        return result

    async def write_bytes(
        self,
        sandbox_id: str,
        path: str,
        content: bytes,
    ) -> None:
        sandbox = await self._resolve(sandbox_id)
        fs = getattr(sandbox, "fs", None)
        upload = getattr(fs, "upload_file", None)
        if not callable(upload):
            raise SandboxTransportError(
                "daytona write_bytes requires sandbox.fs.upload_file"
            )
        try:
            await upload(content, path)
        except Exception as exc:
            raise SandboxTransportError(
                f"daytona write_bytes failed for {path}: {exc}"
            ) from exc

    # -- batched checked apply -----------------------------------------

    async def apply_diff_batch_checked(
        self,
        sandbox_id: str,
        specs: Sequence[CheckedWriteSpec],
    ) -> CheckedWriteResult:
        if not specs:
            return CheckedWriteResult(success=True, written_paths=())
        ops = [
            {
                "path": spec.path,
                "expected_sha": spec.expected_sha,
                "content_b64": (
                    None
                    if spec.content is None
                    else base64.b64encode(spec.content).decode("ascii")
                ),
            }
            for spec in specs
        ]
        payload_bytes = json.dumps(ops).encode("utf-8")
        if len(payload_bytes) <= _INLINE_PAYLOAD_LIMIT_BYTES:
            return await self._apply_inline(sandbox_id, payload_bytes)
        return await self._apply_staged(sandbox_id, payload_bytes)

    async def _apply_inline(
        self, sandbox_id: str, payload_bytes: bytes,
    ) -> CheckedWriteResult:
        encoded = base64.b64encode(payload_bytes).decode("ascii")
        prelude = _INLINE_PRELUDE_TEMPLATE.replace("__PAYLOAD_B64__", encoded)
        script = prelude + _APPLY_SCRIPT_BODY
        result = await self.exec(sandbox_id, f"python3 -c {shlex.quote(script)}")
        return self._parse_apply_result(result)

    async def _apply_staged(
        self, sandbox_id: str, payload_bytes: bytes,
    ) -> CheckedWriteResult:
        tmp_path = await self._stage_remote_payload(sandbox_id, payload_bytes)
        try:
            script = _FROM_FILE_PRELUDE + _APPLY_SCRIPT_BODY
            result = await self.exec(
                sandbox_id,
                f"python3 -c {shlex.quote(script)} {shlex.quote(tmp_path)}",
            )
            return self._parse_apply_result(result)
        finally:
            await self._remove_remote_path(sandbox_id, tmp_path)

    async def _stage_remote_payload(
        self, sandbox_id: str, payload_bytes: bytes,
    ) -> str:
        tmp_path = f"/tmp/eos-checked-apply-{uuid.uuid4().hex}.json"
        truncate_script = (
            "import pathlib,sys; "
            "p=pathlib.Path(sys.argv[1]); "
            "p.parent.mkdir(parents=True, exist_ok=True); "
            'p.write_bytes(b"")'
        )
        truncate_cmd = (
            f"python3 -c {shlex.quote(truncate_script)} {shlex.quote(tmp_path)}"
        )
        res = await self.exec(sandbox_id, truncate_cmd)
        if res.exit_code not in (0, None):
            raise SandboxTransportError(
                f"stage payload truncate failed: {res.stdout[-200:]!r}"
            )
        append_script = (
            "import base64,pathlib,sys; "
            "p=pathlib.Path(sys.argv[1]); "
            'open(str(p), "ab").write(base64.b64decode(sys.argv[2]))'
        )
        chunk_size = _INLINE_PAYLOAD_LIMIT_BYTES
        for index in range(0, len(payload_bytes), chunk_size):
            chunk = payload_bytes[index : index + chunk_size]
            chunk_b64 = base64.b64encode(chunk).decode("ascii")
            cmd = (
                f"python3 -c {shlex.quote(append_script)} "
                f"{shlex.quote(tmp_path)} {shlex.quote(chunk_b64)}"
            )
            res = await self.exec(sandbox_id, cmd)
            if res.exit_code not in (0, None):
                await self._remove_remote_path(sandbox_id, tmp_path)
                raise SandboxTransportError(
                    f"stage payload chunk failed: {res.stdout[-200:]!r}"
                )
        return tmp_path

    async def _remove_remote_path(self, sandbox_id: str, path: str) -> None:
        try:
            await self.exec(sandbox_id, f"rm -f {shlex.quote(path)}")
        except Exception:
            logger.debug(
                "remote tmp cleanup failed for %s", path, exc_info=True,
            )

    @staticmethod
    def _parse_apply_result(result: RawExecResult) -> CheckedWriteResult:
        if result.exit_code not in (0, None):
            raise SandboxTransportError(
                f"checked batch apply failed: exit_code={result.exit_code} "
                f"stdout={result.stdout[-1000:]!r}"
            )
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise SandboxTransportError(
                f"checked batch apply returned invalid JSON: "
                f"{result.stdout[-1000:]!r}"
            ) from exc
        if payload.get("ok"):
            return CheckedWriteResult(
                success=True,
                written_paths=tuple(payload.get("written_paths") or ()),
            )
        conflict_path = str(payload.get("path") or "")
        return CheckedWriteResult(
            success=False,
            conflict_paths=(conflict_path,) if conflict_path else (),
            conflict_reason=str(payload.get("reason") or "failed"),
        )

    # -- search --------------------------------------------------------

    async def search(
        self,
        sandbox_id: str,
        pattern: str,
        *,
        root: str | None = None,
        include: str | None = None,
    ) -> Sequence[SearchMatch]:
        del include  # transport returns the full grep stream; callers filter
        target = root or "/"
        command = build_grep_command(root=target, pattern=pattern)
        result = await self.exec(sandbox_id, command, timeout=60)
        if result.exit_code not in (0, None):
            raise SandboxTransportError(
                f"daytona search failed (exit={result.exit_code}): "
                f"{result.stdout[-1000:]!r}"
            )
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise SandboxTransportError(
                f"daytona search returned invalid JSON: "
                f"{result.stdout[-1000:]!r}"
            ) from exc
        if not bool(payload.get("ok")):
            raise SandboxTransportError(
                f"daytona search reported failure: "
                f"{payload.get('error') or payload}"
            )
        matches: list[SearchMatch] = []
        for raw in payload.get("matches") or ():
            if not isinstance(raw, dict):
                continue
            file_path = str(raw.get("file") or "")
            line = int(raw.get("line") or 0)
            preview = str(raw.get("content") or "")
            if not file_path or not line:
                continue
            matches.append(
                SearchMatch(path=file_path, line=line, preview=preview),
            )
        return tuple(matches)

    async def list_paths(
        self,
        sandbox_id: str,
        glob: str,
        *,
        root: str | None = None,
    ) -> Sequence[str]:
        target = root or "/"
        command = build_glob_command(root=target, pattern=glob)
        result = await self.exec(sandbox_id, command, timeout=30)
        if result.exit_code not in (0, None):
            raise SandboxTransportError(
                f"daytona list_paths failed (exit={result.exit_code}): "
                f"{result.stdout[-1000:]!r}"
            )
        return tuple(
            line for line in (result.stdout or "").splitlines() if line.strip()
        )

    # -- ci daemon socket bridge --------------------------------------

    async def ci_rpc(
        self,
        sandbox_id: str,
        payload: bytes,
        *,
        socket_path: str,
        timeout: int | None = None,
    ) -> bytes:
        """Bridge ``payload`` to the in-sandbox CI daemon Unix socket.

        Co-located in the transport so the python shim's per-call wrap
        cost is amortized inside one ``transport.exec`` round-trip
        instead of being re-paid by ``CiRpcClient`` on every call.
        """
        request_b64 = base64.b64encode(payload).decode("ascii")
        socket_timeout = float(timeout if timeout is not None else 30)
        script = _CI_RPC_BRIDGE_TEMPLATE.format(
            payload_b64=request_b64,
            socket_path=repr(socket_path),
            socket_timeout=repr(socket_timeout),
        )
        exec_timeout = max(1, int(socket_timeout) + 5)
        result = await self.exec(
            sandbox_id,
            f"python3 - <<'PY'\n{script}\nPY",
            timeout=exec_timeout,
        )
        stdout = (result.stdout or "").strip()
        if result.exit_code not in (0, None):
            raise ConnectionRefusedError(stdout or "ci_rpc bridge failed")
        try:
            return base64.b64decode(stdout)
        except Exception as exc:
            raise ConnectionRefusedError(
                f"ci_rpc bridge produced invalid base64: {stdout[-200:]!r}"
            ) from exc

    # -- internals -----------------------------------------------------

    async def _resolve(self, sandbox_id: str) -> Any:
        try:
            return await self._resolver(sandbox_id)
        except Exception as exc:
            raise SandboxTransportError(
                f"daytona transport could not resolve sandbox "
                f"{sandbox_id!r}: {exc}"
            ) from exc


# -- Apply-script bodies ----------------------------------------------------
#
# Mirrors the semantics of ContentManager._apply_remote_batch_checked:
# verify each spec's expected sha, back up current contents, apply the
# batch atomically, restore on failure. Supports deletes via
# ``content_b64 is None``.

_APPLY_SCRIPT_BODY = '''
backups = []
for item in ops:
    path = pathlib.Path(item["path"])
    expected = item.get("expected_sha")
    try:
        current = path.read_bytes()
        existed = True
        current_sha = hashlib.sha256(current).hexdigest()[:16]
    except FileNotFoundError:
        existed = False
        current = b""
        current_sha = ""
    backups.append({
        "path": item["path"],
        "existed": existed,
        "content_b64": base64.b64encode(current).decode("ascii"),
    })
    if expected is None:
        if existed:
            print(json.dumps({"ok": False, "reason": "exists", "path": item["path"], "message": "file already exists"}))
            raise SystemExit(0)
    else:
        if (not existed) or current_sha != expected:
            print(json.dumps({"ok": False, "reason": "base_mismatch", "path": item["path"], "message": "file content changed before checked apply"}))
            raise SystemExit(0)

written = []
try:
    for item in ops:
        path = pathlib.Path(item["path"])
        if item.get("content_b64") is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            written.append(item["path"])
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(item["content_b64"]))
        written.append(item["path"])
except Exception as exc:
    for backup in reversed(backups):
        bpath = pathlib.Path(backup["path"])
        try:
            if backup["existed"]:
                bpath.parent.mkdir(parents=True, exist_ok=True)
                bpath.write_bytes(base64.b64decode(backup["content_b64"]))
            else:
                try:
                    bpath.unlink()
                except FileNotFoundError:
                    pass
        except Exception:
            pass
    print(json.dumps({"ok": False, "reason": "write_failed", "path": "", "message": str(exc)}))
    raise SystemExit(0)

print(json.dumps({"ok": True, "written_paths": written}))
'''

_INLINE_PRELUDE_TEMPLATE = '''
import base64
import hashlib
import json
import pathlib

ops = json.loads(base64.b64decode("__PAYLOAD_B64__").decode("utf-8"))
'''

_FROM_FILE_PRELUDE = '''
import base64
import hashlib
import json
import pathlib
import sys

ops = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
'''


# Inline socket bridge for ci_rpc. base64 in/out preserves every byte 0-255.
_CI_RPC_BRIDGE_TEMPLATE = '''import base64
import socket
import sys

frame = base64.b64decode("{payload_b64}")
sock = socket.socket(socket.AF_UNIX)
sock.settimeout({socket_timeout})
try:
    sock.connect({socket_path})
except Exception as exc:
    sys.stderr.write("ci_rpc connect failed: " + repr(exc))
    raise SystemExit(1)
sock.sendall(frame)
sock.shutdown(socket.SHUT_WR)
chunks = []
while True:
    data = sock.recv(65536)
    if not data:
        break
    chunks.append(data)
sock.close()
sys.stdout.write(base64.b64encode(b"".join(chunks)).decode("ascii"))
'''


__all__ = ["DaytonaTransport"]
