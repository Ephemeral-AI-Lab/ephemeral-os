"""Build a git snapshot of the live workspace without moving refs or firing hooks.

The sandbox-side primitive for overlay CodeAct auditing (see
``docs/architecture/overlay-sandbox-plan.md`` §2). One ``git commit-tree``
call produces a dangling commit whose tree captures tracked + staged +
unstaged + untracked content, honoring ``.gitignore`` so gitignored dep
trees (``.venv/``, ``node_modules/``, caches) stay out.

Invariants enforced by this module (and tested in
``backend/tests/test_code_intelligence/test_git_snapshot.py``):

* The snapshot is reachable via ``git show <sha>:path`` — used later as
  the strict-base source for OCC write verification.
* The live ``.git/index`` is byte-identical before and after the call
  (achieved by redirecting ``GIT_INDEX_FILE`` to a tempfile).
* No ref is moved — ``for-each-ref`` output is unchanged.
* ``pre-commit`` / ``commit-msg`` hooks do not fire — ``commit-tree`` is
  plumbing, hooks bind only to ``git commit``.
* ``git add -A`` honors ``.gitignore`` → the snapshot never contains
  gitignored trees.
* The source must be the canonical repository checkout with a real ``.git``
  directory. Linked Git worktrees are intentionally rejected because the
  snapshot baseline is copied from the repository workspace, not a worktree.
"""

from __future__ import annotations

import base64
import json
import logging
import shlex
from collections.abc import Awaitable, Callable
from typing import Any

from tools.daytona_toolkit._daytona_utils import _extract_exit_code, _wrap_bash_command

logger = logging.getLogger(__name__)


class GitSnapshotError(RuntimeError):
    """Raised when the sandbox-side snapshot script fails."""


async def build_live_snapshot(
    sandbox: Any,
    exec_process: Callable[..., Awaitable[Any]],
    repo_root: str,
    *,
    timeout: int = 120,
) -> str:
    """Build a dangling commit capturing the live working tree.

    Returns the SHA of a commit reachable via ``git show <sha>:path``.
    Tracked, staged, unstaged and untracked content is included; the
    ``.gitignore`` rules are honored so gitignored deps are excluded.

    The live repository is not mutated:

    * No ref is moved. ``HEAD`` and all branches stay at their prior sha.
    * The live ``.git/index`` is byte-identical before and after — all
      index writes go to a tempfile under ``GIT_INDEX_FILE``.
    * Hooks do not fire. ``git commit-tree`` is plumbing; hooks only bind
      to ``git commit``.
    """
    workspace_root = repo_root.rstrip("/")
    if not workspace_root:
        raise GitSnapshotError("repo_root must be a non-empty path")

    payload = await _run_snapshot_script(
        sandbox,
        exec_process,
        workspace_root=workspace_root,
        timeout=timeout,
    )

    snap = str(payload.get("snap") or "").strip()
    if not snap or len(snap) < 7 or not all(c in "0123456789abcdef" for c in snap):
        raise GitSnapshotError(
            f"git_snapshot script returned invalid sha: {payload!r}"
        )
    return snap


async def _run_snapshot_script(
    sandbox: Any,
    exec_process: Callable[..., Awaitable[Any]],
    *,
    workspace_root: str,
    timeout: int,
) -> dict[str, Any]:
    encoded = base64.b64encode(_SNAPSHOT_SCRIPT.encode("utf-8")).decode("ascii")
    command = (
        "python3 -c "
        + shlex.quote(
            "import base64,sys; "
            "ns={'__name__':'__main__','__file__':'<git-snapshot>'}; "
            f"exec(base64.b64decode({encoded!r}).decode('utf-8'), ns)"
        )
        + " "
        + shlex.quote(workspace_root)
    )
    response = await exec_process(
        sandbox,
        _wrap_bash_command(command),
        timeout=timeout,
    )
    stdout, exit_code = _extract_exit_code(
        str(getattr(response, "result", "") or ""),
        fallback_exit_code=getattr(response, "exit_code", None),
    )
    if exit_code != 0:
        raise GitSnapshotError(
            f"git_snapshot script failed: exit_code={exit_code} stdout={stdout[-2000:]!r}"
        )
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError as exc:
        raise GitSnapshotError(
            f"git_snapshot script returned invalid JSON: {stdout[-2000:]!r}"
        ) from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise GitSnapshotError(f"git_snapshot script failed: {payload!r}")
    return payload


