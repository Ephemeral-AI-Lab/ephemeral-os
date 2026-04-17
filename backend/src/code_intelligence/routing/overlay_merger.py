"""Merge strategies for applying overlay upperdir content to a live repo.

The overlay auditor captures each actor's *full* new file content in
upperdir. Applying that content naively as a full-file overwrite
clobbers any concurrent writer that modified disjoint lines of the
same file. To preserve concurrent edits, we generate the actor's hunks
(``diff(lowerdir, upperdir)``) and apply them to the *current* live
content (``repo_root``), which may already contain other actors' hunks.

``git merge-file`` does exactly this merge: given ``ours``, ``base``,
``theirs`` it writes ``ours + (theirs - base)`` to stdout, returning 0
on a clean merge or a positive exit code equal to the number of
unresolved conflict hunks.

This module abstracts the merge step behind a protocol so callers can
swap a simple overwrite (v1 default) for a sandbox-backed git merger,
or later a pure-Python merger without changing the auditor.
"""

from __future__ import annotations

import logging
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MergeResult:
    """Outcome of merging one path."""

    merged_content: str
    conflicts: int
    """Number of unresolved conflict hunks. 0 == clean merge."""
    strategy: str
    """Identifier for the strategy that produced ``merged_content``."""


class Merger(Protocol):
    """Strategy for combining ``upperdir_content`` with the current
    repo state, using ``lowerdir`` as the merge base."""

    async def merge(
        self,
        *,
        sandbox: Any,
        path: str,
        lowerdir: str,
        repo_root: str,
        upperdir_content: str,
    ) -> MergeResult: ...


class OverwriteMerger:
    """Default: return ``upperdir_content`` verbatim (last-writer-wins).

    Safe when only one actor writes a given path; loses concurrent
    hunks otherwise. Used as the v1 fallback while the sandbox-backed
    merger rolls out.
    """

    async def merge(
        self,
        *,
        sandbox: Any,
        path: str,
        lowerdir: str,
        repo_root: str,
        upperdir_content: str,
    ) -> MergeResult:
        return MergeResult(
            merged_content=upperdir_content,
            conflicts=0,
            strategy="overwrite",
        )


class GitMergeFileMerger:
    """3-way merge via sandbox-side ``git merge-file``.

    Staging: writes ``upperdir_content`` to ``$RUN_DIR/merge-theirs``
    in the sandbox (via an exec-backed here-doc), then invokes
    ``git merge-file --stdout <ours> <base> <theirs>`` where ``ours`` is
    the current live file, ``base`` is the lowerdir snapshot, and
    ``theirs`` is the upperdir-captured version. Captures stdout for
    the merged content and the exit code as the conflict count.

    Falls back to ``OverwriteMerger`` if the underlying git invocation
    fails in a way that suggests git itself is unavailable (non-zero
    exit with empty stdout AND a missing file or non-git workspace).
    """

    def __init__(
        self,
        *,
        exec_process: Callable[..., Awaitable[Any]],
        scratch_dir: str = "/tmp/overlay-merge",
    ) -> None:
        self._exec = exec_process
        self._scratch_dir = scratch_dir
        self._fallback = OverwriteMerger()

    async def merge(
        self,
        *,
        sandbox: Any,
        path: str,
        lowerdir: str,
        repo_root: str,
        upperdir_content: str,
    ) -> MergeResult:
        rel = _relpath(path, repo_root)
        ours = path
        base = f"{lowerdir.rstrip('/')}/{rel}"
        theirs = f"{self._scratch_dir}/{_safe_id(rel)}.theirs"
        output = f"{self._scratch_dir}/{_safe_id(rel)}.merged"

        # Stage theirs on-sandbox; base64 over the wire keeps any
        # special content (NULs, quotes) intact.
        import base64

        theirs_b64 = base64.b64encode(upperdir_content.encode("utf-8")).decode("ascii")
        stage_cmd = (
            f"mkdir -p {shlex.quote(self._scratch_dir)} && "
            f"printf '%s' {shlex.quote(theirs_b64)} | base64 -d > {shlex.quote(theirs)}"
        )
        try:
            await self._exec(sandbox, stage_cmd, timeout=30)
        except Exception as exc:
            logger.warning(
                "GitMergeFileMerger stage failed for %s: %s; using overwrite",
                path,
                exc,
            )
            return await self._fallback.merge(
                sandbox=sandbox,
                path=path,
                lowerdir=lowerdir,
                repo_root=repo_root,
                upperdir_content=upperdir_content,
            )

        # git merge-file writes merge result into ``ours`` by default
        # or to stdout with --stdout. We want a clean capture so use
        # -p (alias for --stdout) and redirect to a file to avoid
        # transport-level stdout truncation.
        merge_cmd = (
            f"git merge-file -p "
            f"{shlex.quote(ours)} {shlex.quote(base)} {shlex.quote(theirs)} "
            f"> {shlex.quote(output)} 2>/dev/null; echo $?"
        )
        try:
            response = await self._exec(sandbox, merge_cmd, timeout=60)
        except Exception as exc:
            logger.warning(
                "GitMergeFileMerger run failed for %s: %s; using overwrite",
                path,
                exc,
            )
            return await self._fallback.merge(
                sandbox=sandbox,
                path=path,
                lowerdir=lowerdir,
                repo_root=repo_root,
                upperdir_content=upperdir_content,
            )

        raw = str(getattr(response, "result", "") or "").strip()
        try:
            exit_code = int(raw.splitlines()[-1]) if raw else -1
        except ValueError:
            exit_code = -1

        read_cmd = (
            f"if [ -f {shlex.quote(output)} ]; then "
            f"base64 < {shlex.quote(output)} | tr -d '\\n'; fi"
        )
        read_resp = await self._exec(sandbox, read_cmd, timeout=30)
        merged_b64 = str(getattr(read_resp, "result", "") or "").strip()
        try:
            merged = base64.b64decode(merged_b64).decode("utf-8")
        except Exception:
            merged = upperdir_content  # fall back to upperdir

        if exit_code < 0 and not merged:
            # git merge-file totally failed (e.g. base missing). Fall
            # back to overwrite so the write still lands.
            return await self._fallback.merge(
                sandbox=sandbox,
                path=path,
                lowerdir=lowerdir,
                repo_root=repo_root,
                upperdir_content=upperdir_content,
            )

        return MergeResult(
            merged_content=merged,
            conflicts=max(exit_code, 0),
            strategy="git_merge_file",
        )


def _relpath(abs_path: str, repo_root: str) -> str:
    root = repo_root.rstrip("/") + "/"
    if abs_path.startswith(root):
        return abs_path[len(root) :]
    return abs_path.lstrip("/")


def _safe_id(rel: str) -> str:
    # Collapse slashes and unusual chars so one merge's staging files
    # never collide with another's even if paths differ only in /.
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in rel)


__all__ = [
    "GitMergeFileMerger",
    "MergeResult",
    "Merger",
    "OverwriteMerger",
]
