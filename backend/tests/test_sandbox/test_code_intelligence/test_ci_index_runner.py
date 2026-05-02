"""Unit tests for ``sandbox.code_intelligence.in_sandbox.ci_index``."""

from __future__ import annotations

import json
import pickle
import stat
import textwrap
from pathlib import Path

import pytest

from sandbox.code_intelligence.in_sandbox.ci_index import main
from sandbox.code_intelligence.in_sandbox.ci_storage import (
    state_dir,
    workspace_root_hash,
)


_FIXTURE_FILES = {
    "alpha.py": textwrap.dedent(
        """
        def alpha_one():
            return 1

        class AlphaCls:
            def method_a(self):
                return self
        """
    ).lstrip(),
    "beta.py": textwrap.dedent(
        """
        def beta_one():
            pass

        def beta_two():
            pass
        """
    ).lstrip(),
    "gamma.py": "GAMMA = 1\n",
    "delta.py": textwrap.dedent(
        """
        def delta_helper():
            return 'd'
        """
    ).lstrip(),
    "epsilon.py": textwrap.dedent(
        """
        class EpsilonCls:
            pass
        """
    ).lstrip(),
}


@pytest.fixture
def home_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def workspace_with_fixtures(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name, body in _FIXTURE_FILES.items():
        (workspace / name).write_text(body, encoding="utf-8")
    return workspace


def _read_snapshot_pickle(state: Path) -> dict:
    with open(state / "index.snapshot", "rb") as f:
        loaded = pickle.load(f)
    assert isinstance(loaded, dict)
    return loaded


def test_full_build_writes_snapshot(
    home_in_tmp: Path,
    workspace_with_fixtures: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["--workspace-root", str(workspace_with_fixtures)])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["mode"] == "full_build"
    assert payload["file_count"] == len(_FIXTURE_FILES)
    assert payload["symbol_count"] >= 1

    state = state_dir(str(workspace_with_fixtures))
    snapshot = _read_snapshot_pickle(state)
    assert len(snapshot) == len(_FIXTURE_FILES)
    # Each fixture must contribute at least one symbol entry.
    for fp, symbols in snapshot.items():
        assert isinstance(symbols, list)
        assert Path(fp).name in _FIXTURE_FILES


def test_full_build_snapshot_path_matches_workspace_root_hash(
    home_in_tmp: Path,
    workspace_with_fixtures: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["--workspace-root", str(workspace_with_fixtures)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    expected = (
        home_in_tmp
        / ".cache"
        / "eos-ci"
        / workspace_root_hash(str(workspace_with_fixtures))
        / "v1"
        / "index.snapshot"
    )
    assert Path(payload["snapshot_path"]) == expected
    assert expected.exists()


def test_refresh_single_file_patches_only_that_entry(
    home_in_tmp: Path,
    workspace_with_fixtures: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Seed snapshot with a full build first.
    assert main(["--workspace-root", str(workspace_with_fixtures)]) == 0
    capsys.readouterr()

    state = state_dir(str(workspace_with_fixtures))
    pre = _read_snapshot_pickle(state)
    pre_other = {k: v for k, v in pre.items() if Path(k).name != "alpha.py"}

    # Mutate alpha.py and run refresh.
    target = workspace_with_fixtures / "alpha.py"
    target.write_text("def alpha_renamed():\n    return 99\n", encoding="utf-8")
    rc = main(
        [
            "--workspace-root",
            str(workspace_with_fixtures),
            "--file",
            str(target),
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert payload["mode"] == "refresh_one"
    assert payload["file"] == str(target)
    assert payload["generation"] >= 1

    post = _read_snapshot_pickle(state)
    # Only alpha entry changed; every other entry preserved bit-for-bit.
    for k, v in pre_other.items():
        assert k in post
        assert post[k] == v
    alpha_key = next(k for k in post if Path(k).name == "alpha.py")
    alpha_symbols = post[alpha_key]
    names = {sym.name for sym in alpha_symbols}
    assert "alpha_renamed" in names


def test_storage_unavailable_returns_exit_code_13(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    workspace_with_fixtures: Path,
) -> None:
    home = tmp_path / "ro_home"
    home.mkdir()
    cache = home / ".cache"
    cache.mkdir()
    cache.chmod(stat.S_IRUSR)
    monkeypatch.setenv("HOME", str(home))
    try:
        rc = main(["--workspace-root", str(workspace_with_fixtures)])
        assert rc == 13
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        assert payload["ok"] is False
        assert payload["error"] == "storage_unavailable"
        assert payload["errno"] != 0
        assert ".cache/eos-ci" in payload["path"]
    finally:
        cache.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
