"""OCC-gated overlay-based command auditor.

Runs one sandbox command inside a per-run user+mount namespace with an
overlayfs whose upperdir captures the command's writes in isolation.
Upperdir changes are applied back to ``repo_root`` as one OCC batch via
:meth:`WriteCoordinator.commit_operation_against_base`, so codeact
mutations share the same single OCC boundary as typed edits / writes /
renames.

Scope of v1:

* ``MODIFY`` and ``DELETE`` upperdir entries commit through the
  coordinator with ``strict_base=True`` — any lowerdir/workspace drift
  between overlay mount and commit aborts the whole batch with
  ``aborted_version`` and leaves disk unchanged.
* ``SYMLINK`` and ``OPAQUE_DIR`` are rejected with
  :class:`OverlayUnsupportedChangeError` (D3a). Widening
  ``OperationChange`` to represent those kinds is out of scope for v1.

The auditor interface mirrors the pre-OCC shape so callers see the same
``SimpleNamespace(result, exit_code, changed_paths, ...)`` they
received from the legacy process auditor.
"""

from __future__ import annotations

import base64
import inspect
import json
import logging
import os
import posixpath
import shlex
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from tools.daytona_toolkit._daytona_utils import _extract_exit_code, _wrap_bash_command

from code_intelligence.hashing import content_hash
from code_intelligence.routing.overlay_exec import (
    OverlayExec,
    OverlayExecError,
    OverlayMountError,
    OverlayRunResult,
)
from code_intelligence.routing.upperdir_walker import (
    ChangeKind,
    UpperdirChange,
    cleanup_tar,
    iter_upperdir_changes,
)
from code_intelligence.types import OperationChange, OperationResult

logger = logging.getLogger(__name__)


class OverlayUnsupportedChangeError(RuntimeError):
    """Raised when upperdir contains a change kind OCC can't represent.

    Per D3a in ``.omc/plans/svc-cmd-occ-migration.md``, a symlink or opaque
    directory in upperdir aborts the whole ``svc.cmd`` run rather than
    silently dropping writes that the ledger cannot record.
    """


@dataclass(frozen=True)
class OverlayAuditorConfig:
    """Tunable knobs for :class:`OverlayAuditor`.

    ``lowerdir_provider`` is an async callable ``(repo_root) -> lowerdir
    path`` that returns a path whose content hashes equal the
    :class:`ContentManager` head at the moment of overlay mount. Phase
    3's outer-lowerdir provider populates this with an independent
    workspace snapshot (tracked + untracked + dirty files) so drift
    detection catches peer writes that never reached git. The auditor
    itself does not manage lowerdir lifecycle.
    """

    tmpfs_size: str = "2g"
    audit_description_prefix: str = "overlay_codeact"


LowerdirRefresh = Callable[[list[OperationChange]], object]


