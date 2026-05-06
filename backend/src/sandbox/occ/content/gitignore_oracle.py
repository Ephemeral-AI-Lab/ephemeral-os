"""Cached ``git check-ignore`` wrapper for OCC route decisions.

Two oracle backends share the ``is_ignored`` / ``filter_ignored`` interface:

* :class:`GitignoreOracle` — shells out to ``git check-ignore`` against a
  materialized workspace. Authoritative; matches every git semantic detail.
* :class:`PathspecGitignoreOracle` — pure-Python evaluator based on the
  ``pathspec`` library. Reads ``.gitignore`` files directly and never spawns
  a subprocess. Selected at runtime via ``EPHEMERALOS_GITIGNORE_BACKEND=pathspec``.

The pathspec backend is feature-flagged; default remains the git backend
until parity is proven (see Phase 2b of the API latency reduction plan).
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sandbox.layer_stack import LayerStackManager
from sandbox.layer_stack.manifest import Manifest

if TYPE_CHECKING:  # pragma: no cover - import only for type-checkers
    import pathspec  # noqa: F401


_pathspec_module: Any | None = None


def _load_pathspec() -> Any:
    """Lazy-import ``pathspec`` so the git backend works in environments without it.

    The pathspec dependency is required only by ``PathspecGitignoreOracle``;
    keeping the import deferred lets the sandbox runtime boot in images that
    don't ship ``pathspec`` (the common case until 2b is fully proven).
    """
    global _pathspec_module
    if _pathspec_module is None:
        _pathspec_module = importlib.import_module("pathspec")
    return _pathspec_module


@dataclass(frozen=True)
class RunOutcome:
    returncode: int
    stdout: bytes
    stderr: bytes


RunFn = Callable[[list[str], bytes], RunOutcome]
ReadGitignoreFn = Callable[[str], str | None]


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
        """Return ``True`` if *path* is gitignored."""
        if path in self._cache:
            return self._cache[path]
        self._populate([path])
        return self._cache.get(path, False)

    def filter_ignored(self, paths: Iterable[str]) -> set[str]:
        """Return the subset of *paths* that are gitignored."""
        unique_paths = list(dict.fromkeys(paths))
        uncached = [p for p in unique_paths if p not in self._cache]
        if uncached:
            self._populate(uncached)
        return {p for p in unique_paths if self._cache.get(p, False)}

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
                source, _line, pattern, raw_path = record
                if source and not pattern.startswith(b"!"):
                    ignored.add(raw_path.decode("utf-8").rstrip("/"))
        for path in paths:
            self._cache[path] = path in ignored or path.rstrip("/") in ignored


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


class PathspecGitignoreOracle:
    """Pure-Python gitignore evaluator backed by the ``pathspec`` library.

    Honours the standard nested-``.gitignore`` semantics: a ``.gitignore`` at
    directory ``D`` applies only to paths under ``D``, and a deeper match
    overrides a shallower one. Within one ``.gitignore``, ``!`` re-includes
    work via ``GitIgnoreSpec.check_file``.

    ``read_gitignore(dir_rel)`` returns the contents of the ``.gitignore``
    inside ``dir_rel`` (``""`` for the workspace root) or ``None`` if absent.
    When omitted, the oracle reads from ``<workspace_root>/<dir_rel>/.gitignore``
    on the filesystem; this gives drop-in parity with :class:`GitignoreOracle`.

    Note: this backend is case-sensitive. Git on case-folding filesystems may
    accept ``error.log`` for a pattern of ``Error.log``; that mode is rare in
    sandbox workloads and out of scope for the parity guarantees here.
    """

    def __init__(
        self,
        workspace_root: str,
        *,
        read_gitignore: ReadGitignoreFn | None = None,
    ) -> None:
        self._workspace_root = str(workspace_root or "")
        self._read = read_gitignore or self._read_from_disk
        self._path_cache: dict[str, bool] = {}
        self._dir_cache: dict[str, bool] = {}
        self._spec_cache: dict[str, Any | None] = {}
        self._pathspec = _load_pathspec()

    def is_ignored(self, path: str) -> bool:
        if path in self._path_cache:
            return self._path_cache[path]
        result = self._evaluate_file(path)
        self._path_cache[path] = result
        return result

    def filter_ignored(self, paths: Iterable[str]) -> set[str]:
        unique_paths = list(dict.fromkeys(paths))
        return {p for p in unique_paths if self.is_ignored(p)}

    def _evaluate_file(self, path: str) -> bool:
        rel = path.lstrip("/")
        if not rel:
            return False
        parts = rel.split("/")
        # Git's directory-exclusion seal: if any ancestor directory of *path*
        # is excluded, no deeper ``!`` re-include can rescue contents under
        # that directory. Test each ancestor (root → path.parent) first.
        accum = ""
        for depth in range(len(parts) - 1):
            accum = f"{accum}/{parts[depth]}" if accum else parts[depth]
            if self._is_dir_excluded(accum):
                return True
        return self._match_with_inheritance(rel, as_directory=False)

    def _is_dir_excluded(self, dir_rel: str) -> bool:
        if dir_rel in self._dir_cache:
            return self._dir_cache[dir_rel]
        # A nested ancestor can be excluded even if the parent dir is not —
        # but if any ancestor is excluded, propagate up.
        parts = dir_rel.split("/")
        accum = ""
        for depth in range(len(parts) - 1):
            accum = f"{accum}/{parts[depth]}" if accum else parts[depth]
            if self._is_dir_excluded(accum):
                self._dir_cache[dir_rel] = True
                return True
        excluded = self._match_with_inheritance(dir_rel, as_directory=True)
        self._dir_cache[dir_rel] = excluded
        return excluded

    def _match_with_inheritance(self, path: str, *, as_directory: bool) -> bool:
        """Last-match-wins evaluation across every ``.gitignore`` above *path*.

        ``as_directory`` appends a trailing slash so directory-only patterns
        (``foo/``) take effect. Caller is responsible for the directory-seal
        early-exit; this is the unsealed evaluator.
        """
        parts = path.split("/")
        target = path + "/" if as_directory else path
        ignored = False
        accum = ""
        for depth in range(len(parts)):
            spec = self._spec_for_dir(accum)
            if spec is not None:
                sub = target[len(accum) :].lstrip("/") if accum else target
                if sub:
                    outcome = spec.check_file(sub)
                    if outcome.include is True:
                        ignored = True
                    elif outcome.include is False:
                        ignored = False
            accum = f"{accum}/{parts[depth]}" if accum else parts[depth]
        return ignored

    def _spec_for_dir(self, dir_rel: str) -> Any | None:
        if dir_rel in self._spec_cache:
            return self._spec_cache[dir_rel]
        content = self._read(dir_rel)
        spec: Any | None = None
        if content:
            spec = self._pathspec.GitIgnoreSpec.from_lines(content.splitlines())
        self._spec_cache[dir_rel] = spec
        return spec

    def _read_from_disk(self, dir_rel: str) -> str | None:
        root = Path(self._workspace_root) if self._workspace_root else Path()
        gitignore = (root / dir_rel / ".gitignore") if dir_rel else (root / ".gitignore")
        try:
            if gitignore.is_file():
                return gitignore.read_text(encoding="utf-8")
        except OSError:
            return None
        return None


def select_backend() -> str:
    """Return ``"pathspec"`` or ``"git"`` based on ``EPHEMERALOS_GITIGNORE_BACKEND``."""
    raw = (os.environ.get("EPHEMERALOS_GITIGNORE_BACKEND") or "").strip().lower()
    if raw == "pathspec":
        return "pathspec"
    return "git"


_GITIGNORE_CACHE_DIR = "cache"
_GITIGNORE_CACHE_PREFIX = "gitignore-"
_GITIGNORE_CACHE_KEEP = 16
_GITIGNORE_READY_MARKER = ".ready"


class LayerStackGitignoreOracle(GitignoreOracle):
    """Evaluate gitignore rules from a layer-stack snapshot.

    Two backends share the API:

    * ``git`` (default): materializes the snapshot into a per-version on-disk
      workspace under ``<storage_root>/cache/gitignore-<version>/`` and runs
      ``git check-ignore`` against it. The on-disk workspace is built once
      per manifest version and shared across processes — so a fresh runtime
      process attaches to an existing ready workspace in O(stat) instead of
      paying the materialize + ``git init`` cost.
    * ``pathspec``: skips materialization entirely and reads ``.gitignore``
      files directly from the snapshot via ``LayerStackManager.read_text``.
      Selected via ``EPHEMERALOS_GITIGNORE_BACKEND=pathspec``.
    """

    def __init__(
        self,
        layer_stack: LayerStackManager,
        *,
        backend: str | None = None,
    ) -> None:
        self._layer_stack = layer_stack
        self._backend = backend or select_backend()
        self._oracles: dict[int, GitignoreOracle | PathspecGitignoreOracle] = {}
        self.cache_hits: int = 0
        self.cache_misses: int = 0
        self.last_materialize_s: float = 0.0
        self.last_git_init_s: float = 0.0

    def is_ignored(self, path: str) -> bool:
        return self.is_ignored_in_snapshot(
            path,
            self._layer_stack.read_active_manifest(),
        )

    def filter_ignored(self, paths: Iterable[str]) -> set[str]:
        snapshot = self._layer_stack.read_active_manifest()
        return {path for path in paths if self.is_ignored_in_snapshot(path, snapshot)}

    def is_ignored_in_snapshot(self, path: str, snapshot: Manifest) -> bool:
        return self._oracle_for_snapshot(snapshot).is_ignored(path)

    def _oracle_for_snapshot(
        self,
        snapshot: Manifest,
    ) -> GitignoreOracle | PathspecGitignoreOracle:
        version = snapshot.version
        cached = self._oracles.get(version)
        if cached is not None and self._cache_entry_is_valid(cached):
            self.cache_hits += 1
            self.last_materialize_s = 0.0
            self.last_git_init_s = 0.0
            return cached

        self.cache_misses += 1
        if cached is not None:
            # In-memory entry referenced an evicted on-disk workspace; drop it.
            self._oracles.pop(version, None)
        if self._backend == "pathspec":
            oracle = self._build_pathspec_oracle(snapshot)
        else:
            oracle = self._build_git_oracle(snapshot)
        self._oracles[version] = oracle
        return oracle

    @staticmethod
    def _cache_entry_is_valid(
        oracle: GitignoreOracle | PathspecGitignoreOracle,
    ) -> bool:
        # Pathspec oracles read from a callable, not from disk — always valid.
        # Git oracles wrap a disk workspace that opportunistic cache eviction may
        # have removed underneath us; check the workspace directory still exists.
        if isinstance(oracle, GitignoreOracle):
            workspace = Path(getattr(oracle, "_workspace_root", "") or "")
            return workspace.is_dir()
        return True

    def _build_pathspec_oracle(self, snapshot: Manifest) -> PathspecGitignoreOracle:
        # No materialize, no git init: read .gitignore content lazily via the
        # merged view.
        self.last_materialize_s = 0.0
        self.last_git_init_s = 0.0

        def _read_gitignore(dir_rel: str) -> str | None:
            rel = f"{dir_rel}/.gitignore" if dir_rel else ".gitignore"
            content, exists = self._layer_stack.read_text(rel, snapshot)
            return content if exists else None

        return PathspecGitignoreOracle(
            workspace_root="",
            read_gitignore=_read_gitignore,
        )

    def _build_git_oracle(self, snapshot: Manifest) -> GitignoreOracle:
        workspace = _ensure_disk_cached_workspace(
            self._layer_stack,
            snapshot,
            timings=self,
        )
        return GitignoreOracle(str(workspace))


def _gitignore_cache_root(storage_root: Path) -> Path:
    return storage_root / _GITIGNORE_CACHE_DIR


def _gitignore_cache_path(storage_root: Path, version: int) -> Path:
    return _gitignore_cache_root(storage_root) / f"{_GITIGNORE_CACHE_PREFIX}{version}"


def _ensure_disk_cached_workspace(
    layer_stack: LayerStackManager,
    snapshot: Manifest,
    *,
    timings: "LayerStackGitignoreOracle | None" = None,
) -> Path:
    """Return a ready-to-use materialized workspace for *snapshot*.

    Builds under a unique temp dir + ``.ready`` marker and ``os.rename``s into
    place atomically. Concurrent builders fall back to whichever rename wins.
    Evicts older cache entries (version < active - N) opportunistically so
    growth stays bounded without a periodic sweep.
    """
    storage_root = Path(layer_stack.storage_root)
    cache_root = _gitignore_cache_root(storage_root)
    cache_root.mkdir(parents=True, exist_ok=True)

    final = _gitignore_cache_path(storage_root, snapshot.version)
    ready = final / _GITIGNORE_READY_MARKER
    if ready.is_file():
        if timings is not None:
            timings.last_materialize_s = 0.0
            timings.last_git_init_s = 0.0
        return final

    staging = cache_root / f"{_GITIGNORE_CACHE_PREFIX}{snapshot.version}.tmp.{uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=False)
    materialize_start = time.perf_counter()
    layer_stack.materialize(staging, snapshot)
    materialize_s = time.perf_counter() - materialize_start
    git_init_start = time.perf_counter()
    _init_git_workspace(staging)
    git_init_s = time.perf_counter() - git_init_start
    (staging / _GITIGNORE_READY_MARKER).write_text("", encoding="utf-8")

    try:
        staging.rename(final)
    except OSError:
        # Another process (or thread) won the race. Use the existing ready
        # workspace and discard our staged copy.
        shutil.rmtree(staging, ignore_errors=True)
        if not (final / _GITIGNORE_READY_MARKER).is_file():
            raise RuntimeError(
                f"gitignore cache neither installed nor ready: {final}"
            )
        if timings is not None:
            timings.last_materialize_s = 0.0
            timings.last_git_init_s = 0.0
        return final

    _evict_stale_gitignore_cache(
        layer_stack,
        keep_last_n=_GITIGNORE_CACHE_KEEP,
        protect_version=snapshot.version,
    )
    if timings is not None:
        timings.last_materialize_s = materialize_s
        timings.last_git_init_s = git_init_s
    return final


def _evict_stale_gitignore_cache(
    layer_stack: LayerStackManager,
    *,
    keep_last_n: int,
    protect_version: int | None = None,
) -> None:
    """Remove cached gitignore workspaces whose version is below the threshold.

    Triggered opportunistically on cache build to bound on-disk growth.
    Long-running snapshot leases can refer to versions far behind the active
    manifest; ``protect_version`` keeps the caller's chosen version alive so
    we never evict a workspace we are about to use.
    """
    storage_root = Path(layer_stack.storage_root)
    cache_root = _gitignore_cache_root(storage_root)
    if not cache_root.is_dir():
        return
    active_version = layer_stack.read_active_manifest().version
    threshold = active_version - keep_last_n
    for child in cache_root.iterdir():
        name = child.name
        if not name.startswith(_GITIGNORE_CACHE_PREFIX):
            continue
        suffix = name[len(_GITIGNORE_CACHE_PREFIX) :]
        # Skip in-flight tmp dirs from concurrent builders.
        if ".tmp." in suffix:
            continue
        try:
            version = int(suffix)
        except ValueError:
            continue
        if protect_version is not None and version == protect_version:
            continue
        if version <= threshold:
            shutil.rmtree(child, ignore_errors=True)


def _init_git_workspace(workspace: Path) -> None:
    completed = subprocess.run(
        ["git", "-C", str(workspace), "init", "-q"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", "replace")
        raise RuntimeError(f"git init for OCC gitignore oracle failed: {stderr!r}")


__all__ = [
    "GitignoreOracle",
    "LayerStackGitignoreOracle",
    "PathspecGitignoreOracle",
    "ReadGitignoreFn",
    "RunFn",
    "RunOutcome",
    "select_backend",
]
