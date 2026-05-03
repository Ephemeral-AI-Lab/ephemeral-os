"""Execution-path unit tests for OverlayAuditor."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from sandbox.code_intelligence.overlay import auditor as overlay_auditor_module
from sandbox.code_intelligence.overlay import process_exec as overlay_process_exec_module
from sandbox.code_intelligence.overlay.command_executor import AuditedCommandExecutor
from sandbox.code_intelligence.service import CodeIntelligenceService
from sandbox.code_intelligence.registry import dispose_all_code_intelligence


def _make_executor(svc: CodeIntelligenceService, sandbox_id: str, workspace_root: str) -> AuditedCommandExecutor:
    """Build an AuditedCommandExecutor wired to the service's WriteCoordinator.

    Slice 5a moves OCC commit out of overlay; tests that exercise the
    overlay→OCC integration drive ``executor.cmd`` (which preserves the
    legacy SimpleNamespace contract) instead of ``auditor.execute``
    (which now returns the OCC-free ``OverlayRunOutcome``).
    """
    executor = AuditedCommandExecutor(
        sandbox_id=sandbox_id,
        workspace_root=workspace_root,
        write_coordinator=svc._write_coordinator,
        rebind_sandbox=lambda _sandbox: None,
        transport=None,
        daemon_local=False,
    )

    async def _bridge(_sandbox, command, *, timeout=None):
        return await _sandbox.exec(command, timeout=timeout)

    executor._exec_sandbox_process = _bridge  # type: ignore[assignment]
    return executor


@pytest.fixture(autouse=True)
def _registry():
    dispose_all_code_intelligence()
    yield
    dispose_all_code_intelligence()


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
        import subprocess
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
    import subprocess

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
    import subprocess

    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "-m", "seed"], check=True
    )


def test_overlay_runtime_bundle_contains_executable_facade_and_runtime_package(
    tmp_path: Path,
) -> None:
    raw = overlay_auditor_module._overlay_runtime_bundle_bytes()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        names = set(tar.getnames())
        try:
            tar.extractall(tmp_path, filter="data")
        except TypeError:
            tar.extractall(tmp_path)

    assert "overlay_run.py" in names
    assert "overlay_runtime/__init__.py" in names
    assert "overlay_runtime/runner.py" in names
    assert "overlay_runtime/classifier.py" in names

    proc = subprocess.run(
        [sys.executable, str(tmp_path / "overlay_run.py"), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "overlay_run.py" in proc.stdout


@pytest.mark.asyncio
async def test_auditor_commits_gitinclude_changes_via_occ_when_legacy_attribute_flag_disabled(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_fixture_repo(repo)
    target = repo / "app.py"
    target.write_text("old\n", encoding="utf-8")
    _commit_all(repo)

    diff_payload = "\n".join(
        [
            _meta_line(gitinclude_changes=1, upper_files=1, upper_bytes=4),
            json.dumps(
                {
                    "path": "app.py",
                    "kind": "modify",
                    "base_content": "old\n",
                    "base_existed": True,
                    "final_content": "new\n",
                    "strict_base": True,
                }
            ),
        ]
    )
    sandbox = _ScriptedSandbox(
        repo_root=repo, diff_contents=diff_payload, user_exit=0
    )
    svc = CodeIntelligenceService(
        sandbox_id=f"overlay-auditor-commit-{tmp_path.name}",
        workspace_root=str(repo),
    )
    executor = _make_executor(
        svc,
        f"overlay-auditor-commit-{tmp_path.name}",
        str(repo),
    )

    result = await executor.cmd(
        sandbox,
        "echo hi",
        agent_id="alice",
        timeout=60,
        attribute_changes=False,
    )

    assert result.exit_code == 0
    assert result.git_commit_status == "committed"
    assert result.changed_paths == [str(target)]
    assert result.ambient_changed_paths == []
    assert result.mixed_gitinclude_gitignore is False
    assert result.mixed_partial_apply is False
    assert target.read_text(encoding="utf-8") == "new\n"


@pytest.mark.asyncio
async def test_auditor_returns_user_command_stdout_from_overlay_run_dir(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_fixture_repo(repo)
    (repo / "app.py").write_text("old\n", encoding="utf-8")
    _commit_all(repo)

    sandbox = _ScriptedSandbox(
        repo_root=repo,
        diff_contents=_meta_line(exit_code=0),
        user_exit=0,
        stdout_contents="hello from overlay\n",
    )
    svc = CodeIntelligenceService(
        sandbox_id=f"overlay-stdout-{tmp_path.name}",
        workspace_root=str(repo),
    )
    executor = _make_executor(svc, f"overlay-stdout-{tmp_path.name}", str(repo))

    result = await executor.cmd(sandbox, "echo hello from overlay", timeout=60)

    assert result.result == "hello from overlay\n"


@pytest.mark.asyncio
async def test_auditor_forwards_live_stdout_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    monkeypatch.setattr(
        overlay_process_exec_module,
        "PROGRESS_POLL_INTERVAL_SECONDS",
        0.01,
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_fixture_repo(repo)
    (repo / "app.py").write_text("old\n", encoding="utf-8")
    _commit_all(repo)

    first_progress_seen = asyncio.Event()
    sandbox = _StreamingScriptedSandbox(
        repo_root=repo,
        diff_contents=_meta_line(exit_code=0),
        user_exit=0,
        first_progress_seen=first_progress_seen,
    )
    svc = CodeIntelligenceService(
        sandbox_id=f"overlay-progress-{tmp_path.name}",
        workspace_root=str(repo),
    )
    executor = _make_executor(svc, f"overlay-progress-{tmp_path.name}", str(repo))
    progress: list[str] = []

    def on_progress(line: str) -> None:
        progress.append(line)
        if "first" in line:
            first_progress_seen.set()

    result = await executor.cmd(
        sandbox,
        "echo first && sleep 1 && echo second",
        timeout=60,
        on_progress_line=on_progress,
    )

    assert result.result == "first\nsecond\n"
    assert first_progress_seen.is_set()
    assert any("first" in line for line in progress)


@pytest.mark.asyncio
async def test_auditor_reports_noop_for_gitignore_only_changes(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_fixture_repo(repo)
    (repo / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    (repo / "app.py").write_text("old\n", encoding="utf-8")
    _commit_all(repo)

    (repo / ".venv").mkdir()
    (repo / ".venv" / "cfg").write_text("home=/usr\n", encoding="utf-8")
    sandbox = _ScriptedSandbox(
        repo_root=repo,
        diff_contents=_meta_line(
            exit_code=0,
            gitignore_changes=1,
            gitignore_paths=[".venv/cfg"],
            direct_merged_bytes=10,
        ),
        user_exit=0,
    )
    svc = CodeIntelligenceService(
        sandbox_id=f"overlay-gitignore-only-{tmp_path.name}",
        workspace_root=str(repo),
    )
    executor = _make_executor(
        svc, f"overlay-gitignore-only-{tmp_path.name}", str(repo)
    )

    result = await executor.cmd(sandbox, "python -m venv .venv", timeout=60)

    assert result.git_commit_status == "noop"
    assert result.changed_paths == []
    assert result.gitignore_direct_merged_paths == [str(repo / ".venv" / "cfg")]


@pytest.mark.asyncio
async def test_auditor_surfaces_mixed_partial_apply_on_occ_abort(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_fixture_repo(repo)
    target = repo / "requirements.txt"
    target.write_text("foo==0.1\n", encoding="utf-8")
    _commit_all(repo)

    # The classifier inside the ns would already have direct-merged the
    # gitignore file before OCC runs; we simulate that by writing the
    # file directly to the live workspace and letting the NDJSON declare
    # it as merged.
    (repo / ".venv").mkdir()
    (repo / ".venv" / "bar.cfg").write_text("home=/usr\n", encoding="utf-8")

    # Peer write to the gitinclude file so strict OCC aborts.
    target.write_text("peer-changed\n", encoding="utf-8")

    diff_payload = "\n".join(
        [
            _meta_line(
                gitinclude_changes=1,
                gitignore_changes=1,
                gitignore_paths=[".venv/bar.cfg"],
                upper_files=2,
                direct_merged_bytes=10,
            ),
            json.dumps(
                {
                    "path": "requirements.txt",
                    "kind": "modify",
                    # base_content matches SNAP (pre-peer-write). OCC sees
                    # live != base_content and aborts.
                    "base_content": "foo==0.1\n",
                    "base_existed": True,
                    "final_content": "foo==0.2\n",
                    "strict_base": True,
                }
            ),
        ]
    )
    sandbox = _ScriptedSandbox(
        repo_root=repo, diff_contents=diff_payload, user_exit=0
    )
    svc = CodeIntelligenceService(
        sandbox_id=f"overlay-partial-{tmp_path.name}",
        workspace_root=str(repo),
    )
    executor = _make_executor(svc, f"overlay-partial-{tmp_path.name}", str(repo))

    # Before building SNAP, reset gitinclude file to its committed content
    # so SNAP captures "foo==0.1\n" as base, then apply the peer write
    # so OCC mismatches.
    target.write_text("foo==0.1\n", encoding="utf-8")

    # The scripted sandbox sequences SNAP before the user-cmd
    # intercept — we use that intercept to apply the peer write.
    original_exec = sandbox.exec

    async def _exec_with_peer(command, timeout=None):
        result = await original_exec(command, timeout=timeout)
        # Apply the peer write the first time we see the unshare step.
        if "unshare -Urm" in command and target.read_text(encoding="utf-8") == "foo==0.1\n":
            target.write_text("peer-changed\n", encoding="utf-8")
        return result

    sandbox.exec = _exec_with_peer  # type: ignore[assignment]

    result = await executor.cmd(sandbox, "pip install foo && echo foo >> requirements.txt", timeout=60)

    assert result.mixed_gitinclude_gitignore is True
    assert result.mixed_partial_apply is True
    assert result.git_commit_status == "aborted_version"
    assert result.changed_paths == []
    # Tracked live path appears as ambient (the user tried to change it).
    assert str(target) in result.ambient_changed_paths
    # Gitignored direct-merged path surfaces in the additive metadata.
    assert str(repo / ".venv" / "bar.cfg") in result.gitignore_direct_merged_paths
    assert any("gitinclude changes aborted" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_auditor_surfaces_policy_reject(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_fixture_repo(repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _commit_all(repo)

    reject_payload = json.dumps(
        {
            "_reject": {
                "reason": "overlay_rejected_dotgit_writes",
                "paths": [".git/config"],
            }
        }
    )
    sandbox = _ScriptedSandbox(
        repo_root=repo, diff_contents=reject_payload, user_exit=201
    )
    svc = CodeIntelligenceService(
        sandbox_id=f"overlay-reject-{tmp_path.name}",
        workspace_root=str(repo),
    )
    executor = _make_executor(svc, f"overlay-reject-{tmp_path.name}", str(repo))

    result = await executor.cmd(sandbox, "echo .git/hack", timeout=30)

    assert result.git_commit_status == "rejected"
    assert result.git_conflict_reason
    assert "overlay_rejected_dotgit_writes" in result.git_conflict_reason
    assert result.changed_paths == []
    assert result.ambient_changed_paths == []
