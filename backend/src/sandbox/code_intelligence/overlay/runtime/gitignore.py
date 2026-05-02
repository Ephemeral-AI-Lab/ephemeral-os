"""Gitignore routing helpers used inside the overlay namespace."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Iterator


def has_git_routing_metadata(lower_root: str) -> bool:
    """Return whether ``lower_root`` has enough git metadata for routing checks."""

    git_path = os.path.join(lower_root, ".git")
    return os.path.isdir(git_path) or os.path.isfile(git_path)


def check_ignore_factory(*, repo_root: str) -> Callable[[list[str]], set[str]]:
    """Return a callable that batch-checks gitignore membership."""

    def _check(paths: list[str]) -> set[str]:
        if not paths:
            return set()
        ignored: set[str] = set()
        for chunk in _chunk_paths(paths, byte_limit=1024 * 1024):
            stdin_bytes = b"\0".join(path.encode("utf-8") for path in chunk) + b"\0"
            proc = subprocess.run(
                [
                    "git",
                    "-C",
                    repo_root,
                    "check-ignore",
                    "-z",
                    "--stdin",
                    "--verbose",
                    "--non-matching",
                ],
                input=stdin_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if proc.returncode not in (0, 1):
                stderr = proc.stderr.decode("utf-8", "replace")
                raise RuntimeError(
                    f"git check-ignore failed: rc={proc.returncode} stderr={stderr!r}"
                )
            fields = proc.stdout.split(b"\0")
            if fields and fields[-1] == b"":
                fields = fields[:-1]
            for i in range(0, len(fields), 4):
                record = fields[i : i + 4]
                if len(record) < 4:
                    break
                source, _line, _pattern, path = record
                if source:
                    ignored.add(path.decode("utf-8"))
        return ignored

    return _check


def _chunk_paths(paths: list[str], *, byte_limit: int) -> Iterator[list[str]]:
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


__all__ = ["check_ignore_factory", "has_git_routing_metadata"]
