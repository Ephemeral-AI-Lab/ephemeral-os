"""Unit tests for :class:`OverlayAuditor` (OCC-gated v2).

These exercise the auditor in isolation — no real unshare / overlayfs —
by stubbing :class:`OverlayExec` with a pre-built upperdir tarball and
mocking the :class:`WriteCoordinator`. The contract:

* MODIFY / DELETE → one :class:`OperationChange` each, all submitted
  as a single ``commit_operation_against_base`` batch with
  ``strict_base=True``.
* SYMLINK / OPAQUE_DIR → :class:`OverlayUnsupportedChangeError` (D3a);
  no commit, no disk writes.
* Coordinator abort → audit result with ``changed_paths=[]`` and the
  conflict metadata surfaced through ``overlay_commit_*`` fields.
* ``attribute_changes=False`` → no commit; upperdir paths are reported
  on ``ambient_changed_paths``.
"""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from code_intelligence.hashing import content_hash
from code_intelligence.routing.overlay_auditor import (
    OverlayAuditor,
    OverlayAuditorConfig,
    OverlayUnsupportedChangeError,
)
from code_intelligence.routing.overlay_exec import OverlayRunResult
from code_intelligence.types import EditResult, OperationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_tar(
    *,
    modify: dict[str, bytes] | None = None,
    symlink: dict[str, str] | None = None,
) -> str:
    """Create a tar the walker parses into MODIFY/SYMLINK entries."""
    fd, path = tempfile.mkstemp(prefix="overlay-test-", suffix=".tar")
    os.close(fd)
    with tarfile.open(path, "w") as tar:
        for rel, content in (modify or {}).items():
            info = tarfile.TarInfo(name=f"./{rel}")
            info.size = len(content)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(content))
        for rel, target in (symlink or {}).items():
            info = tarfile.TarInfo(name=f"./{rel}")
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
    return path


def _make_run(audit_tar: str, *, stdout: str = "done", exit_code: int = 0) -> OverlayRunResult:
    return OverlayRunResult(
        run_id="test-run",
        exit_code=exit_code,
        stdout=stdout,
        audit_tar_path=audit_tar,
        run_dir="/tmp/overlay-test-run",
    )


class _StubOverlayExec:
    def __init__(self, run_result: OverlayRunResult) -> None:
        self._run_result = run_result
        self.calls: list[tuple[Any, str]] = []

    async def execute(
        self,
        sandbox: Any,
        command: str,
        *,
        lowerdir: str,
        repo_root: str,
        timeout: Any = None,
    ) -> OverlayRunResult:
        del lowerdir, repo_root, timeout
        self.calls.append((sandbox, command))
        return self._run_result


