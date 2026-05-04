from __future__ import annotations

from pathlib import Path

from stack_overlay.experiment_suite import (
    SuiteProfile,
    _run_e10,
    _run_large_upperdir_to_occ,
    _run_package_install_upperdirs_to_occ,
)


def test_large_file_upperdir_captures_to_occ_merge(tmp_path: Path) -> None:
    result = _run_large_upperdir_to_occ(
        tmp_path / "large-upperdir",
        file_size_bytes=512 * 1024,
    )

    assert result["success"] is True
    assert result["changes"] == 2
    assert result["largest_file_bytes"] == 512 * 1024
    assert result["content_hash_matches"] is True


def test_install_shaped_upperdirs_capture_to_occ_merge(tmp_path: Path) -> None:
    result = _run_package_install_upperdirs_to_occ(
        tmp_path / "package-installs",
        npm_packages=12,
        pip_packages=10,
        files_per_package=4,
    )

    assert result["success"] is True
    runs = {run["workflow"]: run for run in result["runs"]}
    assert runs["npm_install"]["success"] is True
    assert runs["npm_install"]["sentinel_present"] is True
    assert runs["npm_install"]["changes"] > 12 * 4
    assert runs["pip_install_target"]["success"] is True
    assert runs["pip_install_target"]["sentinel_present"] is True
    assert runs["pip_install_target"]["changes"] > 10 * 4


def test_e10_fails_when_large_upperdir_workload_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class _DummyManager:
        class _Snapshot:
            depth = 0

        def snapshot(self) -> _DummyManager._Snapshot:
            return self._Snapshot()

    class _DummyOcc:
        def __init__(self, manager: _DummyManager) -> None:
            self.manager = manager

    monkeypatch.setattr(
        "stack_overlay.experiment_suite.LayerManager.create",
        lambda *args, **kwargs: _DummyManager(),
    )
    monkeypatch.setattr(
        "stack_overlay.experiment_suite.OccCommitter",
        _DummyOcc,
    )
    monkeypatch.setattr(
        "stack_overlay.experiment_suite._e10_same_path_conflict",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "stack_overlay.experiment_suite._e10_delete_noop",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "stack_overlay.experiment_suite._e10_create_conflict",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "stack_overlay.experiment_suite._run_direct_merge_matrix",
        lambda: {"violations": 0},
    )
    monkeypatch.setattr(
        "stack_overlay.experiment_suite._run_large_diff_benchmark",
        lambda root: {"runs": [{"success": True}]},
    )
    monkeypatch.setattr(
        "stack_overlay.experiment_suite._run_large_upperdir_to_occ",
        lambda root: {"success": False},
    )
    monkeypatch.setattr(
        "stack_overlay.experiment_suite._run_package_install_upperdirs_to_occ",
        lambda root: {"success": True},
    )

    result = _run_e10(
        tmp_path / "e10",
        SuiteProfile(
            e4_runs=0,
            e4_shell_ops=0,
            e4_api_ops=0,
            e5_commits=0,
            e6_runs=0,
            e7_commits=0,
            e10_iterations=1,
        ),
        progress_log=None,
    )

    assert result["status"] == "failed"
    assert result["metrics"]["large_upperdir_to_occ"]["success"] is False
