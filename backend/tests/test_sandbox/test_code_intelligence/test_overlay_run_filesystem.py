"""Unit tests for overlay_run filesystem helpers."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from sandbox.code_intelligence.overlay.run import (
    ClassifyOutcome,
    PolicyRejectOutcome,
    REJECT_DOTGIT,
    check_ignore_factory,
    direct_merge_factory,
    lowerdir_base_factory,
    narrow_prune_opaque_factory,
    walk_upperdir,
    write_diff_ndjson,
    _write_result_json,
    write_reject_ndjson,
)


def test_walk_upperdir_yields_files_and_skips_plain_dirs(tmp_path: Path) -> None:
    root = tmp_path / "upper"
    root.mkdir()
    (root / "a.py").write_text("a\n", encoding="utf-8")
    (root / "pkg").mkdir()
    (root / "pkg" / "b.py").write_text("b\n", encoding="utf-8")

    rels = sorted(e.rel for e in walk_upperdir(str(root)))
    assert rels == ["a.py", "pkg/b.py"]


def test_walk_upperdir_handles_missing_root(tmp_path: Path) -> None:
    assert list(walk_upperdir(str(tmp_path / "missing"))) == []


# ---------------------------------------------------------------------------
# NDJSON emitters
# ---------------------------------------------------------------------------


def test_write_diff_ndjson_meta_and_entries(tmp_path: Path) -> None:
    outcome = ClassifyOutcome(
        gitinclude=(
            __import__("sandbox.code_intelligence.overlay.run", fromlist=["GitincludeChange"])
            .GitincludeChange(
                path="a.py",
                kind="modify",
                base_content="old\n",
                base_existed=True,
                final_content="new\n",
            ),
        ),
        gitignore_paths=(".venv/cfg",),
        direct_merged_bytes=12,
        whiteouts_gitinclude=0,
        whiteouts_gitignore_refused=0,
        dotgit_rejects=0,
    )

    path = write_diff_ndjson(
        run_dir=str(tmp_path),
        exit_code=0,
        outcome=outcome,
        upper_bytes=99,
        upper_files=3,
        run_timings={"total": 0.5, "classify": 0.2},
    )

    lines = Path(path).read_text(encoding="utf-8").splitlines()
    meta = json.loads(lines[0])
    assert meta["_meta"]["gitinclude_changes"] == 1
    assert meta["_meta"]["gitignore_changes"] == 1
    assert meta["_meta"]["gitignore_paths"] == [".venv/cfg"]
    assert meta["_meta"]["run_timings"] == {"total": 0.5, "classify": 0.2}
    entry = json.loads(lines[1])
    assert entry["path"] == "a.py"
    assert entry["kind"] == "modify"
    assert entry["base_content"] == "old\n"
    assert entry["final_content"] == "new\n"
    assert entry["strict_base"] is True


def test_write_reject_ndjson_emits_reject_block(tmp_path: Path) -> None:
    reject = PolicyRejectOutcome(reason=REJECT_DOTGIT, paths=(".git/config",))
    path = write_reject_ndjson(
        run_dir=str(tmp_path),
        reject=reject,
        run_timings={"total": 0.2},
    )
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload == {
        "_reject": {
            "reason": REJECT_DOTGIT,
            "paths": [".git/config"],
            "run_timings": {"total": 0.2},
        }
    }


def test_write_result_json_is_atomic_completion_marker(tmp_path: Path) -> None:
    path = _write_result_json(
        run_dir=str(tmp_path),
        exit_code=7,
        rejected={"reason": "overlay_rejected_dotgit_writes", "paths": [".git/config"]},
        run_timings={"total": 0.2},
    )

    assert Path(path).name == "result.json"
    assert not list(tmp_path.glob("result.json.tmp-*"))
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert payload == {
        "exit_code": 7,
        "rejected": {
            "reason": "overlay_rejected_dotgit_writes",
            "paths": [".git/config"],
        },
        "run_timings": {"total": 0.2},
    }


# ---------------------------------------------------------------------------
# check_ignore_factory — real git check-ignore against a fixture repo.
# ---------------------------------------------------------------------------


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Tester"], check=True
    )


def test_lowerdir_base_factory_reads_gitinclude_base(tmp_path: Path) -> None:
    repo = tmp_path / "lowerdir-repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "app.py").write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "seed"], check=True)
    (repo / "app.py").write_text("dirty\n", encoding="utf-8")

    read_base = lowerdir_base_factory(lower_root=str(repo))

    assert read_base("app.py") == b"dirty\n"


def test_lowerdir_base_factory_reads_gitignored_base(tmp_path: Path) -> None:
    repo = tmp_path / "lowerdir-ignored"
    repo.mkdir()
    _init_repo(repo)
    (repo / ".gitignore").write_text(".venv/\nnode_modules/\n", encoding="utf-8")
    (repo / "app.py").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "seed"], check=True)
    (repo / ".venv").mkdir()
    (repo / ".venv" / "pyvenv.cfg").write_text("home=/usr\n", encoding="utf-8")

    read_base = lowerdir_base_factory(lower_root=str(repo))

    assert read_base(".venv/pyvenv.cfg") == b"home=/usr\n"


def test_check_ignore_factory_splits_gitinclude_and_ignored(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / ".gitignore").write_text(".venv/\nnode_modules/\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("hi\n", encoding="utf-8")

    check = check_ignore_factory(repo_root=str(repo))

    ignored = check(
        [
            "src/app.py",
            ".venv/pyvenv.cfg",
            "node_modules/pkg/index.js",
            "README.md",  # not matched by any .gitignore rule
        ]
    )
    assert ignored == {".venv/pyvenv.cfg", "node_modules/pkg/index.js"}


def test_check_ignore_factory_empty_input_returns_empty_set(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    assert check_ignore_factory(repo_root=str(repo))([]) == set()


def test_check_ignore_factory_matches_dir_only_pattern_with_trailing_slash(
    tmp_path: Path,
) -> None:
    # Regression for opaque-dir routing bug: a dir-only gitignore pattern
    # like ".pytest_cache/" does NOT match bare ".pytest_cache" when the
    # path is absent on the live side (sandbox created it in upper only).
    # Passing the rel with a trailing slash matches correctly. The
    # classifier relies on this behavior.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / ".gitignore").write_text(".pytest_cache/\n", encoding="utf-8")

    check = check_ignore_factory(repo_root=str(repo))
    assert ".pytest_cache" not in check([".pytest_cache"])  # bare, absent
    assert ".pytest_cache/" in check([".pytest_cache/"])  # with slash


# ---------------------------------------------------------------------------
# narrow_prune_opaque_factory — on-disk behavior.
# ---------------------------------------------------------------------------


def test_narrow_prune_opaque_deletes_only_lower_only_children(
    tmp_path: Path,
) -> None:
    live_root = tmp_path / "live"
    upper_root = tmp_path / "upper"
    live_dir = live_root / ".pytest_cache"
    upper_dir = upper_root / ".pytest_cache"
    live_dir.mkdir(parents=True)
    upper_dir.mkdir(parents=True)

    # Both sides have "shared.txt"; only live has "stale.txt"; only
    # upper has "new.txt" (no-op for prune — merge will write it later).
    (live_dir / "shared.txt").write_text("old", encoding="utf-8")
    (live_dir / "stale.txt").write_text("stale", encoding="utf-8")
    (upper_dir / "shared.txt").write_text("new", encoding="utf-8")
    (upper_dir / "new.txt").write_text("new", encoding="utf-8")

    prune = narrow_prune_opaque_factory(live_root=str(live_root))
    count = prune(".pytest_cache", str(upper_dir))

    assert count == 1
    assert (live_dir / "shared.txt").exists()  # preserved for rename-over
    assert not (live_dir / "stale.txt").exists()  # pruned


def test_narrow_prune_opaque_recurses_into_lower_only_subdirs(
    tmp_path: Path,
) -> None:
    live_root = tmp_path / "live"
    upper_root = tmp_path / "upper"
    live_dir = live_root / ".cache"
    upper_dir = upper_root / ".cache"
    (live_dir / "__pycache__").mkdir(parents=True)
    (live_dir / "__pycache__" / "a.pyc").write_bytes(b"pyc")
    upper_dir.mkdir(parents=True)

    prune = narrow_prune_opaque_factory(live_root=str(live_root))
    count = prune(".cache", str(upper_dir))

    assert count == 1
    assert not (live_dir / "__pycache__").exists()


def test_narrow_prune_opaque_unlinks_symlink_children_without_following(
    tmp_path: Path,
) -> None:
    # Critical safety property: if the live dir contains a symlink to an
    # *outside* directory, prune must unlink the symlink itself and not
    # descend into the target.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")

    live_root = tmp_path / "live"
    upper_root = tmp_path / "upper"
    live_dir = live_root / ".cache"
    upper_dir = upper_root / ".cache"
    live_dir.mkdir(parents=True)
    upper_dir.mkdir(parents=True)
    os.symlink(str(outside), str(live_dir / "linked"))

    prune = narrow_prune_opaque_factory(live_root=str(live_root))
    count = prune(".cache", str(upper_dir))

    assert count == 1
    assert not (live_dir / "linked").exists()
    assert (outside / "keep.txt").exists()  # NOT followed into


def test_narrow_prune_opaque_returns_zero_when_live_dir_absent(
    tmp_path: Path,
) -> None:
    prune = narrow_prune_opaque_factory(live_root=str(tmp_path))
    # Nothing exists at this rel — should be a no-op, not an error.
    assert prune("missing/dir", str(tmp_path / "upper_missing")) == 0


# ---------------------------------------------------------------------------
# direct_merge_factory — atomic rename into live
# ---------------------------------------------------------------------------


def test_direct_merge_writes_file_and_is_observably_atomic(tmp_path: Path) -> None:
    upper = tmp_path / "upper"
    upper.mkdir()
    live = tmp_path / "live"
    live.mkdir()
    src = upper / ".venv" / "pyvenv.cfg"
    src.parent.mkdir(parents=True)
    src.write_text("home=/usr\n", encoding="utf-8")

    merge = direct_merge_factory(live_root=str(live))
    st = src.stat()
    bytes_written = merge(".venv/pyvenv.cfg", str(src), st)

    target = live / ".venv" / "pyvenv.cfg"
    assert target.read_text(encoding="utf-8") == "home=/usr\n"
    assert bytes_written == len("home=/usr\n")

    # No .overlay-merge temp files left behind in the parent.
    stray = [p.name for p in (live / ".venv").iterdir() if ".overlay-merge" in p.name]
    assert stray == []


def test_direct_merge_overwrites_existing_live_file_last_writer_wins(
    tmp_path: Path,
) -> None:
    upper = tmp_path / "upper"
    upper.mkdir()
    live = tmp_path / "live"
    live.mkdir()
    (live / "dep.txt").write_text("old\n", encoding="utf-8")
    (upper / "dep.txt").write_text("new\n", encoding="utf-8")

    merge = direct_merge_factory(live_root=str(live))
    merge("dep.txt", str(upper / "dep.txt"), (upper / "dep.txt").stat())

    assert (live / "dep.txt").read_text(encoding="utf-8") == "new\n"


# ---------------------------------------------------------------------------
# Edge case: mixed gitinclude + gitignore writes (plan §0 row)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# run_user_command cwd invariant: relative paths must resolve against workspace
# ---------------------------------------------------------------------------


def test_run_user_command_runs_in_workspace_cwd(tmp_path: Path) -> None:
    from sandbox.code_intelligence.overlay.run import run_user_command

    stdout, exit_code = run_user_command(
        user_cmd="pwd",
        stdin_bytes=None,
        cwd=str(tmp_path),
        stdout_path=str(tmp_path / "stdout.bin"),
    )
    assert exit_code == 0
    assert Path(stdout.decode().strip()) == tmp_path.resolve()


def test_run_user_command_resolves_relative_paths_against_cwd(tmp_path: Path) -> None:
    from sandbox.code_intelligence.overlay.run import run_user_command

    (tmp_path / "marker.txt").write_text("hi\n", encoding="utf-8")
    stdout, exit_code = run_user_command(
        user_cmd="cat marker.txt",
        stdin_bytes=None,
        cwd=str(tmp_path),
        stdout_path=str(tmp_path / "stdout.bin"),
    )
    assert exit_code == 0
    assert stdout == b"hi\n"


def test_run_user_command_disables_optional_git_locks(tmp_path: Path) -> None:
    from sandbox.code_intelligence.overlay.run import run_user_command

    stdout, exit_code = run_user_command(
        user_cmd='printf "%s" "$GIT_OPTIONAL_LOCKS"',
        stdin_bytes=None,
        cwd=str(tmp_path),
        stdout_path=str(tmp_path / "stdout.bin"),
    )
    assert exit_code == 0
    assert stdout == b"0"


# ---------------------------------------------------------------------------
# _parse_args smoke: catch typos in the CLI without needing Linux execution.
# ---------------------------------------------------------------------------


def test_parse_args_accepts_the_full_argument_surface() -> None:
    from sandbox.code_intelligence.overlay.run import _parse_args

    ns = _parse_args(
        [
            "--workspace-root", "/ws",
            "--run-dir", "/run",
            "--upper-size-mb", "256",
            "--user-cmd-b64", "ZWNobyBoaQ==",
            "--stdin-b64", "c3RkaW4=",
        ]
    )
    assert ns.workspace_root == "/ws"
    assert ns.run_dir == "/run"
    assert ns.upper_size_mb == 256
    assert ns.user_cmd_b64 == "ZWNobyBoaQ=="
    assert ns.stdin_b64 == "c3RkaW4="


def test_parse_args_rejects_missing_required_argument() -> None:
    from sandbox.code_intelligence.overlay.run import _parse_args

    with pytest.raises(SystemExit):
        _parse_args(["--workspace-root", "/ws"])