# Sandbox-side Python. Kept as a data string so it can be base64-shipped
# through the exec transport. Uses only the stdlib.
_SNAPSHOT_SCRIPT = r'''
import json
import os
import subprocess
import sys
import tempfile


def reply(**payload):
    payload.setdefault("ok", True)
    print(json.dumps(payload, separators=(",", ":")))


def fail(error):
    print(json.dumps({"ok": False, "error": error}, separators=(",", ":")))
    raise SystemExit(0)


def git(args, *, cwd, env=None, input_bytes=None, check=True):
    proc = subprocess.run(
        ["git", "-C", cwd, *args],
        env=env,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            "git "
            + " ".join(args)
            + f" failed: rc={proc.returncode} "
            + f"stdout={proc.stdout.decode('utf-8', 'replace')} "
            + f"stderr={proc.stderr.decode('utf-8', 'replace')}"
        )
    return proc


def main():
    repo_root = sys.argv[1]
    if not os.path.isdir(repo_root):
        fail(f"repo_root does not exist: {repo_root}")
    if not os.path.isdir(os.path.join(repo_root, ".git")):
        # Linked Git worktrees have a .git pointer file. They are intentionally
        # excluded: this snapshot is the repository baseline, not a worktree copy.
        fail(
            "repo_root must be a canonical git checkout with a .git directory "
            f"(linked worktrees are not supported): {repo_root}"
        )

    # Redirect the index so "git add -A" does not touch the live index.
    tmp_index_fd, tmp_index_path = tempfile.mkstemp(prefix="git-snapshot-idx-")
    os.close(tmp_index_fd)
    os.unlink(tmp_index_path)  # git refuses to read a stub; let it create one.

    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = tmp_index_path
    # Suppress any user identity prompts. commit-tree still needs an
    # author/committer when no ref-parent is supplied, so we pin both to
    # stable plumbing identities that never hit the reflog (no ref is moved).
    env.setdefault("GIT_AUTHOR_NAME", "EphemeralOS Snapshot")
    env.setdefault("GIT_AUTHOR_EMAIL", "snapshot@ephemeralos.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "EphemeralOS Snapshot")
    env.setdefault("GIT_COMMITTER_EMAIL", "snapshot@ephemeralos.invalid")
    # Deterministic commit date keeps content-addressed SHAs testable
    # when running against fixture repos.
    env.setdefault("GIT_AUTHOR_DATE", "1700000000 +0000")
    env.setdefault("GIT_COMMITTER_DATE", "1700000000 +0000")

    try:
        # Seed the tempfile index from HEAD so commit-tree sees a coherent
        # starting point when HEAD exists. For empty repos (no commits yet)
        # skip read-tree — the index simply starts empty, which is what we
        # want.
        head_proc = git(
            ["rev-parse", "--verify", "HEAD"],
            cwd=repo_root,
            env=env,
            check=False,
        )
        has_head = head_proc.returncode == 0
        head_sha = head_proc.stdout.decode("utf-8", "replace").strip() if has_head else ""
        if has_head:
            git(["read-tree", "HEAD"], cwd=repo_root, env=env)

        # "git add -A" in the tempfile index: honors .gitignore, captures
        # staged + unstaged + untracked content, resolves deletions as
        # removals from the tree. Never touches the live index.
        git(["add", "-A"], cwd=repo_root, env=env)

        # Write the tree.
        tree_proc = git(["write-tree"], cwd=repo_root, env=env)
        tree_sha = tree_proc.stdout.decode("utf-8", "replace").strip()
        if not tree_sha:
            fail("git write-tree returned empty sha")

        commit_args = ["commit-tree", tree_sha, "-m", "overlay-snapshot"]
        if has_head:
            # Parent to HEAD so callers can diff the snapshot against HEAD
            # via standard commit-range machinery (not required by the
            # overlay auditor, but cheap).
            commit_args.extend(["-p", head_sha])
        commit_proc = git(commit_args, cwd=repo_root, env=env)
        commit_sha = commit_proc.stdout.decode("utf-8", "replace").strip()
        if not commit_sha:
            fail("git commit-tree returned empty sha")

        reply(snap=commit_sha, tree=tree_sha, parent=head_sha or None)
    except Exception as exc:  # pragma: no cover - defensive
        fail(str(exc))
    finally:
        try:
            os.unlink(tmp_index_path)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        fail(str(exc))
'''


__all__ = [
    "GitSnapshotError",
    "build_live_snapshot",
]