def _build_auditor(
    *,
    lowerdir: str,
    repo_root: str,
    run_result: OverlayRunResult,
    coordinator_result: OperationResult | None = None,
    lowerdir_refresh: Any = None,
) -> tuple[OverlayAuditor, MagicMock]:
    commit_result = coordinator_result or OperationResult(
        success=True,
        status="committed",
        files=(
            EditResult(success=True, file_path=f"{repo_root.rstrip('/')}/foo.py", message="ok"),
        ),
        conflict_file=None,
        conflict_reason="",
        timings={},
    )
    coordinator = MagicMock()
    coordinator.commit_operation_against_base = MagicMock(return_value=commit_result)

    async def _lowerdir_provider(_repo_root: str) -> str:
        return lowerdir

    async def _noop_exec(_sandbox, _command, **_kwargs):
        return SimpleNamespace(result="", exit_code=0)

    auditor = OverlayAuditor(
        workspace_root=repo_root,
        exec_process=_noop_exec,
        write_coordinator=coordinator,
        lowerdir_provider=_lowerdir_provider,
        lowerdir_refresh=lowerdir_refresh,
        config=OverlayAuditorConfig(),
    )
    auditor._overlay = _StubOverlayExec(run_result)  # type: ignore[attr-defined]

    # Patch the remote tar download to return the local tar path produced
    # by _build_tar — the real implementation streams base64 over ssh.
    async def _download(_sandbox, remote_path):
        return remote_path

    auditor._download_remote_tar = _download  # type: ignore[method-assign]
    return auditor, coordinator


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_modify_upperdir_commits_one_occ_batch(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    lowerdir = tmp_path / "lower"
    repo_root.mkdir()
    lowerdir.mkdir()
    (lowerdir / "foo.py").write_bytes(b"old\n")

    tar = _build_tar(modify={"foo.py": b"new\n"})
    try:
        run = _make_run(tar)
        auditor, coord = _build_auditor(
            lowerdir=str(lowerdir), repo_root=str(repo_root), run_result=run,
        )
        # Point the auditor at our local tar instead of a remote path.
        run = _make_run(tar)
        auditor._overlay = _StubOverlayExec(run)  # type: ignore[attr-defined]

        result = _run(
            auditor.execute(SimpleNamespace(), "python x.py", agent_id="alice"),
        )

        assert result.exit_code == 0
        assert result.changed_paths == [f"{repo_root}/foo.py"]
        coord.commit_operation_against_base.assert_called_once()
        args, kwargs = coord.commit_operation_against_base.call_args
        changes = args[0]
        assert len(changes) == 1
        change = changes[0]
        assert change.file_path == f"{repo_root}/foo.py"
        assert change.base_content == "old\n"
        assert change.base_hash == content_hash("old\n")
        assert change.final_content == "new\n"
        assert change.strict_base is True
        assert kwargs["edit_type"] == "svc_cmd_overlay"
        assert kwargs["agent_id"] == "alice"
    finally:
        # Auditor's cleanup_tar may have already unlinked the local tar.
        try:
            os.unlink(tar)
        except FileNotFoundError:
            pass


def test_empty_upperdir_skips_commit(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    lowerdir = tmp_path / "lower"
    repo_root.mkdir()
    lowerdir.mkdir()

    tar = _build_tar(modify={})
    try:
        run = _make_run(tar, stdout="noop")
        auditor, coord = _build_auditor(
            lowerdir=str(lowerdir), repo_root=str(repo_root), run_result=run,
        )
        auditor._overlay = _StubOverlayExec(run)  # type: ignore[attr-defined]

        result = _run(auditor.execute(SimpleNamespace(), "echo hi"))

        assert result.changed_paths == []
        assert result.ambient_changed_paths == []
        coord.commit_operation_against_base.assert_not_called()
    finally:
        # Auditor's cleanup_tar may have already unlinked the local tar.
        try:
            os.unlink(tar)
        except FileNotFoundError:
            pass


def test_create_new_file_flags_base_existed_false(tmp_path) -> None:
    """An upperdir file whose path is absent from lowerdir is a create."""
    repo_root = tmp_path / "repo"
    lowerdir = tmp_path / "lower"
    repo_root.mkdir()
    lowerdir.mkdir()

    tar = _build_tar(modify={"new_file.py": b"fresh\n"})
    try:
        run = _make_run(tar)
        auditor, coord = _build_auditor(
            lowerdir=str(lowerdir), repo_root=str(repo_root), run_result=run,
        )
        auditor._overlay = _StubOverlayExec(run)  # type: ignore[attr-defined]

        _run(auditor.execute(SimpleNamespace(), "touch"))

        changes = coord.commit_operation_against_base.call_args.args[0]
        assert len(changes) == 1
        assert changes[0].base_existed is False
        assert changes[0].base_content == ""
        assert changes[0].base_hash == ""
        assert changes[0].final_content == "fresh\n"
    finally:
        # Auditor's cleanup_tar may have already unlinked the local tar.
        try:
            os.unlink(tar)
        except FileNotFoundError:
            pass


def test_remote_lowerdir_read_uses_exec_transport(tmp_path) -> None:
    """Live lowerdirs are remote; base content must be read via sandbox exec."""
    repo_root = tmp_path / "repo"
    lowerdir = tmp_path / "lower"
    repo_root.mkdir()
    lowerdir.mkdir()
    (lowerdir / "foo.py").write_bytes(b"old-remote\n")

    tar = _build_tar(modify={"foo.py": b"new\n"})

    class _Process:
        async def exec(self, command: str, timeout: int | None = None):
            del timeout
            completed = await asyncio.to_thread(
                subprocess.run,
                command,
                shell=True,
                text=True,
                capture_output=True,
                check=False,
            )
            return SimpleNamespace(
                result=completed.stdout + completed.stderr,
                exit_code=completed.returncode,
            )

    async def _exec(_sandbox, command, **_kwargs):
        return await _Process().exec(command)

    try:
        run = _make_run(tar)
        auditor, coord = _build_auditor(
            lowerdir=str(lowerdir), repo_root=str(repo_root), run_result=run,
        )
        auditor._exec_process = _exec  # type: ignore[method-assign]
        auditor._overlay = _StubOverlayExec(run)  # type: ignore[attr-defined]

        _run(auditor.execute(SimpleNamespace(process=_Process()), "touch"))

        changes = coord.commit_operation_against_base.call_args.args[0]
        assert len(changes) == 1
        assert changes[0].base_existed is True
        assert changes[0].base_content == "old-remote\n"
        assert changes[0].base_hash == content_hash("old-remote\n")
    finally:
        try:
            os.unlink(tar)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# D3a rejection
# ---------------------------------------------------------------------------


def test_symlink_upperdir_raises_unsupported_change_error(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    lowerdir = tmp_path / "lower"
    repo_root.mkdir()
    lowerdir.mkdir()
    (lowerdir / "foo.py").write_bytes(b"old\n")

    tar = _build_tar(
        modify={"foo.py": b"new\n"},
        symlink={"link_to_foo": "foo.py"},
    )
    try:
        run = _make_run(tar)
        auditor, coord = _build_auditor(
            lowerdir=str(lowerdir), repo_root=str(repo_root), run_result=run,
        )
        auditor._overlay = _StubOverlayExec(run)  # type: ignore[attr-defined]

        with pytest.raises(OverlayUnsupportedChangeError, match="symlink"):
            _run(auditor.execute(SimpleNamespace(), "ln -s foo.py link"))

        coord.commit_operation_against_base.assert_not_called()
    finally:
        # Auditor's cleanup_tar may have already unlinked the local tar.
        try:
            os.unlink(tar)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# OCC abort surfaces cleanly
# ---------------------------------------------------------------------------


def test_aborted_version_returns_clean_audit_result(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    lowerdir = tmp_path / "lower"
    repo_root.mkdir()
    lowerdir.mkdir()
    (lowerdir / "foo.py").write_bytes(b"old\n")

    tar = _build_tar(modify={"foo.py": b"new\n"})
    try:
        run = _make_run(tar)
        aborted = OperationResult(
            success=False,
            status="aborted_version",
            files=(
                EditResult(
                    success=False,
                    file_path=f"{repo_root}/foo.py",
                    message="drift",
                ),
            ),
            conflict_file=f"{repo_root}/foo.py",
            conflict_reason="peer wrote first",
            timings={},
        )
        auditor, _coord = _build_auditor(
            lowerdir=str(lowerdir),
            repo_root=str(repo_root),
            run_result=run,
            coordinator_result=aborted,
        )
        auditor._overlay = _StubOverlayExec(run)  # type: ignore[attr-defined]

        result = _run(auditor.execute(SimpleNamespace(), "touch"))

        assert result.changed_paths == []
        assert result.overlay_commit_status == "aborted_version"
        assert result.overlay_conflict_file == f"{repo_root}/foo.py"
        assert result.overlay_conflict_reason == "peer wrote first"
        assert result.ambient_changed_paths == [f"{repo_root}/foo.py"]
    finally:
        # Auditor's cleanup_tar may have already unlinked the local tar.
        try:
            os.unlink(tar)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# attribute_changes=False
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Lowerdir refresh after commit
# ---------------------------------------------------------------------------


def test_successful_commit_invokes_lowerdir_refresh(tmp_path) -> None:
    """After a committed batch, the auditor hands the OperationChanges
    to the lowerdir_refresh callback so the next run sees the post-commit
    state (prevents false ``aborted_version`` from a stale snapshot)."""
    repo_root = tmp_path / "repo"
    lowerdir = tmp_path / "lower"
    repo_root.mkdir()
    lowerdir.mkdir()
    (lowerdir / "foo.py").write_bytes(b"old\n")

    captured: list[list[Any]] = []

    def _refresh(changes):
        captured.append(list(changes))

    tar = _build_tar(modify={"foo.py": b"new\n"})
    try:
        run = _make_run(tar)
        auditor, coord = _build_auditor(
            lowerdir=str(lowerdir),
            repo_root=str(repo_root),
            run_result=run,
            lowerdir_refresh=_refresh,
        )
        auditor._overlay = _StubOverlayExec(run)  # type: ignore[attr-defined]

        _run(auditor.execute(SimpleNamespace(), "touch"))

        coord.commit_operation_against_base.assert_called_once()
        assert len(captured) == 1
        refreshed = captured[0]
        assert len(refreshed) == 1
        assert refreshed[0].file_path == f"{repo_root}/foo.py"
        assert refreshed[0].final_content == "new\n"
    finally:
        try:
            os.unlink(tar)
        except FileNotFoundError:
            pass


def test_aborted_commit_does_not_invoke_lowerdir_refresh(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    lowerdir = tmp_path / "lower"
    repo_root.mkdir()
    lowerdir.mkdir()
    (lowerdir / "foo.py").write_bytes(b"old\n")

    calls: list[Any] = []

    def _refresh(changes):
        calls.append(changes)

    tar = _build_tar(modify={"foo.py": b"new\n"})
    try:
        run = _make_run(tar)
        aborted = OperationResult(
            success=False,
            status="aborted_version",
            files=(),
            conflict_file=f"{repo_root}/foo.py",
            conflict_reason="drift",
            timings={},
        )
        auditor, _coord = _build_auditor(
            lowerdir=str(lowerdir),
            repo_root=str(repo_root),
            run_result=run,
            coordinator_result=aborted,
            lowerdir_refresh=_refresh,
        )
        auditor._overlay = _StubOverlayExec(run)  # type: ignore[attr-defined]

        _run(auditor.execute(SimpleNamespace(), "touch"))

        # Refresh must not fire on abort — stale lowerdir is safer than
        # one that claims a commit landed when it didn't.
        assert calls == []
    finally:
        try:
            os.unlink(tar)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# attribute_changes=False
# ---------------------------------------------------------------------------


def test_attribute_changes_false_skips_commit(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    lowerdir = tmp_path / "lower"
    repo_root.mkdir()
    lowerdir.mkdir()

    tar = _build_tar(modify={"foo.py": b"new\n"})
    try:
        run = _make_run(tar)
        auditor, coord = _build_auditor(
            lowerdir=str(lowerdir), repo_root=str(repo_root), run_result=run,
        )
        auditor._overlay = _StubOverlayExec(run)  # type: ignore[attr-defined]

        result = _run(
            auditor.execute(SimpleNamespace(), "echo", attribute_changes=False),
        )

        coord.commit_operation_against_base.assert_not_called()
        assert result.ambient_changed_paths == [f"{repo_root}/foo.py"]
        assert result.changed_paths == []
    finally:
        # Auditor's cleanup_tar may have already unlinked the local tar.
        try:
            os.unlink(tar)
        except FileNotFoundError:
            pass
