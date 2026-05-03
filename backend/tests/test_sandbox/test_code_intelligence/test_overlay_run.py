"""Unit tests for ``overlay_run`` classifier and helpers.

The mount + user-command portion of ``overlay_run.py`` can only be
exercised on Linux with ``unshare`` / overlayfs / ``userxattr``. This
module targets the pure classifier, whiteout/opaque detection, NDJSON
emitter, and the ``git check-ignore`` batch helper — all of which run on
darwin with a real host ``git`` binary and synthetic upperdir trees.

Each test covers one branch called out in
``docs/architecture/overlay-sandbox-plan.md`` §3 / §8 PR 2.
"""

from __future__ import annotations

import os
import stat
from types import SimpleNamespace


from sandbox.code_intelligence.overlay.run import (
    Classifier,
    ClassifyOutcome,
    PolicyRejectOutcome,
    REJECT_DOTGIT,
    REJECT_GITIGNORE_WHITEOUT,
    REJECT_NON_UTF8_GITINCLUDE,
    REJECT_UNSUPPORTED_OPAQUE_DIR,
    REJECT_UNSUPPORTED_SYMLINK,
    UpperEntry,
    is_opaque_dir,
    is_symlink,
    is_whiteout,
    reject_exit_code,
)


# ---------------------------------------------------------------------------
# Entry builders: construct UpperEntry values without touching real xattrs.
# ---------------------------------------------------------------------------


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


def _regular_entry(
    rel: str, *, upper_path: str = "", xattrs: dict[bytes, bytes] | None = None, size: int = 1
) -> UpperEntry:
    return UpperEntry(
        rel=rel,
        st=_fake_stat(size=size),
        xattrs=dict(xattrs or {}),
        upper_path=upper_path or f"/synthetic/upper/{rel}",
    )


def _whiteout_char_entry(rel: str) -> UpperEntry:
    return UpperEntry(
        rel=rel,
        st=_fake_stat(mode=stat.S_IFCHR, rdev=0),
        xattrs={},
        upper_path=f"/synthetic/upper/{rel}",
    )


def _whiteout_rootless_entry(rel: str) -> UpperEntry:
    return UpperEntry(
        rel=rel,
        st=_fake_stat(size=0),
        xattrs={b"user.overlay.whiteout": b""},
        upper_path=f"/synthetic/upper/{rel}",
    )


def _opaque_dir_entry(rel: str, *, ns: bytes = b"user.overlay.opaque") -> UpperEntry:
    return UpperEntry(
        rel=rel,
        st=_fake_stat(mode=stat.S_IFDIR | 0o755),
        xattrs={ns: b"y"},
        upper_path=f"/synthetic/upper/{rel}",
    )


