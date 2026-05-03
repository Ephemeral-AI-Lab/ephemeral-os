"""Client for sandbox-local code-intelligence commands."""

from __future__ import annotations

import base64
import json
import logging
import textwrap
import threading
import time
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

from sandbox.api.transport import SandboxTransport
from sandbox.client.async_bridge import run_sync
from sandbox.code_intelligence.daemon.wire import (
    edit_request_to_dict,
    edit_result_from_dict,
    editspec_to_dict,
    normalize_edit_specs,
    normalize_write_specs,
    operation_change_to_dict,
    operation_result_from_dict,
    writespec_to_dict,
)
from sandbox.code_intelligence.core.types import (
    EditRequest,
    EditResult,
    EditSpec,
    OperationChange,
    OperationResult,
    WriteSpec,
)

logger = logging.getLogger(__name__)


class DaemonCommandError(Exception):
    """Raised when the daemon returns an ``ok=False`` command envelope."""

    def __init__(
        self,
        kind: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.details = details or {}


class DaemonCommandClient:
    """Transport-backed client for sandbox-local command dispatch."""

    is_initialized: bool = False

    def __init__(
        self,
        sandbox_id: str,
        workspace_root: str = "/workspace",
        *,
        transport: SandboxTransport,
    ) -> None:
        from sandbox.code_intelligence.daemon.launcher import DaemonLauncher

        self.sandbox_id = sandbox_id
        self.workspace_root = workspace_root
        self._transport = transport
        self._launcher = DaemonLauncher(transport, sandbox_id, workspace_root)
        self.is_initialized = False
        self._init_lock = threading.Lock()

    def ensure_initialized(self, wait: bool = True) -> bool:
        del wait
        with self._init_lock:
            if self.is_initialized:
                return True
        run_sync(self._ensure_initialized_async())
        with self._init_lock:
            return self.is_initialized

    async def _ensure_initialized_async(self) -> None:
        """Upload the command runtime and mark the channel initialized."""
        await self._launcher.ensure_daemon()
        with self._init_lock:
            self.is_initialized = True

    def _call_sync(self, op: str, args: dict[str, Any] | None = None) -> Any:
        """Run one sandbox-local command synchronously."""
        return run_sync(self._call_daemon_command(op, args or {}))

    async def _call_async(
        self,
        op: str,
        args: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> Any:
        return await self._call_daemon_command(op, args or {}, timeout=timeout)

    async def _call_daemon_command(
        self,
        op: str,
        args: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> Any:
        """Run one command in the sandbox via ``SandboxTransport.exec``."""
        from sandbox.code_intelligence.daemon.launcher import DaemonUnavailable

        started = time.perf_counter()
        try:
            result = await self._call_daemon_once(
                self._launcher,
                op,
                args or {},
                timeout=timeout,
            )
            logger.debug(
                "ci daemon command done: op=%s elapsed=%.3fs retry=false",
                op,
                time.perf_counter() - started,
            )
            return result
        except (ConnectionRefusedError, BrokenPipeError, FileNotFoundError, OSError):
            retry_started = time.perf_counter()
            await self._launcher.ensure_daemon()
            logger.debug(
                "ci daemon command retry after ensure_daemon: "
                "op=%s ensure_elapsed=%.3fs",
                op,
                time.perf_counter() - retry_started,
            )
            try:
                result = await self._call_daemon_once(
                    self._launcher,
                    op,
                    args or {},
                    timeout=timeout,
                )
                logger.debug(
                    "ci daemon command done: op=%s elapsed=%.3fs retry=true",
                    op,
                    time.perf_counter() - started,
                )
                return result
            except (
                ConnectionRefusedError,
                BrokenPipeError,
                FileNotFoundError,
                OSError,
            ) as exc:
                raise DaemonUnavailable(
                    f"daemon unreachable after respawn: {exc}"
                ) from exc

    async def _call_daemon_once(
        self,
        launcher: Any,
        op: str,
        args: dict[str, Any],
        *,
        timeout: float,
    ) -> Any:
        await launcher.ensure_daemon()
        response = await self._run_command_via_process_exec(op, args, timeout=timeout)
        logger.debug(
            "ci daemon command_once: op=%s ok=%s",
            op,
            response.get("ok"),
        )
        if not response.get("ok"):
            error = response.get("error") or {}
            raise DaemonCommandError(
                kind=str(error.get("kind") or "InternalError"),
                message=str(error.get("message") or ""),
                details=error.get("details")
                if isinstance(error.get("details"), dict)
                else {},
            )
        return response.get("result")

    async def _run_command_via_process_exec(
        self, op: str, args: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        """Run the command module in the sandbox and decode its response."""
        payload = {"op": op, "args": args}
        encoded = base64.b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        script = textwrap.dedent(
            f"""
            import base64
            import json
            import sys
            from sandbox.code_intelligence.daemon.command import run_command

            payload = json.loads(base64.b64decode({encoded!r}).decode("utf-8"))
            response = run_command(
                workspace_root={self.workspace_root!r},
                op=str(payload["op"]),
                args=dict(payload.get("args") or {{}}),
            )
            raw = json.dumps(response, separators=(",", ":")).encode("utf-8")
            sys.stdout.write(base64.b64encode(raw).decode("ascii"))
            """
        ).strip()
        command = (
            f"cd /tmp/eos-ci-runtime && "
            f"python3 - <<'PY'\n{script}\nPY"
        )
        result = await self._transport.exec(
            self.sandbox_id,
            command,
            timeout=max(1, int(timeout) + 5),
        )
        stdout = (getattr(result, "stdout", "") or "").strip()
        if getattr(result, "exit_code", 1) != 0:
            raise ConnectionRefusedError(stdout)
        try:
            decoded = json.loads(base64.b64decode(stdout).decode("utf-8"))
        except Exception as exc:
            raise ConnectionRefusedError(
                f"daemon command produced invalid response: {stdout!r}"
            ) from exc
        if not isinstance(decoded, dict):
            raise ConnectionRefusedError(
                f"daemon command produced non-object response: {decoded!r}"
            )
        return decoded

    def warmup(self) -> None:
        self.ensure_initialized(wait=True)

    def rebind_sandbox(self, sandbox: Any) -> None:
        del sandbox
        return None

    async def cmd(self, sandbox: Any, command: str, **kwargs: Any) -> Any:
        del sandbox
        on_progress_line = kwargs.pop("on_progress_line", None)
        timeout = kwargs.get("timeout")
        command_timeout = float(timeout if timeout is not None else 600) + 30.0
        command_started = time.perf_counter()
        raw = await self._call_async(
            "svc_cmd",
            {"command": command, **kwargs},
            timeout=command_timeout,
        )
        command_elapsed = round(time.perf_counter() - command_started, 6)
        result = SimpleNamespace(**(raw or {}))
        result.daemon_call_timings = {"total": command_elapsed}
        if on_progress_line is not None:
            progress_text = str(getattr(result, "result", "") or "")
            if progress_text:
                on_progress_line(progress_text)
        return result

    def apply_edit(self, request: EditRequest) -> EditResult:
        result = self._call_sync("apply_edit", {"request": edit_request_to_dict(request)})
        return edit_result_from_dict(result)

    def commit_operation_against_base(
        self,
        changes: Sequence[OperationChange],
        *,
        agent_id: str = "",
        edit_type: str,
        description: str = "",
    ) -> OperationResult:
        result = self._call_sync(
            "commit_operation_against_base",
            {
                "changes": [operation_change_to_dict(c) for c in changes],
                "agent_id": agent_id,
                "edit_type": edit_type,
                "description": description,
            },
        )
        return operation_result_from_dict(result)

    def commit_specs_many(
        self,
        requests: Sequence[dict[str, Any]],
    ) -> list[OperationResult]:
        rows = self._call_sync("commit_specs_many", {"requests": list(requests)})
        return [operation_result_from_dict(r) for r in (rows or [])]

    def write_file(
        self,
        specs: Sequence[WriteSpec] | WriteSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        normalized = normalize_write_specs(specs)
        result = self._call_sync(
            "write_file",
            {
                "specs": [writespec_to_dict(s) for s in normalized],
                "agent_id": agent_id,
                "description": description,
            },
        )
        return operation_result_from_dict(result)

    def edit_file(
        self,
        specs: Sequence[EditSpec] | EditSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        normalized = normalize_edit_specs(specs)
        result = self._call_sync(
            "edit_file",
            {
                "specs": [editspec_to_dict(s) for s in normalized],
                "agent_id": agent_id,
                "description": description,
            },
        )
        return operation_result_from_dict(result)


    def undo_last_edit(self, file_path: str) -> EditResult:
        result = self._call_sync("undo_last_edit", {"file_path": file_path})
        return edit_result_from_dict(result)

    def dispose(self) -> None:
        try:
            run_sync(self._launcher.shutdown())
        except Exception:
            logger.debug(
                "CI daemon shutdown skipped for sandbox %s",
                self.sandbox_id,
                exc_info=True,
            )