class OverlayAuditor:
    """Audit one sandbox process op via an overlayfs capture, committed via OCC."""

    def __init__(
        self,
        *,
        workspace_root: str,
        exec_process: Callable[..., Awaitable[Any]],
        write_coordinator: Any,
        lowerdir_provider: Callable[[str], Awaitable[str]],
        lowerdir_refresh: LowerdirRefresh | None = None,
        config: OverlayAuditorConfig | None = None,
    ) -> None:
        self._workspace_root = workspace_root
        self._exec_process = exec_process
        self._write_coordinator = write_coordinator
        self._lowerdir_provider = lowerdir_provider
        self._lowerdir_refresh = lowerdir_refresh
        self._config = config or OverlayAuditorConfig()
        self._overlay = OverlayExec(
            exec_process=exec_process,
            tmpfs_size=self._config.tmpfs_size,
        )

    async def execute(
        self,
        sandbox: Any,
        command: str,
        *,
        timeout: int | None = None,
        description: str = "",
        agent_id: str = "",
        team_run_id: str = "",
        agent_run_id: str = "",
        task_id: str = "",
        attribute_changes: bool = True,
    ) -> Any:
        """Run *command* inside an overlay namespace and commit its writes.

        Returns a ``SimpleNamespace`` with ``result`` (user stdout),
        ``exit_code`` (user exit), ``changed_paths`` (paths committed to
        disk through OCC), ``ambient_changed_paths`` (paths the overlay
        observed but the caller asked not to attribute), and
        ``files_written`` (count of committed paths).

        Raises
        ------
        OverlayMountError / OverlayExecError
            Transport failure — the caller (``svc.cmd``) surfaces these
            unchanged.
        OverlayUnsupportedChangeError
            Upperdir contains a symlink or opaque directory. No writes
            land; disk is unchanged.
        """
        del team_run_id, agent_run_id, task_id  # reserved for ledger enrichment
        lowerdir = await self._lowerdir_provider(self._workspace_root)
        run = await self._overlay.execute(
            sandbox,
            command,
            lowerdir=lowerdir,
            repo_root=self._workspace_root,
            timeout=timeout,
        )

        local_tar = await self._download_remote_tar(sandbox, run.audit_tar_path)
        try:
            changes = list(iter_upperdir_changes(local_tar))
            if not attribute_changes:
                return _audit_result(
                    run,
                    committed=[],
                    ambient=[self._repo_path(change.path) for change in changes],
                )
            return await self._commit_changes(
                sandbox,
                run,
                changes=changes,
                lowerdir=lowerdir,
                description=description or self._config.audit_description_prefix,
                agent_id=agent_id,
            )
        finally:
            cleanup_tar(local_tar)
            await self._cleanup_remote_run_dir(sandbox, run.run_dir)

    # -- OCC commit path ------------------------------------------------------

    async def _commit_changes(
        self,
        sandbox: Any,
        run: OverlayRunResult,
        *,
        changes: list[UpperdirChange],
        lowerdir: str,
        description: str,
        agent_id: str,
    ) -> Any:
        operation_changes = []
        for change in changes:
            operation_changes.append(
                await self._upperdir_change_to_operation(
                    sandbox,
                    change,
                    lowerdir=lowerdir,
                )
            )
        if not operation_changes:
            return _audit_result(run, committed=[], ambient=[])

        result: OperationResult = self._write_coordinator.commit_operation_against_base(
            operation_changes,
            agent_id=agent_id,
            edit_type="svc_cmd_overlay",
            description=description,
        )
        if not result.success:
            logger.warning(
                "svc.cmd overlay commit aborted: status=%s reason=%s file=%s",
                result.status,
                result.conflict_reason,
                result.conflict_file,
            )
            return _audit_result(
                run,
                committed=[],
                ambient=[op.file_path for op in operation_changes],
                overlay_commit_status=result.status,
                overlay_conflict_file=result.conflict_file,
                overlay_conflict_reason=result.conflict_reason,
            )
        if self._lowerdir_refresh is not None:
            try:
                refresh_result = self._lowerdir_refresh(operation_changes)
                if inspect.isawaitable(refresh_result):
                    await refresh_result
            except Exception:  # pragma: no cover - best effort
                logger.debug(
                    "overlay lowerdir refresh raised; next run will re-probe",
                    exc_info=True,
                )
        committed = [op.file_path for op in operation_changes]
        return _audit_result(run, committed=committed, ambient=[])

    async def _upperdir_change_to_operation(
        self,
        sandbox: Any,
        change: UpperdirChange,
        *,
        lowerdir: str,
    ) -> OperationChange:
        if change.kind in (ChangeKind.SYMLINK, ChangeKind.OPAQUE_DIR):
            raise OverlayUnsupportedChangeError(
                f"svc.cmd rejected: overlay upperdir contains "
                f"{change.kind.value} at {change.path!r}; "
                "extend OperationChange or deny this command instead"
            )

        file_path = self._repo_path(change.path)
        base_content, base_existed = await self._read_lowerdir_entry(
            sandbox,
            lowerdir,
            change.path,
        )
        base_hash = content_hash(base_content) if base_existed else ""

        if change.kind is ChangeKind.DELETE:
            return OperationChange(
                file_path=file_path,
                base_content=base_content,
                base_hash=base_hash,
                final_content=None,
                base_existed=base_existed,
                strict_base=True,
            )

        if change.kind is ChangeKind.MODIFY:
            payload = change.content or b""
            try:
                final_content = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise OverlayUnsupportedChangeError(
                    f"svc.cmd rejected: non-UTF-8 content at {change.path!r} "
                    f"({exc})"
                ) from exc
            return OperationChange(
                file_path=file_path,
                base_content=base_content,
                base_hash=base_hash,
                final_content=final_content,
                base_existed=base_existed,
                strict_base=True,
            )

        raise OverlayUnsupportedChangeError(
            f"svc.cmd rejected: unhandled upperdir change kind "
            f"{change.kind.value!r} at {change.path!r}"
        )

    def _repo_path(self, relative: str) -> str:
        return f"{self._workspace_root.rstrip('/')}/{relative}"

    async def _read_lowerdir_entry(
        self,
        sandbox: Any,
        lowerdir: str,
        rel_path: str,
    ) -> tuple[str, bool]:
        """Read the lowerdir's view of *rel_path* as UTF-8, returning ``(content, existed)``.

        Lowerdir snapshots live inside Daytona for live runs. Unit tests
        still pass plain local paths, so local reads remain the fallback
        when no sandbox process is present.
        """
        process = getattr(sandbox, "process", None)
        if callable(getattr(process, "exec", None)):
            return await self._read_remote_lowerdir_entry(sandbox, lowerdir, rel_path)
        return self._read_local_lowerdir_entry(lowerdir, rel_path)

    @staticmethod
    def _read_local_lowerdir_entry(lowerdir: str, rel_path: str) -> tuple[str, bool]:
        candidate = os.path.join(lowerdir, rel_path)
        try:
            with open(candidate, "rb") as handle:
                raw = handle.read()
        except FileNotFoundError:
            return "", False
        except IsADirectoryError:
            return "", False
        except OSError:
            return "", False
        try:
            return raw.decode("utf-8"), True
        except UnicodeDecodeError:
            # Non-UTF-8 lower file — let the walker catch this when it
            # tries to encode the final content; it raises a clean
            # OverlayUnsupportedChangeError up the call stack.
            return raw.decode("utf-8", errors="replace"), True

    async def _read_remote_lowerdir_entry(
        self,
        sandbox: Any,
        lowerdir: str,
        rel_path: str,
    ) -> tuple[str, bool]:
        candidate = posixpath.join(lowerdir.rstrip("/"), rel_path)
        script = """
import base64
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
try:
    if not path.exists() or path.is_dir():
        payload = {"exists": False, "content_b64": ""}
    else:
        payload = {
            "exists": True,
            "content_b64": base64.b64encode(path.read_bytes()).decode("ascii"),
        }
except OSError as exc:
    payload = {"exists": False, "content_b64": "", "error": str(exc)}
print(json.dumps(payload, separators=(",", ":")))
"""
        command = _wrap_bash_command(
            f"python3 -c {shlex.quote(script)} {shlex.quote(candidate)}"
        )
        response = await self._exec_process(sandbox, command, timeout=60)
        stdout, exit_code = _extract_exit_code(
            str(getattr(response, "result", "") or ""),
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        if exit_code != 0:
            raise OverlayExecError(
                f"lowerdir read failed for {candidate}: "
                f"exit_code={exit_code} stdout={stdout.strip()!r}"
            )
        try:
            payload = json.loads(stdout or "{}")
        except json.JSONDecodeError as exc:
            raise OverlayExecError(
                f"lowerdir read returned invalid JSON for {candidate}: {stdout!r}"
            ) from exc
        if not isinstance(payload, dict) or not payload.get("exists"):
            return "", False
        encoded = str(payload.get("content_b64", "") or "")
        raw = base64.b64decode(encoded)
        try:
            return raw.decode("utf-8"), True
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace"), True

    # -- Remote scratch I/O ---------------------------------------------------

    async def _download_remote_tar(
        self,
        sandbox: Any,
        remote_path: str,
    ) -> str:
        """Fetch ``remote_path`` from the sandbox to a local temp file."""
        cmd = (
            f"if [ -f {shlex.quote(remote_path)} ]; then "
            f"base64 < {shlex.quote(remote_path)} | tr -d '\\n'; "
            "else echo __OVERLAY_AUDIT_TAR_MISSING__; exit 2; fi"
        )
        response = await self._exec_process(
            sandbox,
            _wrap_bash_command(cmd),
            timeout=60,
        )
        raw, exit_code = _extract_exit_code(
            str(getattr(response, "result", "") or ""),
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        raw = raw.strip()
        if exit_code != 0 or raw == "__OVERLAY_AUDIT_TAR_MISSING__":
            raise OverlayExecError(
                f"audit tar download failed for {remote_path}: "
                f"exit_code={exit_code} stdout={raw!r}"
            )
        if not raw:
            raise OverlayExecError(f"audit tar download returned empty payload for {remote_path}")
        data = base64.b64decode(raw) if raw else b""
        fd, local_path = tempfile.mkstemp(prefix="overlay-audit-", suffix=".tar")
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
        except BaseException:
            try:
                os.unlink(local_path)
            except OSError:
                pass
            raise
        return local_path

    async def _cleanup_remote_run_dir(self, sandbox: Any, run_dir: str) -> None:
        try:
            await self._exec_process(
                sandbox,
                _wrap_bash_command(f"rm -rf {shlex.quote(run_dir)}"),
                timeout=30,
            )
        except Exception:
            logger.debug(
                "overlay_auditor: failed to clean up remote %s",
                run_dir,
                exc_info=True,
            )


def _audit_result(
    run: OverlayRunResult,
    *,
    committed: list[str],
    ambient: list[str],
    overlay_commit_status: str | None = None,
    overlay_conflict_file: str | None = None,
    overlay_conflict_reason: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        result=run.stdout,
        exit_code=run.exit_code,
        changed_paths=committed,
        ambient_changed_paths=ambient,
        files_written=len(committed),
        overlay_commit_status=overlay_commit_status,
        overlay_conflict_file=overlay_conflict_file,
        overlay_conflict_reason=overlay_conflict_reason,
    )


__all__ = [
    "OverlayAuditor",
    "OverlayAuditorConfig",
    "OverlayUnsupportedChangeError",
    "OverlayExecError",
    "OverlayMountError",
]
