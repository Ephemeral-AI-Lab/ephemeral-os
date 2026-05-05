"""Phase 2 of the API latency reduction plan: gitignore oracle cold-start.

Covers two backends of ``_LayerStackGitignoreOracle``:

* ``git`` (default) — disk-cached materialized workspace under
  ``<storage_root>/cache/gitignore-<version>/`` with atomic-rename install
  and ``.ready`` marker.
* ``pathspec`` — pure-Python ``GitIgnoreSpec`` evaluator that reads
  ``.gitignore`` content via ``LayerStackManager.read_text`` (no materialize,
  no ``git init``, no subprocess).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from sandbox.layer_stack import LayerChange, LayerStackManager
from sandbox.occ.content.gitignore_oracle import LayerStackGitignoreOracle
from sandbox.occ.content.hashing import ContentHasher


def _have_git() -> bool:
    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(not _have_git(), reason="git binary not on PATH")


def _publish(manager: LayerStackManager, tmp_path: Path, rel: str, content: bytes) -> None:
    source = tmp_path / "sources" / rel.replace("/", "-")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)
    manager.publish_changes(
        [
            LayerChange(
                path=rel,
                kind="write",
                content_hash=ContentHasher().hash_bytes(content),
                source_path=str(source),
            )
        ]
    )


def _seed_repo(manager: LayerStackManager, tmp_path: Path) -> None:
    _publish(manager, tmp_path, ".gitignore", b"build/*\n!build/keep.txt\n")
    _publish(manager, tmp_path, "pkg/.gitignore", b"*.tmp\n!important.tmp\n")


def test_disk_cache_workspace_built_under_storage_root(tmp_path: Path) -> None:
    manager = LayerStackManager(tmp_path / "stack")
    _seed_repo(manager, tmp_path)

    oracle = LayerStackGitignoreOracle(manager, backend="git")
    assert oracle.is_ignored("build/out.o") is True
    # First call paid the build cost — capture before later cache-hits zero it.
    materialize_s = oracle.last_materialize_s
    git_init_s = oracle.last_git_init_s
    assert oracle.is_ignored("build/keep.txt") is False
    assert oracle.is_ignored("pkg/cache.tmp") is True
    assert oracle.is_ignored("pkg/important.tmp") is False

    snapshot = manager.read_active_manifest()
    cached = manager.storage_root / "cache" / f"gitignore-{snapshot.version}"
    assert (cached / ".ready").is_file()
    assert (cached / ".gitignore").is_file()
    assert (cached / ".git").is_dir()
    assert materialize_s > 0.0
    assert git_init_s > 0.0
    # Subsequent calls hit the in-memory cache and report no extra work.
    assert oracle.last_materialize_s == 0.0
    assert oracle.last_git_init_s == 0.0


def test_disk_cache_warm_attach_skips_build_cost(tmp_path: Path) -> None:
    manager = LayerStackManager(tmp_path / "stack")
    _seed_repo(manager, tmp_path)

    # First instance builds the cache.
    LayerStackGitignoreOracle(manager, backend="git").is_ignored(
        "build/out.o"
    )

    # A *fresh* oracle instance (simulating a new runtime process) should
    # attach to the existing on-disk workspace without paying materialize or
    # git init.
    fresh = LayerStackGitignoreOracle(manager, backend="git")
    assert fresh.is_ignored("build/out.o") is True
    assert fresh.last_materialize_s == 0.0
    assert fresh.last_git_init_s == 0.0


def test_disk_cache_atomic_rename_handles_concurrent_winner(tmp_path: Path) -> None:
    manager = LayerStackManager(tmp_path / "stack")
    _seed_repo(manager, tmp_path)
    snapshot = manager.read_active_manifest()

    # Pre-populate the final cache dir to simulate a concurrent process that
    # already finished building. The current builder must rename-fail, clean
    # up its staging, and reuse the existing ready dir.
    final = manager.storage_root / "cache" / f"gitignore-{snapshot.version}"
    final.mkdir(parents=True)
    # Construct a real workspace inside it so check-ignore works.
    (final / ".gitignore").write_text("build/*\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(final), "init", "-q"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (final / ".ready").write_text("", encoding="utf-8")

    oracle = LayerStackGitignoreOracle(manager, backend="git")
    assert oracle.is_ignored("build/out.o") is True
    # Cache hit on a ready dir: no build cost.
    assert oracle.last_materialize_s == 0.0
    assert oracle.last_git_init_s == 0.0


def test_old_cache_versions_evicted_on_build(tmp_path: Path) -> None:
    manager = LayerStackManager(tmp_path / "stack")
    _seed_repo(manager, tmp_path)

    # Drop legacy cache dirs for ancient versions.
    cache = manager.storage_root / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    stale = cache / "gitignore-0"
    stale.mkdir()
    (stale / ".ready").write_text("", encoding="utf-8")

    # Bump the manifest well past the keep-window (default 16) so the
    # eviction threshold sweeps version 0 cleanly.
    for i in range(20):
        _publish(manager, tmp_path, f"src/file_{i:02d}.py", b"x\n")

    LayerStackGitignoreOracle(manager, backend="git").is_ignored(
        "build/out.o"
    )

    assert not stale.is_dir(), "old cache dir should have been evicted"


def test_pathspec_backend_skips_materialize_and_git_init(tmp_path: Path) -> None:
    manager = LayerStackManager(tmp_path / "stack")
    _seed_repo(manager, tmp_path)

    oracle = LayerStackGitignoreOracle(manager, backend="pathspec")
    assert oracle.is_ignored("build/out.o") is True
    assert oracle.is_ignored("build/keep.txt") is False
    assert oracle.is_ignored("pkg/cache.tmp") is True
    assert oracle.is_ignored("pkg/important.tmp") is False

    # No on-disk workspace expected.
    cache = manager.storage_root / "cache"
    if cache.is_dir():
        for child in cache.iterdir():
            assert not child.name.startswith("gitignore-"), (
                "pathspec backend must not materialize a workspace"
            )

    # Timings reported as zero — no subprocess ran.
    assert oracle.last_materialize_s == 0.0
    assert oracle.last_git_init_s == 0.0


def test_pathspec_backend_matches_git_backend_on_layer_stack(tmp_path: Path) -> None:
    manager = LayerStackManager(tmp_path / "stack")
    _seed_repo(manager, tmp_path)

    git_oracle = LayerStackGitignoreOracle(manager, backend="git")
    pathspec_oracle = LayerStackGitignoreOracle(
        manager, backend="pathspec"
    )

    paths = [
        "build/out.o",
        "build/keep.txt",
        "build/sub/keep.txt",
        "pkg/cache.tmp",
        "pkg/important.tmp",
        "pkg/nested/x.tmp",
        "src/main.py",
    ]
    for p in paths:
        assert git_oracle.is_ignored(p) == pathspec_oracle.is_ignored(p), p


