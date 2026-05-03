"""Unit tests for overlay_run metadata and NDJSON round trips."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sandbox.code_intelligence.overlay.run import (
    Classifier,
    ClassifyOutcome,
    PolicyRejectOutcome,
    lowerdir_base_factory,
    write_diff_ndjson,
    write_reject_ndjson,
)


def _fake_stat(
    *,
    mode: int = stat.S_IFREG | 0o644,
    size: int = 0,
    rdev: int = 0,
) -> os.stat_result:
    return SimpleNamespace(  # type: ignore[return-value]
        st_mode=mode,
        st_ino=1,
        st_dev=1,
        st_nlink=1,
        st_uid=0,
        st_gid=0,
        st_size=size,
        st_atime=0.0,
        st_mtime=0.0,
        st_ctime=0.0,
        st_rdev=rdev,
    )


def _regular_entry(rel: str, *, size: int = 1) -> Any:
    from sandbox.code_intelligence.overlay.run import UpperEntry

    return UpperEntry(
        rel=rel,
        st=_fake_stat(size=size),
        xattrs={},
        upper_path=f"/synthetic/upper/{rel}",
    )


class _Classifier:
    """Test harness that wires real-ish callbacks with in-memory state."""

    def __init__(
        self,
        *,
        upper_bytes: dict[str, bytes],
        base_bytes: dict[str, bytes],
        ignored: set[str],
    ) -> None:
        self.upper_bytes = upper_bytes
        self.base_bytes = base_bytes
        self.ignored = {r.rstrip("/") for r in ignored}
        self.merged: list[tuple[str, int]] = []
        self.check_ignore_calls: list[list[str]] = []
        self.pruned: list[tuple[str, str]] = []

    def read_upper(self, rel: str) -> bytes:
        return self.upper_bytes[rel]

    def read_base(self, rel: str) -> bytes | None:
        return self.base_bytes.get(rel)

    def check_ignore(self, rels: list[str]) -> set[str]:
        self.check_ignore_calls.append(list(rels))
        return {r for r in rels if r.rstrip("/") in self.ignored}

    def direct_merge(self, rel: str, upper_path: str, upper_st: os.stat_result) -> int:
        del upper_path, upper_st
        size = len(self.upper_bytes.get(rel, b""))
        self.merged.append((rel, size))
        return size

    def prune_opaque_narrow(self, rel: str, upper_dir: str) -> int:
        self.pruned.append((rel, upper_dir))
        return 0

    def classifier(self) -> Classifier:
        return Classifier(
            read_upper_bytes=self.read_upper,
            read_base=self.read_base,
            check_ignore=self.check_ignore,
            direct_merge=self.direct_merge,
            prune_opaque_narrow=self.prune_opaque_narrow,
        )


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Tester"], check=True
    )

def test_git_routing_metadata_accepts_git_dir_or_file(tmp_path: Path) -> None:
    from sandbox.code_intelligence.overlay.run import has_git_routing_metadata

    plain = tmp_path / "plain"
    plain.mkdir()
    assert has_git_routing_metadata(str(plain)) is False

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    assert has_git_routing_metadata(str(repo)) is True

    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text("gitdir: ../repo/.git\n", encoding="utf-8")
    assert has_git_routing_metadata(str(linked)) is True


def test_namespace_mount_root_uses_writable_tmp_prefix() -> None:
    from sandbox.code_intelligence.overlay import run as overlay_run

    assert overlay_run._NS_ROOT.startswith("/tmp/")
    assert overlay_run._NS_TMP.startswith(overlay_run._NS_ROOT + "/")
    assert overlay_run._NS_UPPER.startswith(overlay_run._NS_TMP + "/")
    assert overlay_run._NS_WORK.startswith(overlay_run._NS_TMP + "/")


# ---------------------------------------------------------------------------
# lowerdir_base_factory filesystem round-trip
# ---------------------------------------------------------------------------


def test_lowerdir_base_returns_none_for_missing_path(tmp_path: Path) -> None:
    read_base = lowerdir_base_factory(lower_root=str(tmp_path))

    assert read_base("not-in-lower.py") is None


def test_lowerdir_base_rejects_path_escape(tmp_path: Path) -> None:
    read_base = lowerdir_base_factory(lower_root=str(tmp_path))

    with pytest.raises(RuntimeError, match="escapes lowerdir"):
        read_base("../outside.py")


def test_lowerdir_base_reads_symlink_target_as_base_bytes(tmp_path: Path) -> None:
    target = tmp_path / "link"
    target.symlink_to("real-target")
    read_base = lowerdir_base_factory(lower_root=str(tmp_path))

    assert read_base("link") == b"real-target"


# ---------------------------------------------------------------------------
# NDJSON round-trip: write_diff_ndjson -> parse_diff_ndjson must be lossless
# on the fields the schema promises.
# ---------------------------------------------------------------------------


def test_ndjson_round_trip_preserves_gitinclude_and_gitignore(tmp_path: Path) -> None:
    from sandbox.code_intelligence.overlay.auditor import parse_diff_ndjson
    from sandbox.code_intelligence.overlay.run import GitincludeChange

    outcome = ClassifyOutcome(
        gitinclude=(
            GitincludeChange(
                path="src/app.py",
                kind="modify",
                base_content="old\n",
                base_existed=True,
                final_content="new\n",
            ),
            GitincludeChange(
                path="src/gone.py",
                kind="delete",
                base_content="bye\n",
                base_existed=True,
                final_content=None,
            ),
            GitincludeChange(
                path="src/new.py",
                kind="create",
                base_content="",
                base_existed=False,
                final_content="hi\n",
            ),
        ),
        gitignore_paths=(".venv/cfg", "node_modules/pkg/index.js"),
        direct_merged_bytes=123,
        whiteouts_gitinclude=1,
        whiteouts_gitignore_refused=0,
        dotgit_rejects=0,
    )

    path = write_diff_ndjson(
        run_dir=str(tmp_path),
        exit_code=0,
        outcome=outcome,
        upper_bytes=999,
        upper_files=5,
        run_timings={"total": 0.4, "user_command": 0.1},
    )
    raw = Path(path).read_text(encoding="utf-8")
    parsed = parse_diff_ndjson(raw)

    assert not isinstance(parsed, PolicyRejectOutcome)
    # No PolicyReject — parser returns OverlayDiff.
    assert parsed.upper_bytes == 999
    assert parsed.upper_files == 5
    assert parsed.direct_merged_bytes == 123
    assert parsed.whiteouts_gitinclude == 1
    assert parsed.gitignore_paths == (".venv/cfg", "node_modules/pkg/index.js")
    assert parsed.run_timings == {"total": 0.4, "user_command": 0.1}

    kinds = [c.kind for c in parsed.gitinclude_changes]
    assert kinds == ["modify", "delete", "create"]
    delete_change = [c for c in parsed.gitinclude_changes if c.kind == "delete"][0]
    assert delete_change.final_content is None
    create_change = [c for c in parsed.gitinclude_changes if c.kind == "create"][0]
    assert create_change.base_existed is False
    modify_change = [c for c in parsed.gitinclude_changes if c.kind == "modify"][0]
    assert modify_change.base_content == "old\n"
    assert modify_change.final_content == "new\n"


def test_ndjson_round_trip_preserves_reject_block(tmp_path: Path) -> None:
    from sandbox.code_intelligence.overlay.auditor import parse_diff_ndjson

    reject = PolicyRejectOutcome(
        reason="overlay_rejected_dotgit_writes",
        paths=(".git/config", ".git/objects/a"),
    )
    path = write_reject_ndjson(
        run_dir=str(tmp_path),
        reject=reject,
        run_timings={"total": 0.7},
    )
    raw = Path(path).read_text(encoding="utf-8")
    parsed = parse_diff_ndjson(raw)

    # parse_diff_ndjson returns OverlayPolicyReject (different dataclass,
    # same schema).
    assert parsed.reason == "overlay_rejected_dotgit_writes"  # type: ignore[union-attr]
    assert parsed.paths == (".git/config", ".git/objects/a")  # type: ignore[union-attr]
    assert parsed.run_timings == {"total": 0.7}  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Preserved: Edge case: mixed gitinclude + gitignore writes (plan §0 row)
# ---------------------------------------------------------------------------


def test_mixed_write_direct_merges_gitignore_even_when_gitinclude_path_is_present(
    tmp_path: Path,
) -> None:
    # The classifier is pure; the orchestrator is the one that decides
    # whether to commit gitinclude via OCC after gitignore already landed.
    # Here we just verify the classifier itself routes correctly.
    env = _Classifier(
        upper_bytes={
            "requirements.txt": b"foo==1.0\n",
            ".venv/lib/foo/__init__.py": b"# foo\n",
        },
        base_bytes={"requirements.txt": b""},
        ignored={".venv/lib/foo/__init__.py"},
    )
    result = env.classifier().classify(
        [
            _regular_entry("requirements.txt"),
            _regular_entry(".venv/lib/foo/__init__.py"),
        ]
    )
    assert isinstance(result, ClassifyOutcome)
    assert [c.path for c in result.gitinclude] == ["requirements.txt"]
    assert result.gitignore_paths == (".venv/lib/foo/__init__.py",)
    # Gitignored direct-merge runs inside the classifier (inside the ns
    # in production). That means it is already applied before the
    # orchestrator runs gitinclude OCC — the partial-apply contract.
    assert env.merged == [(".venv/lib/foo/__init__.py", len(b"# foo\n"))]
