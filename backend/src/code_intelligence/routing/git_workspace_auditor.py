"""OCC-gated Git workspace command auditor."""

from __future__ import annotations

import base64
import json
import logging
import shlex
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

from tools.daytona_toolkit._daytona_utils import _extract_exit_code, _wrap_bash_command

from code_intelligence.routing.git_diff_committer import (
    GitDiffCommitter,
    changed_live_paths,
)
from code_intelligence.routing.git_workspace_pool import GitWorkspacePool
from code_intelligence.routing.git_workspace_types import (
    GitWorkspaceCommandError,
    GitWorkspaceCommandResult,
    GitWorkspaceBaseline,
    GitWorkspaceError,
    GitWorkspaceLease,
    GitWorkspaceUnsupportedChangeError,
    WorkspaceDiff,
    WorkspaceDiffFile,
)

logger = logging.getLogger(__name__)


class GitWorkspaceAuditor:
    """Run one command in an isolated Git workspace and commit via OCC.

    The auditor is deliberately decoupled from ``CodeIntelligenceService``. It
    depends on a workspace root, an async process executor, a slot pool, and a
    diff committer. Other callers can reuse it for future audited process flows.
    """

    def __init__(
        self,
        *,
        workspace_root: str,
        exec_process: Callable[..., Awaitable[Any]],
        pool: GitWorkspacePool,
        committer: GitDiffCommitter,
    ) -> None:
        self._workspace_root = workspace_root.rstrip("/")
        self._exec_process = exec_process
        self._pool = pool
        self._committer = committer

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
        """Run *command* in a leased Git workspace and commit via OCC."""

        del team_run_id, agent_run_id, task_id  # reserved for ledger enrichment
        lease = await self._pool.lease(sandbox)
        discard = False
        try:
            baseline = await self._pool.prepare_baseline(sandbox, lease)
            command_result = await self._run_command(
                sandbox,
                lease,
                command,
                timeout=timeout,
            )
            diff = await self._collect_diff(
                sandbox,
                lease,
                baseline=baseline,
                command_result=command_result,
            )
            if not attribute_changes:
                return _audit_result(
                    command_result,
                    committed=[],
                    ambient=changed_live_paths(diff),
                )
            return await self._commit_diff(
                command_result,
                diff,
                description=description or "daytona_codeact git workspace",
                agent_id=agent_id,
            )
        except Exception:
            discard = True
            raise
        finally:
            try:
                await self._pool.release(sandbox, lease, discard=discard)
            except Exception:
                logger.debug(
                    "git workspace lease release failed for %s",
                    lease.slot_path,
                    exc_info=True,
                )

    async def _commit_diff(
        self,
        command_result: GitWorkspaceCommandResult,
        diff: WorkspaceDiff,
        *,
        description: str,
        agent_id: str,
    ) -> Any:
        result = await self._committer.commit(
            diff,
            agent_id=agent_id,
            description=description,
        )
        if not result.success:
            return _audit_result(
                command_result,
                committed=[],
                ambient=changed_live_paths(diff),
                git_commit_status=result.status,
                git_conflict_file=result.conflict_file,
                git_conflict_reason=result.conflict_reason,
            )
        return _audit_result(
            command_result,
            committed=changed_live_paths(diff),
            ambient=[],
            git_commit_status=result.status,
        )

    async def _run_command(
        self,
        sandbox: Any,
        lease: GitWorkspaceLease,
        command: str,
        *,
        timeout: int | None,
    ) -> GitWorkspaceCommandResult:
        mapped = self._map_command_to_slot(command, lease.slot_path)
        response = await self._exec_process(sandbox, mapped, timeout=timeout)
        raw = str(getattr(response, "result", "") or "")
        _cleaned, exit_code = _extract_exit_code(
            raw,
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        return GitWorkspaceCommandResult(stdout=raw, exit_code=exit_code)

    def _map_command_to_slot(self, command: str, slot_path: str) -> str:
        mapped = command.replace(self._workspace_root, slot_path.rstrip("/"))
        return (
            "env -u LC_ALL "
            f"EOS_CODEACT_WORKSPACE_ROOT={shlex.quote(slot_path)} "
            f"EOS_LIVE_WORKSPACE_ROOT={shlex.quote(self._workspace_root)} "
            f"bash -o pipefail -lc {shlex.quote(mapped)}"
        )

    async def _collect_diff(
        self,
        sandbox: Any,
        lease: GitWorkspaceLease,
        *,
        baseline: GitWorkspaceBaseline,
        command_result: GitWorkspaceCommandResult,
    ) -> WorkspaceDiff:
        payload = await self._run_json_script(
            sandbox,
            _COLLECT_DIFF_SCRIPT,
            [
                self._workspace_root,
                lease.slot_path,
                baseline.snapshot_path,
                str(command_result.exit_code),
                base64.b64encode(command_result.stdout.encode("utf-8")).decode("ascii"),
            ],
            timeout=180,
        )
        files: list[WorkspaceDiffFile] = []
        for item in payload.get("files", []):
            if not isinstance(item, dict):
                continue
            files.append(
                WorkspaceDiffFile(
                    path=str(item["path"]),
                    old_path=(
                        str(item["old_path"])
                        if item.get("old_path") is not None
                        else None
                    ),
                    status=str(item["status"]),  # type: ignore[arg-type]
                    base_existed=bool(item["base_existed"]),
                    base_hash=str(item.get("base_hash") or ""),
                    final_existed=bool(item["final_existed"]),
                    final_hash=str(item.get("final_hash") or ""),
                    base_content=str(item.get("base_content") or ""),
                    final_content=(
                        str(item["final_content"])
                        if item.get("final_content") is not None
                        else None
                    ),
                )
            )
        return WorkspaceDiff(
            files=tuple(files),
            baseline_ref=baseline.snapshot_path,
            workspace_root=self._workspace_root,
            command_exit_code=command_result.exit_code,
            stdout=command_result.stdout,
            patch=str(payload.get("patch") or ""),
        )

    async def _run_json_script(
        self,
        sandbox: Any,
        script: str,
        args: list[str],
        *,
        timeout: int,
    ) -> dict[str, Any]:
        encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
        command = (
            "python3 -c "
            + shlex.quote(
                "import base64,sys; "
                "ns={'__name__':'__main__','__file__':'<git-workspace>'}; "
                f"exec(base64.b64decode({encoded!r}).decode('utf-8'), ns)"
            )
            + " "
            + " ".join(shlex.quote(arg) for arg in args)
        )
        response = await self._exec_process(
            sandbox,
            _wrap_bash_command(command),
            timeout=timeout,
        )
        stdout, exit_code = _extract_exit_code(
            str(getattr(response, "result", "") or ""),
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        if exit_code != 0:
            raise GitWorkspaceCommandError(
                f"git workspace script failed: exit_code={exit_code} stdout={stdout[-2000:]!r}"
            )
        try:
            payload = json.loads(stdout or "{}")
        except json.JSONDecodeError as exc:
            raise GitWorkspaceCommandError(
                f"git workspace script returned invalid JSON: {stdout[-2000:]!r}"
            ) from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            error = str(payload.get("error") if isinstance(payload, dict) else payload)
            if isinstance(payload, dict) and payload.get("unsupported"):
                raise GitWorkspaceUnsupportedChangeError(error)
            raise GitWorkspaceCommandError(error)
        return payload


def _audit_result(
    command_result: GitWorkspaceCommandResult,
    *,
    committed: list[str],
    ambient: list[str],
    git_commit_status: str | None = None,
    git_conflict_file: str | None = None,
    git_conflict_reason: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        result=command_result.stdout,
        exit_code=command_result.exit_code,
        changed_paths=committed,
        ambient_changed_paths=ambient,
        files_written=len(committed),
        git_commit_status=git_commit_status,
        git_conflict_file=git_conflict_file,
        git_conflict_reason=git_conflict_reason,
    )


_COLLECT_DIFF_SCRIPT = r'''
import base64
import hashlib
import json
import os
import pathlib
import subprocess
import sys


def reply(**payload):
    payload.setdefault("ok", True)
    print(json.dumps(payload, separators=(",", ":")))


def fail(error, *, unsupported=False):
    print(json.dumps({"ok": False, "error": error, "unsupported": unsupported}, separators=(",", ":")))
    raise SystemExit(0)


def run(args, *, check=True):
    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and proc.returncode != 0:
        raise RuntimeError(
            "command failed: "
            + " ".join(args)
            + f"\nstdout={proc.stdout.decode('utf-8', 'replace')}"
            + f"\nstderr={proc.stderr.decode('utf-8', 'replace')}"
        )
    return proc


def content_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def validate_rel(path):
    path = str(path).replace("\\", "/")
    if not path or path.startswith("/") or "\0" in path:
        fail(f"invalid changed path: {path!r}", unsupported=True)
    parts = pathlib.PurePosixPath(path).parts
    if any(part == ".." for part in parts):
        fail(f"changed path escapes workspace: {path!r}", unsupported=True)
    return path


def decode_utf8(raw, path):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(f"non-UTF-8 content at {path!r}: {exc}", unsupported=True)


def iter_files(root):
    root_path = pathlib.Path(root)
    if not root_path.exists():
        fail(f"baseline snapshot does not exist: {root}", unsupported=True)
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [name for name in dirnames if name != ".git"]
        for filename in filenames:
            path = pathlib.Path(dirpath, filename)
            rel = validate_rel(path.relative_to(root_path).as_posix())
            if path.is_symlink():
                fail(f"unsupported symlink at {rel!r}", unsupported=True)
            if not path.is_file():
                fail(f"unsupported non-file entry at {rel!r}", unsupported=True)
            yield rel


def read_bytes(root, rel_path):
    return pathlib.Path(root, rel_path).read_bytes()


def collect():
    workspace_root, slot, baseline_snapshot, exit_code_raw, stdout_b64 = sys.argv[1:6]
    del workspace_root, exit_code_raw, stdout_b64
    base_paths = set(iter_files(baseline_snapshot))
    final_paths = set(iter_files(slot))
    files = []
    for new_path in sorted(base_paths | final_paths):
        old_path = None
        base_exists = new_path in base_paths
        final_exists = new_path in final_paths
        if base_exists:
            base_raw = read_bytes(baseline_snapshot, new_path)
        else:
            base_raw = b""
        if final_exists:
            final_raw = read_bytes(slot, new_path)
        else:
            final_raw = b""

        if base_exists and final_exists and base_raw == final_raw:
            continue

        if final_exists and not base_exists:
            status = "add"
            base_content = ""
            final_content = decode_utf8(final_raw, new_path)
        elif base_exists and final_exists:
            status = "modify"
            base_content = decode_utf8(base_raw, new_path)
            final_content = decode_utf8(final_raw, new_path)
        elif base_exists and not final_exists:
            status = "delete"
            base_content = decode_utf8(base_raw, new_path)
            final_content = None
        else:
            continue

        final_hash = content_hash(final_content) if final_content is not None else ""
        files.append({
            "path": new_path,
            "old_path": old_path,
            "status": status,
            "base_existed": base_exists,
            "base_hash": content_hash(base_content) if base_exists else "",
            "final_existed": final_exists,
            "final_hash": final_hash,
            "base_content": base_content,
            "final_content": final_content,
        })
    reply(files=files, patch="")


if __name__ == "__main__":
    try:
        collect()
    except Exception as exc:
        fail(str(exc))
'''


__all__ = [
    "GitWorkspaceAuditor",
    "GitWorkspaceError",
]
