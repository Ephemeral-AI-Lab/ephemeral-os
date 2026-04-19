"""Per-sandbox pool of reusable Git workspace slots."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import posixpath
import shlex
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from tools.daytona_toolkit._daytona_utils import _extract_exit_code, _wrap_bash_command

from code_intelligence.routing.git_workspace_config import (
    git_workspace_pool_size_per_sandbox,
)
from code_intelligence.routing.git_workspace_types import (
    GitWorkspaceLease,
    GitWorkspacePrepareError,
)

logger = logging.getLogger(__name__)


class GitWorkspacePool:
    """Lease reusable Git workspace slots for one sandbox/workspace root.

    The pool is intentionally independent from ``CodeIntelligenceService``.
    It needs only a sandbox id, a workspace root, and an async process executor.
    """

    def __init__(
        self,
        *,
        sandbox_id: str,
        workspace_root: str,
        exec_process: Callable[..., Awaitable[Any]],
        pool_size: int | None = None,
    ) -> None:
        self._sandbox_id = sandbox_id
        self._workspace_root = workspace_root.rstrip("/")
        self._exec_process = exec_process
        self._pool_size = (
            git_workspace_pool_size_per_sandbox()
            if pool_size is None
            else max(0, int(pool_size))
        )
        self._pool_root = _pool_root(sandbox_id, self._workspace_root)
        self._queue: asyncio.Queue[GitWorkspaceLease] = asyncio.Queue()
        self._init_lock = asyncio.Lock()
        self._initialized = False

    @property
    def pool_size(self) -> int:
        return self._pool_size

    @property
    def pool_root(self) -> str:
        return self._pool_root

    async def lease(self, sandbox: Any) -> GitWorkspaceLease:
        """Return one workspace slot lease."""

        if self._pool_size == 0:
            slot_id = f"run-{uuid.uuid4().hex}"
            return GitWorkspaceLease(
                slot_id=slot_id,
                slot_path=posixpath.join(self._pool_root, "runs", slot_id),
                pooled=False,
            )

        await self._ensure_prewarmed(sandbox)
        return await self._queue.get()

    async def release(
        self,
        sandbox: Any,
        lease: GitWorkspaceLease,
        *,
        discard: bool = False,
    ) -> None:
        """Reset or discard *lease*, then make the slot available if pooled."""

        if not lease.pooled:
            await self._remove_slot(sandbox, lease.slot_path)
            return

        reusable = not discard
        if reusable:
            try:
                await self._reset_slot(sandbox, lease.slot_path)
            except Exception:
                logger.debug(
                    "git workspace slot reset failed; discarding %s",
                    lease.slot_path,
                    exc_info=True,
                )
                reusable = False

        if not reusable:
            await self._remove_slot(sandbox, lease.slot_path)
            try:
                await self._create_slot(sandbox, lease.slot_path)
                reusable = True
            except Exception:
                logger.warning(
                    "git workspace slot recreation failed for %s",
                    lease.slot_path,
                    exc_info=True,
                )

        if reusable:
            self._queue.put_nowait(lease)

    async def prepare_baseline(self, sandbox: Any, lease: GitWorkspaceLease) -> str:
        """Prepare *lease* so HEAD is a synthetic current-workspace baseline."""

        payload = await self._run_json_script(
            sandbox,
            _PREPARE_BASELINE_SCRIPT,
            [
                self._workspace_root,
                lease.slot_path,
            ],
            timeout=180,
        )
        baseline = str(payload.get("baseline_commit") or "")
        if not baseline:
            raise GitWorkspacePrepareError(
                f"git workspace baseline prepare returned no commit for {lease.slot_path}"
            )
        return baseline

    async def _ensure_prewarmed(self, sandbox: Any) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await self._run_json_script(
                sandbox,
                _PREWARM_SCRIPT,
                [
                    self._workspace_root,
                    self._pool_root,
                    str(self._pool_size),
                ],
                timeout=max(180, self._pool_size * 20),
            )
            for idx in range(self._pool_size):
                slot_id = f"slot-{idx:03d}"
                self._queue.put_nowait(
                    GitWorkspaceLease(
                        slot_id=slot_id,
                        slot_path=posixpath.join(self._pool_root, "slots", slot_id),
                        pooled=True,
                    )
                )
            self._initialized = True

    async def _create_slot(self, sandbox: Any, slot_path: str) -> None:
        await self._run_json_script(
            sandbox,
            _CREATE_SLOT_SCRIPT,
            [self._workspace_root, slot_path],
            timeout=120,
        )

    async def _reset_slot(self, sandbox: Any, slot_path: str) -> None:
        await self._run_json_script(
            sandbox,
            _RESET_SLOT_SCRIPT,
            [slot_path],
            timeout=60,
        )

    async def _remove_slot(self, sandbox: Any, slot_path: str) -> None:
        await self._run_json_script(
            sandbox,
            _REMOVE_SLOT_SCRIPT,
            [slot_path],
            timeout=60,
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
                "sys.argv=[sys.argv[0],*sys.argv[1:]]; "
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
            raise GitWorkspacePrepareError(
                f"git workspace script failed: exit_code={exit_code} stdout={stdout[-2000:]!r}"
            )
        try:
            payload = json.loads(stdout or "{}")
        except json.JSONDecodeError as exc:
            raise GitWorkspacePrepareError(
                f"git workspace script returned invalid JSON: {stdout[-2000:]!r}"
            ) from exc
        if not isinstance(payload, dict) or not payload.get("ok"):
            raise GitWorkspacePrepareError(
                f"git workspace script failed: {payload!r}"
            )
        return payload


def _pool_root(sandbox_id: str, workspace_root: str) -> str:
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in sandbox_id)
    digest = hashlib.sha256(workspace_root.encode("utf-8")).hexdigest()[:12]
    return f"/tmp/eos-codeact-git/{safe_id}/{digest}"


_COMMON_REMOTE = r'''
import json
import os
import pathlib
import shutil
import subprocess
import sys


def reply(**payload):
    payload.setdefault("ok", True)
    print(json.dumps(payload, separators=(",", ":")))


def run(args, *, cwd=None, input_bytes=None, check=True):
    proc = subprocess.run(
        args,
        cwd=cwd,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            "command failed: "
            + " ".join(args)
            + f"\nstdout={proc.stdout.decode('utf-8', 'replace')}"
            + f"\nstderr={proc.stderr.decode('utf-8', 'replace')}"
        )
    return proc


def require_git_repo(repo_root):
    repo = pathlib.Path(repo_root)
    if not repo.exists():
        raise RuntimeError(f"workspace root does not exist: {repo_root}")
    proc = run(["git", "-C", repo_root, "rev-parse", "--show-toplevel"])
    top = proc.stdout.decode().strip()
    if os.path.realpath(top) != os.path.realpath(repo_root):
        raise RuntimeError(f"workspace root is not the git top-level: {repo_root} != {top}")
    run(["git", "-C", repo_root, "rev-parse", "--verify", "HEAD"])


def ensure_slot(repo_root, slot_path):
    require_git_repo(repo_root)
    slot = pathlib.Path(slot_path)
    if not (slot / ".git").exists():
        shutil.rmtree(slot, ignore_errors=True)
        slot.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--shared", "--no-checkout", repo_root, slot_path])
    run(["git", "-C", slot_path, "config", "user.email", "ephemeralos@example.invalid"])
    run(["git", "-C", slot_path, "config", "user.name", "EphemeralOS"])


def reset_slot(slot_path):
    if pathlib.Path(slot_path, ".git").exists():
        run(["git", "-C", slot_path, "reset", "--hard", "-q", "HEAD"])
        run(["git", "-C", slot_path, "clean", "-fdx", "-q"])


def checkout_live_head(repo_root, slot_path):
    head = run(["git", "-C", repo_root, "rev-parse", "HEAD"]).stdout.decode().strip()
    cat = run(["git", "-C", slot_path, "cat-file", "-e", f"{head}^{{commit}}"], check=False)
    if cat.returncode != 0:
        run(["git", "-C", slot_path, "fetch", "-q", repo_root, head])
    run(["git", "-C", slot_path, "checkout", "-q", "--detach", head])
    run(["git", "-C", slot_path, "reset", "--hard", "-q", head])
    run(["git", "-C", slot_path, "clean", "-fdx", "-q"])


def apply_live_state(repo_root, slot_path):
    diff = run([
        "git", "-C", repo_root, "diff", "--binary", "--full-index", "HEAD", "--"
    ]).stdout
    if diff:
        run(["git", "-C", slot_path, "apply", "--binary", "--index"], input_bytes=diff)
    raw = run([
        "git", "-C", repo_root, "ls-files", "--others", "--exclude-standard", "-z"
    ]).stdout
    for rel_raw in [part for part in raw.split(b"\0") if part]:
        rel = rel_raw.decode("utf-8")
        src = pathlib.Path(repo_root, rel)
        dst = pathlib.Path(slot_path, rel)
        if src.is_dir():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    run(["git", "-C", slot_path, "add", "-A"])
    run([
        "git", "-C", slot_path,
        "-c", "user.name=EphemeralOS",
        "-c", "user.email=ephemeralos@example.invalid",
        "commit", "--allow-empty", "-q", "-m", "EphemeralOS CodeAct baseline",
    ])
    return run(["git", "-C", slot_path, "rev-parse", "HEAD"]).stdout.decode().strip()
'''


_PREWARM_SCRIPT = _COMMON_REMOTE + r'''
def main():
    repo_root, pool_root, pool_size_raw = sys.argv[1:4]
    pool_size = int(pool_size_raw)
    require_git_repo(repo_root)
    pathlib.Path(pool_root, "slots").mkdir(parents=True, exist_ok=True)
    pathlib.Path(pool_root, "runs").mkdir(parents=True, exist_ok=True)
    slots = []
    for idx in range(pool_size):
        slot = os.path.join(pool_root, "slots", f"slot-{idx:03d}")
        ensure_slot(repo_root, slot)
        slots.append(slot)
    reply(slots=slots)


if __name__ == "__main__":
    main()
'''


_CREATE_SLOT_SCRIPT = _COMMON_REMOTE + r'''
def main():
    repo_root, slot_path = sys.argv[1:3]
    ensure_slot(repo_root, slot_path)
    reply(slot=slot_path)


if __name__ == "__main__":
    main()
'''


_RESET_SLOT_SCRIPT = _COMMON_REMOTE + r'''
def main():
    reset_slot(sys.argv[1])
    reply(slot=sys.argv[1])


if __name__ == "__main__":
    main()
'''


_REMOVE_SLOT_SCRIPT = _COMMON_REMOTE + r'''
def main():
    shutil.rmtree(sys.argv[1], ignore_errors=True)
    reply(slot=sys.argv[1])


if __name__ == "__main__":
    main()
'''


_PREPARE_BASELINE_SCRIPT = _COMMON_REMOTE + r'''
def main():
    repo_root, slot_path = sys.argv[1:3]
    ensure_slot(repo_root, slot_path)
    checkout_live_head(repo_root, slot_path)
    baseline = apply_live_state(repo_root, slot_path)
    reply(slot=slot_path, baseline_commit=baseline)


if __name__ == "__main__":
    main()
'''


__all__ = [
    "GitWorkspacePool",
]