def _symlink_entry(rel: str) -> UpperEntry:
    return UpperEntry(
        rel=rel,
        st=_fake_stat(mode=stat.S_IFLNK | 0o777, size=7),
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
        # ``ignored`` is matched against the *wire* rels the classifier
        # sends to check_ignore. Dir entries arrive with a trailing "/".
        # Tests that want to ignore a bare dir rel can pass either
        # ".venv" or ".venv/"; the harness tolerates both.
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


# ---------------------------------------------------------------------------
# is_whiteout / is_opaque_dir / is_symlink
# ---------------------------------------------------------------------------


def test_is_whiteout_privileged_char_device() -> None:
    st = _fake_stat(mode=stat.S_IFCHR, rdev=0)
    assert is_whiteout(st, {}) is True


def test_is_whiteout_rootless_userxattr_zero_size_regular() -> None:
    st = _fake_stat(size=0)
    assert is_whiteout(st, {b"user.overlay.whiteout": b""}) is True


def test_is_whiteout_false_when_regular_non_zero_without_xattr() -> None:
    st = _fake_stat(size=10)
    assert is_whiteout(st, {}) is False


def test_is_whiteout_false_when_rootless_but_no_xattr() -> None:
    st = _fake_stat(size=0)
    assert is_whiteout(st, {}) is False


def test_is_opaque_dir_both_xattr_namespaces() -> None:
    st = _fake_stat(mode=stat.S_IFDIR | 0o755)
    assert is_opaque_dir(st, {b"trusted.overlay.opaque": b"y"}) is True
    assert is_opaque_dir(st, {b"user.overlay.opaque": b"y"}) is True
    assert is_opaque_dir(st, {}) is False


def test_is_opaque_dir_false_on_non_dir() -> None:
    st = _fake_stat(mode=stat.S_IFREG)
    assert is_opaque_dir(st, {b"user.overlay.opaque": b"y"}) is False


def test_is_symlink_positive() -> None:
    assert is_symlink(_fake_stat(mode=stat.S_IFLNK | 0o777)) is True
    assert is_symlink(_fake_stat(mode=stat.S_IFREG)) is False


# ---------------------------------------------------------------------------
# Classifier.classify: .git/* reject
# ---------------------------------------------------------------------------


def test_classifier_rejects_dotgit_writes_before_any_other_work() -> None:
    env = _Classifier(
        upper_bytes={".git/config": b"x"},
        base_bytes={},
        ignored=set(),
    )
    result = env.classifier().classify(
        [_regular_entry(".git/config"), _regular_entry("src/app.py")]
    )
    assert isinstance(result, PolicyRejectOutcome)
    assert result.reason == REJECT_DOTGIT
    assert ".git/config" in result.paths


def test_classifier_ignores_benign_dotgit_index_refresh() -> None:
    env = _Classifier(
        upper_bytes={
            ".git/index": b"refreshed-index",
            ".git/index.lock": b"transient-lock",
            "src/app.py": b"new",
        },
        base_bytes={"src/app.py": b"old"},
        ignored=set(),
    )
    result = env.classifier().classify(
        [
            _regular_entry(".git/index"),
            _regular_entry(".git/index.lock"),
            _regular_entry("src/app.py"),
        ]
    )
    assert isinstance(result, ClassifyOutcome)
    assert [change.path for change in result.gitinclude] == ["src/app.py"]
    assert env.check_ignore_calls == [["src/app.py"]]


def test_classifier_still_rejects_dotgit_mutation_with_index_refresh() -> None:
    env = _Classifier(
        upper_bytes={".git/index": b"refreshed-index", ".git/config": b"x"},
        base_bytes={},
        ignored=set(),
    )
    result = env.classifier().classify(
        [_regular_entry(".git/index"), _regular_entry(".git/config")]
    )

    assert isinstance(result, PolicyRejectOutcome)
    assert result.reason == REJECT_DOTGIT
    assert result.paths == (".git/config",)


def test_classifier_rejects_dotgit_even_for_nested_paths() -> None:
    env = _Classifier(upper_bytes={}, base_bytes={}, ignored=set())
    result = env.classifier().classify(
        [_regular_entry(".git/objects/pack/pack-xyz")]
    )
    assert isinstance(result, PolicyRejectOutcome)
    assert result.reason == REJECT_DOTGIT


# ---------------------------------------------------------------------------
# Classifier.classify: gitinclude add / modify / delete
# ---------------------------------------------------------------------------


def test_classifier_emits_gitinclude_create_for_new_file() -> None:
    env = _Classifier(
        upper_bytes={"src/new.py": b"print('new')\n"},
        base_bytes={},
        ignored=set(),
    )
    result = env.classifier().classify([_regular_entry("src/new.py")])
    assert isinstance(result, ClassifyOutcome)
    assert len(result.gitinclude) == 1
    change = result.gitinclude[0]
    assert change.kind == "create"
    assert change.base_existed is False
    assert change.base_content == ""
    assert change.final_content == "print('new')\n"
    assert result.gitignore_paths == ()


def test_classifier_emits_gitinclude_modify_for_existing_file() -> None:
    env = _Classifier(
        upper_bytes={"src/app.py": b"after\n"},
        base_bytes={"src/app.py": b"before\n"},
        ignored=set(),
    )
    result = env.classifier().classify([_regular_entry("src/app.py")])
    assert isinstance(result, ClassifyOutcome)
    change = result.gitinclude[0]
    assert change.kind == "modify"
    assert change.base_existed is True
    assert change.base_content == "before\n"
    assert change.final_content == "after\n"


def test_classifier_emits_gitinclude_delete_for_whiteout() -> None:
    env = _Classifier(
        upper_bytes={},
        base_bytes={"src/gone.py": b"rip\n"},
        ignored=set(),
    )
    result = env.classifier().classify([_whiteout_char_entry("src/gone.py")])
    assert isinstance(result, ClassifyOutcome)
    assert result.whiteouts_gitinclude == 1
    change = result.gitinclude[0]
    assert change.kind == "delete"
    assert change.base_existed is True
    assert change.base_content == "rip\n"
    assert change.final_content is None


def test_classifier_emits_gitinclude_delete_for_rootless_whiteout() -> None:
    env = _Classifier(
        upper_bytes={},
        base_bytes={"src/gone.py": b"rip\n"},
        ignored=set(),
    )
    result = env.classifier().classify([_whiteout_rootless_entry("src/gone.py")])
    assert isinstance(result, ClassifyOutcome)
    change = result.gitinclude[0]
    assert change.kind == "delete"
    assert change.base_existed is True


# ---------------------------------------------------------------------------
# Classifier.classify: gitignore route
# ---------------------------------------------------------------------------


def test_classifier_direct_merges_gitignore_create() -> None:
    env = _Classifier(
        upper_bytes={".venv/pyvenv.cfg": b"home=/usr\n"},
        base_bytes={},
        ignored={".venv/pyvenv.cfg"},
    )
    result = env.classifier().classify([_regular_entry(".venv/pyvenv.cfg")])
    assert isinstance(result, ClassifyOutcome)
    assert result.gitinclude == ()
    assert result.gitignore_paths == (".venv/pyvenv.cfg",)
    assert env.merged == [(".venv/pyvenv.cfg", len(b"home=/usr\n"))]
    assert result.direct_merged_bytes == len(b"home=/usr\n")


def test_classifier_direct_merges_gitignore_binary_bytes() -> None:
    payload = b"\xff\xfe\x00\x01not-utf-8"
    env = _Classifier(
        upper_bytes={"node_modules/pkg/a.so": payload},
        base_bytes={},
        ignored={"node_modules/pkg/a.so"},
    )
    result = env.classifier().classify([_regular_entry("node_modules/pkg/a.so")])
    # Non-UTF-8 content on gitignore route is fine; bytes pass through.
    assert isinstance(result, ClassifyOutcome)
    assert result.gitignore_paths == ("node_modules/pkg/a.so",)
    assert env.merged == [("node_modules/pkg/a.so", len(payload))]


def test_classifier_rejects_gitignore_whiteout() -> None:
    env = _Classifier(
        upper_bytes={},
        base_bytes={},
        ignored={".venv/pyvenv.cfg"},
    )
    result = env.classifier().classify([_whiteout_char_entry(".venv/pyvenv.cfg")])
    assert isinstance(result, PolicyRejectOutcome)
    assert result.reason == REJECT_GITIGNORE_WHITEOUT


def test_classifier_accepts_gitignore_opaque_dir_via_narrow_prune() -> None:
    # Opaque dir on a gitignored path now narrow-prunes instead of rejecting.
    # Classifier should invoke prune_opaque_narrow(rel, upper_path) once
    # and include the rel in the gitignore_paths tally.
    env = _Classifier(upper_bytes={}, base_bytes={}, ignored={".pytest_cache"})
    result = env.classifier().classify(
        [_opaque_dir_entry(".pytest_cache", ns=b"user.overlay.opaque")]
    )
    assert isinstance(result, ClassifyOutcome)
    assert ".pytest_cache" in result.gitignore_paths
    assert env.pruned == [(".pytest_cache", "/synthetic/upper/.pytest_cache")]


def test_classifier_sends_trailing_slash_for_dir_rels_to_check_ignore() -> None:
    # Dir-only .gitignore patterns (".pytest_cache/") only match when the
    # path passed to `git check-ignore` has a trailing slash or the path
    # exists as a directory on the live side. Sandbox-created dirs often
    # don't exist on lower at check time, so the classifier must pass
    # the slash explicitly. Verifies the wire format.
    env = _Classifier(
        upper_bytes={"src/app.py": b"x\n"},
        base_bytes={},
        ignored={".pytest_cache"},
    )
    env.classifier().classify(
        [
            _regular_entry("src/app.py"),
            _opaque_dir_entry(".pytest_cache"),
        ]
    )
    assert len(env.check_ignore_calls) == 1
    wire = env.check_ignore_calls[0]
    assert "src/app.py" in wire  # files stay bare
    assert ".pytest_cache/" in wire  # dirs get trailing "/"
    assert ".pytest_cache" not in wire


# ---------------------------------------------------------------------------
# Classifier.classify: kind-gate rejects on gitinclude route
# ---------------------------------------------------------------------------


def test_classifier_rejects_gitinclude_symlink() -> None:
    env = _Classifier(upper_bytes={}, base_bytes={}, ignored=set())
    result = env.classifier().classify([_symlink_entry("src/link")])
    assert isinstance(result, PolicyRejectOutcome)
    assert result.reason == REJECT_UNSUPPORTED_SYMLINK


def test_classifier_rejects_gitinclude_opaque_dir() -> None:
    env = _Classifier(upper_bytes={}, base_bytes={}, ignored=set())
    result = env.classifier().classify([_opaque_dir_entry("src/pkg")])
    assert isinstance(result, PolicyRejectOutcome)
    assert result.reason == REJECT_UNSUPPORTED_OPAQUE_DIR


# ---------------------------------------------------------------------------
# Classifier.classify: mode-only short-circuit + non-UTF-8 reject
# ---------------------------------------------------------------------------


def test_classifier_skips_mode_only_change_when_content_equal_to_base() -> None:
    env = _Classifier(
        upper_bytes={"src/app.py": b"same\n"},
        base_bytes={"src/app.py": b"same\n"},
        ignored=set(),
    )
    result = env.classifier().classify([_regular_entry("src/app.py")])
    assert isinstance(result, ClassifyOutcome)
    assert result.gitinclude == ()


def test_classifier_rejects_non_utf8_on_gitinclude_route() -> None:
    env = _Classifier(
        upper_bytes={"src/app.py": b"\xff\xfe\x00binary"},
        base_bytes={},
        ignored=set(),
    )
    result = env.classifier().classify([_regular_entry("src/app.py")])
    assert isinstance(result, PolicyRejectOutcome)
    assert result.reason == REJECT_NON_UTF8_GITINCLUDE
    assert result.paths == ("src/app.py",)


def test_classifier_rejects_before_applying_gitignore_route_side_effects() -> None:
    env = _Classifier(
        upper_bytes={
            ".venv/pyvenv.cfg": b"home=/usr\n",
            "src/app.py": b"\xff\xfe\x00binary",
        },
        base_bytes={},
        ignored={".pytest_cache", ".venv/pyvenv.cfg"},
    )
    result = env.classifier().classify(
        [
            _opaque_dir_entry(".pytest_cache"),
            _regular_entry(".venv/pyvenv.cfg"),
            _regular_entry("src/app.py"),
        ]
    )

    assert isinstance(result, PolicyRejectOutcome)
    assert result.reason == REJECT_NON_UTF8_GITINCLUDE
    assert result.paths == ("src/app.py",)
    assert env.merged == []
    assert env.pruned == []


def test_classifier_rejects_non_utf8_base_content_on_gitinclude_modify() -> None:
    env = _Classifier(
        upper_bytes={"src/app.py": b"ok\n"},
        base_bytes={"src/app.py": b"\xff\xfeold"},
        ignored=set(),
    )
    result = env.classifier().classify([_regular_entry("src/app.py")])
    assert isinstance(result, PolicyRejectOutcome)
    assert result.reason == REJECT_NON_UTF8_GITINCLUDE


# ---------------------------------------------------------------------------
# Classifier.classify: mixed gitinclude + gitignore
# ---------------------------------------------------------------------------


def test_classifier_accepts_mixed_gitinclude_and_gitignore() -> None:
    env = _Classifier(
        upper_bytes={
            "src/app.py": b"new source\n",
            ".venv/x.cfg": b"dep=1\n",
        },
        base_bytes={"src/app.py": b"old source\n"},
        ignored={".venv/x.cfg"},
    )
    result = env.classifier().classify(
        [
            _regular_entry("src/app.py"),
            _regular_entry(".venv/x.cfg"),
        ]
    )
    assert isinstance(result, ClassifyOutcome)
    assert [c.path for c in result.gitinclude] == ["src/app.py"]
    assert result.gitignore_paths == (".venv/x.cfg",)


# ---------------------------------------------------------------------------
# reject_exit_code covers every declared reason
# ---------------------------------------------------------------------------


def test_reject_exit_codes_are_distinct_sentinels() -> None:
    reasons = [
        REJECT_DOTGIT,
        REJECT_GITIGNORE_WHITEOUT,
        REJECT_UNSUPPORTED_SYMLINK,
        REJECT_UNSUPPORTED_OPAQUE_DIR,
        REJECT_NON_UTF8_GITINCLUDE,
    ]
    codes = {reject_exit_code(r) for r in reasons}
    assert len(codes) == len(reasons), "policy codes collide"
    for code in codes:
        assert 200 < code < 256


# ---------------------------------------------------------------------------
# walk_upperdir — real filesystem walk
# ---------------------------------------------------------------------------
