"""Tests for ``overlay_auditor`` NDJSON parsing and result assembly.

End-to-end overlay execution is Linux-only; these tests focus on the
deterministic orchestrator-side logic: NDJSON parsing, policy-reject
surfacing on the ``SimpleNamespace`` result, mixed gitinclude + gitignore
partial-apply metadata, and the committer adapter.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from sandbox.code_intelligence.overlay.auditor import (
    OverlayAuditor,
    parse_diff_ndjson,
)
from sandbox.code_intelligence.overlay.command_committer import OverlayCommandCommitter
from sandbox.code_intelligence.overlay.types import (
    OverlayChange,
    OverlayDiff,
    OverlayPolicyReject,
    OverlayRunError,
)
from sandbox.code_intelligence.registry import (
    dispose_all_code_intelligence,
)


@pytest.fixture(autouse=True)
def _registry():
    dispose_all_code_intelligence()
    yield
    dispose_all_code_intelligence()


# ---------------------------------------------------------------------------
# parse_diff_ndjson
# ---------------------------------------------------------------------------


def _meta_line(**overrides) -> str:
    base = {
        "exit_code": 0,
        "upper_bytes": 0,
        "upper_files": 0,
        "gitinclude_changes": 0,
        "gitignore_changes": 0,
        "gitignore_paths": [],
        "whiteouts_gitinclude": 0,
        "whiteouts_gitignore_refused": 0,
        "dotgit_rejects": 0,
        "direct_merged_bytes": 0,
        "run_timings": {},
        "warnings": [],
    }
    base.update(overrides)
    return json.dumps({"_meta": base}, separators=(",", ":"))


def test_parse_ndjson_empty_body_raises() -> None:
    with pytest.raises(OverlayRunError):
        parse_diff_ndjson("")


def test_parse_ndjson_returns_policy_reject() -> None:
    raw = json.dumps(
        {
            "_reject": {
                "reason": "overlay_rejected_dotgit_writes",
                "paths": [".git/config"],
                "run_timings": {"classify": 0.2},
            }
        }
    )
    result = parse_diff_ndjson(raw)
    assert isinstance(result, OverlayPolicyReject)
    assert result.reason == "overlay_rejected_dotgit_writes"
    assert result.paths == (".git/config",)
    assert result.run_timings == {"classify": 0.2}


def test_parse_ndjson_meta_and_one_gitinclude_entry() -> None:
    raw = "\n".join(
        [
            _meta_line(
                gitinclude_changes=1,
                gitignore_changes=1,
                gitignore_paths=[".venv/cfg"],
                upper_bytes=42,
            ),
            json.dumps(
                {
                    "path": "src/app.py",
                    "kind": "modify",
                    "base_content": "before\n",
                    "base_existed": True,
                    "final_content": "after\n",
                    "strict_base": True,
                },
                separators=(",", ":"),
            ),
        ]
    )
    result = parse_diff_ndjson(raw)
    assert isinstance(result, OverlayDiff)
    assert result.upper_bytes == 42
    assert result.gitignore_paths == (".venv/cfg",)
    assert len(result.gitinclude_changes) == 1
    change = result.gitinclude_changes[0]
    assert change.path == "src/app.py"
    assert change.kind == "modify"
    assert change.base_content == "before\n"
    assert change.base_existed is True
    assert change.final_content == "after\n"


def test_parse_ndjson_delete_entry_has_none_final_content() -> None:
    raw = "\n".join(
        [
            _meta_line(gitinclude_changes=1, whiteouts_gitinclude=1),
            json.dumps(
                {
                    "path": "old.py",
                    "kind": "delete",
                    "base_content": "bye\n",
                    "base_existed": True,
                    "final_content": None,
                    "strict_base": True,
                },
                separators=(",", ":"),
            ),
        ]
    )
    result = parse_diff_ndjson(raw)
    assert isinstance(result, OverlayDiff)
    assert result.gitinclude_changes[0].final_content is None
    assert result.gitinclude_changes[0].kind == "delete"


def test_parse_ndjson_invalid_meta_raises() -> None:
    with pytest.raises(OverlayRunError):
        parse_diff_ndjson("not-json\n")


def test_parse_ndjson_invalid_entry_raises() -> None:
    raw = _meta_line(gitinclude_changes=1) + "\nnot-valid-json"
    with pytest.raises(OverlayRunError):
        parse_diff_ndjson(raw)


@pytest.mark.asyncio
async def test_read_diff_error_includes_overlay_output() -> None:
    async def _missing_diff_exec(_sandbox, _command, *, timeout=None):
        return SimpleNamespace(
            result="cat: /tmp/run/diff.ndjson: No such file or directory",
            exit_code=1,
        )

    auditor = OverlayAuditor(
        sandbox_id="overlay-missing-diff",
        workspace_root="/workspace",
        exec_process=_missing_diff_exec,
    )

    with pytest.raises(OverlayRunError) as exc_info:
        await auditor._read_diff(
            object(),
            SimpleNamespace(run_dir="/tmp/run"),
            overlay_stdout="mount setup failed",
            overlay_exit_code=255,
        )

    message = str(exc_info.value)
    assert "overlay_exit_code=255" in message
    assert "mount setup failed" in message


@pytest.mark.asyncio
async def test_local_daemon_readback_uses_filesystem_without_exec(
    tmp_path: Path,
) -> None:
    async def _should_not_exec(_sandbox, _command, *, timeout=None):
        raise AssertionError("local daemon readback should not shell out")

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "stdout.bin").write_text("local stdout\n", encoding="utf-8")
    (run_dir / "diff.ndjson").write_text(
        _meta_line(exit_code=0),
        encoding="utf-8",
    )
    auditor = OverlayAuditor(
        sandbox_id="local",
        workspace_root=str(tmp_path),
        exec_process=_should_not_exec,
    )
    lease = SimpleNamespace(run_dir=str(run_dir))

    assert await auditor._read_stdout(None, lease, fallback="fallback") == "local stdout\n"
    diff = await auditor._read_diff(
        None,
        lease,
        overlay_stdout="local stdout\n",
        overlay_exit_code=0,
    )
    assert isinstance(diff, OverlayDiff)

    await auditor._cleanup_run_dir(None, lease)
    assert not run_dir.exists()


# ---------------------------------------------------------------------------
# OverlayCommandCommitter as a pure data translator (no OCC).
#
# Slice 5a stripped the OCC commit out of OverlayCommandCommitter; the
# committer's only remaining responsibility is shape conversion. The
# strict-base contract is now exercised by AuditedCommandExecutor →
# WriteCoordinator integration tests in test_overlay_occ_decoupling.py.
# ---------------------------------------------------------------------------


def test_committer_translates_modify_to_strict_base_op_change(tmp_path: Path) -> None:
    committer = OverlayCommandCommitter(workspace_root=str(tmp_path))
    change = OverlayChange(
        path="app.py",
        kind="modify",
        base_content="old\n",
        base_existed=True,
        final_content="new\n",
    )
    [op_change] = committer.to_operation_changes([change])
    assert op_change.file_path == f"{tmp_path}/app.py"
    assert op_change.base_content == "old\n"
    assert op_change.final_content == "new\n"
    assert op_change.base_existed is True
    assert op_change.strict_base is True


def test_committer_translates_create_with_empty_base_hash(tmp_path: Path) -> None:
    committer = OverlayCommandCommitter(workspace_root=str(tmp_path))
    change = OverlayChange(
        path="new.py",
        kind="create",
        base_content="",
        base_existed=False,
        final_content="print('hi')\n",
    )
    [op_change] = committer.to_operation_changes([change])
    assert op_change.base_existed is False
    assert op_change.base_hash == ""


def test_committer_translates_delete_to_none_final_content(tmp_path: Path) -> None:
    committer = OverlayCommandCommitter(workspace_root=str(tmp_path))
    change = OverlayChange(
        path="gone.py",
        kind="delete",
        base_content="bye\n",
        base_existed=True,
        final_content=None,
    )
    [op_change] = committer.to_operation_changes([change])
    assert op_change.final_content is None
    assert op_change.base_existed is True


# ---------------------------------------------------------------------------
# Lowerdir freshness guard
# ---------------------------------------------------------------------------


def _make_guarded_auditor(tmp_path: Path) -> OverlayAuditor:
    git_dir = tmp_path / ".git"
    git_dir.mkdir(exist_ok=True)
    (git_dir / "index").write_text("index\n", encoding="utf-8")
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    async def _unused_exec(*_args, **_kwargs):
        raise AssertionError("freshness guard test should not execute commands")

    return OverlayAuditor(
        sandbox_id=f"freshness-{tmp_path.name}",
        workspace_root=str(tmp_path),
        exec_process=_unused_exec,
        daemon_local=True,
    )


@pytest.mark.asyncio
async def test_freshness_guard_rejects_external_idle_mutation(tmp_path: Path) -> None:
    auditor = _make_guarded_auditor(tmp_path)
    await auditor._begin_workspace_fingerprint_guard()
    await auditor._end_workspace_fingerprint_guard()

    (tmp_path / "external.txt").write_text("outside\n", encoding="utf-8")

    with pytest.raises(OverlayRunError, match="workspace changed outside"):
        await auditor._begin_workspace_fingerprint_guard()


@pytest.mark.asyncio
async def test_freshness_guard_allows_concurrent_active_window(tmp_path: Path) -> None:
    auditor = _make_guarded_auditor(tmp_path)
    await auditor._begin_workspace_fingerprint_guard()
    await auditor._end_workspace_fingerprint_guard()

    await auditor._begin_workspace_fingerprint_guard()
    (tmp_path / "during-active.txt").write_text("ok\n", encoding="utf-8")
    await auditor._begin_workspace_fingerprint_guard()
    await auditor._end_workspace_fingerprint_guard()
    await auditor._end_workspace_fingerprint_guard()


# ---------------------------------------------------------------------------
# OverlayAuditor full-trip with a scripted fake exec transport.
# ---------------------------------------------------------------------------


class _ScriptedSandbox:
    """Fake sandbox: intercepts only the ``unshare -Urm`` step.

    The orchestrator issues these commands in order:
      1. Overlay runtime upload → writes the script/package for real.
      2. ``unshare -Urm ... overlay_run.py`` → intercepted. Darwin has no
         unshare/overlayfs, so we pretend to run the user command, write
         ``diff.ndjson`` into the lease's run dir, and return the scripted
         user exit code.
      3. ``cat diff.ndjson`` → runs for real against the run dir we just
         populated.
      4. ``rm -rf run_dir`` → runs for real.

    Darwin ``bash`` supports the subset of features the auditor wraps
    commands in (``pipefail``, ``-lc``), so steps 1/3/4 execute in the
    host shell identically to how they would inside a real sandbox.
    """

    def __init__(
        self,
        *,
        repo_root: Path,
        diff_contents: str,
        user_exit: int,
        stdout_contents: str = "",
    ) -> None:
        self._repo_root = repo_root
        self._diff_contents = diff_contents
        self._user_exit = user_exit
        self._stdout_contents = stdout_contents
        self.commands: list[str] = []
        self._run_dir: str | None = None

    async def exec(self, command: str, timeout: int | None = None):
        import asyncio
        from types import SimpleNamespace

        self.commands.append(command)

        # Step 3: intercept the unshare invocation so we never try to run
        # unshare/overlayfs on darwin. ``--run-dir`` sits inside the
        # quoted inner command, so pull it out with a regex rather than
        # shell-tokenizing.
        if "unshare -Urm" in command:
            import re

            match = re.search(r"--run-dir\s+(\S+)", command)
            if match is None:
                return SimpleNamespace(result="missing run-dir", exit_code=1)
            run_dir = match.group(1)
            Path(run_dir).mkdir(parents=True, exist_ok=True)
            Path(run_dir, "diff.ndjson").write_text(
                self._diff_contents, encoding="utf-8"
            )
            Path(run_dir, "stdout.bin").write_text(
                self._stdout_contents, encoding="utf-8"
            )
            self._run_dir = run_dir
            return SimpleNamespace(result="", exit_code=self._user_exit)

        # Every other command is safe to run on the host shell.
        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return SimpleNamespace(
            result=completed.stdout + completed.stderr,
            exit_code=completed.returncode,
        )


class _StreamingScriptedSandbox(_ScriptedSandbox):
    def __init__(
        self,
        *,
        repo_root: Path,
        diff_contents: str,
        user_exit: int,
        first_progress_seen,
    ) -> None:
        super().__init__(
            repo_root=repo_root,
            diff_contents=diff_contents,
            user_exit=user_exit,
        )
        self._first_progress_seen = first_progress_seen

    async def exec(self, command: str, timeout: int | None = None):
        if "unshare -Urm" not in command:
            return await super().exec(command, timeout=timeout)

        import asyncio
        import re

        match = re.search(r"--run-dir\s+(\S+)", command)
        if match is None:
            return SimpleNamespace(result="missing run-dir", exit_code=1)
        run_dir = match.group(1)
        Path(run_dir).mkdir(parents=True, exist_ok=True)
        stdout_path = Path(run_dir, "stdout.bin")
        stdout_path.write_text("first\n", encoding="utf-8")
        try:
            await asyncio.wait_for(self._first_progress_seen.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
        stdout_path.write_text("first\nsecond\n", encoding="utf-8")
        Path(run_dir, "diff.ndjson").write_text(
            self._diff_contents, encoding="utf-8"
        )
        self._run_dir = run_dir
        return SimpleNamespace(result="", exit_code=self._user_exit)


def _init_fixture_repo(path: Path) -> None:

    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"], check=True
    )
    subprocess.run(
        ["git", "-C", str(path), "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
    )


def _commit_all(path: Path) -> None:

    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "-m", "seed"], check=True
    )


async def _noop_exec(sandbox, command, *, timeout=None):
    return await sandbox.exec(command, timeout=timeout)
