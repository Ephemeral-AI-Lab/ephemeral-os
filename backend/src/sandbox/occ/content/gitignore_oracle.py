"""Cached ``git check-ignore`` wrapper for the OCC orchestrator.

The orchestrator routes gated changes through the OCC gate when the workspace
path is tracked, and through the direct merger when it is gitignored.
``GitignoreOracle`` answers that lookup with a per-process cache so a busy
batch never re-shells out for paths it has already classified.

Cache lifetime: the oracle's lifetime equals the runtime's. Mid-session
``.gitignore`` rewrites are out of scope (see plan §Risks).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class RunOutcome:
    returncode: int
    stdout: bytes
    stderr: bytes


RunFn = Callable[[list[str], bytes], RunOutcome]


def _default_run(argv: list[str], stdin_bytes: bytes) -> RunOutcome:
    proc = subprocess.run(
        argv,
        input=stdin_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return RunOutcome(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


class GitignoreOracle:
    """Cached ``git check-ignore -z --stdin --verbose --non-matching`` lookup."""

    _STDIN_BYTE_LIMIT = 1024 * 1024

    def __init__(
        self,
        workspace_root: str,
        *,
        run: RunFn | None = None,
    ) -> None:
        self._workspace_root = str(workspace_root or "")
        self._cache: dict[str, bool] = {}
        self._run = run or _default_run

    def is_ignored(self, path: str) -> bool:
        """Return ``True`` if *path* is gitignored, with subprocess elision on cache hit."""
        if path in self._cache:
            return self._cache[path]
        self._populate([path])
        return self._cache.get(path, False)

    def filter_ignored(self, paths: Iterable[str]) -> set[str]:
        """Return the subset of *paths* that are gitignored.

        Uncached paths are batched into a single ``git check-ignore`` call;
        cached paths short-circuit.
        """
        unique_paths = list(dict.fromkeys(paths))
        uncached = [p for p in unique_paths if p not in self._cache]
        if uncached:
            self._populate(uncached)
        return {p for p in unique_paths if self._cache.get(p, False)}

    # -- Internals ---------------------------------------------------------

    def _populate(self, paths: list[str]) -> None:
        if not paths:
            return
        ignored: set[str] = set()
        for chunk in _chunk_paths(paths, byte_limit=self._STDIN_BYTE_LIMIT):
            stdin_bytes = b"\0".join(p.encode("utf-8") for p in chunk) + b"\0"
            outcome = self._run(
                [
                    "git",
                    "-C",
                    self._workspace_root,
                    "check-ignore",
                    "-z",
                    "--stdin",
                    "--verbose",
                    "--non-matching",
                ],
                stdin_bytes,
            )
            if outcome.returncode not in (0, 1):
                stderr = outcome.stderr.decode("utf-8", "replace")
                raise RuntimeError(
                    f"git check-ignore failed: rc={outcome.returncode} stderr={stderr!r}"
                )
            fields = outcome.stdout.split(b"\0")
            if fields and fields[-1] == b"":
                fields = fields[:-1]
            for i in range(0, len(fields), 4):
                record = fields[i : i + 4]
                if len(record) < 4:
                    break
                source, _line, _pattern, raw_path = record
                if source:
                    ignored.add(raw_path.decode("utf-8").rstrip("/"))
        for path in paths:
            self._cache[path] = path in ignored or _strip_trailing_slash(path) in ignored


def _strip_trailing_slash(path: str) -> str:
    return path.rstrip("/")


def _chunk_paths(paths: list[str], *, byte_limit: int) -> Iterable[list[str]]:
    chunk: list[str] = []
    size = 0
    for path in paths:
        plen = len(path.encode("utf-8")) + 1
        if chunk and size + plen > byte_limit:
            yield chunk
            chunk = []
            size = 0
        chunk.append(path)
        size += plen
    if chunk:
        yield chunk


__all__ = ["GitignoreOracle", "RunFn", "RunOutcome"]
