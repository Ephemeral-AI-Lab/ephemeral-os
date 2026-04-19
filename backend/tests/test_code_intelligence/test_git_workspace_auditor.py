"""Tests for Git workspace CodeAct auditing."""

from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace

import pytest

from code_intelligence.hashing import content_hash
from code_intelligence.routing.git_diff_committer import GitDiffCommitter
from code_intelligence.routing.git_workspace_auditor import GitWorkspaceAuditor
from code_intelligence.routing.service import (
    CodeIntelligenceService,
    dispose_all_code_intelligence,
)
from code_intelligence.routing.git_workspace_types import WorkspaceDiff, WorkspaceDiffFile
from tools.core.base import ToolExecutionContext
from tools.daytona_toolkit._daytona_utils import _wrap_bash_command
from tools.daytona_toolkit.codeact_tool import daytona_codeact


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    dispose_all_code_intelligence()
    yield
    dispose_all_code_intelligence()


class _AsyncLocalProcess:
    async def exec(self, command: str, timeout: int | None = None):
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


def _init_repo(path) -> None:
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test User"], check=True)


def _commit_all(path, message: str = "seed") -> None:
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", message], check=True)


def _workspace_diff(root: str, rel_path: str, base: str, final: str) -> WorkspaceDiff:
    return WorkspaceDiff(
        files=(
            WorkspaceDiffFile(
                path=rel_path,
                old_path=None,
                status="modify",
                base_existed=True,
                base_hash=content_hash(base),
                final_existed=True,
                final_hash=content_hash(final),
                base_content=base,
                final_content=final,
            ),
        ),
        baseline_commit="baseline",
        workspace_root=root,
        command_exit_code=0,
        stdout="",
    )


def test_git_workspace_command_mapping_uses_daytona_compatible_env_prefix() -> None:
    auditor = GitWorkspaceAuditor(
        workspace_root="/repo",
        exec_process=None,  # type: ignore[arg-type]
        pool=None,  # type: ignore[arg-type]
        committer=None,  # type: ignore[arg-type]
    )

    mapped = auditor._map_command_to_slot(  # type: ignore[attr-defined]
        _wrap_bash_command("cd /repo && true"),
        "/tmp/eos-codeact-git/sandbox/slot-000",
    )

    assert mapped.startswith("env -u LC_ALL EOS_CODEACT_WORKSPACE_ROOT=")
    assert not mapped.startswith("EOS_CODEACT_WORKSPACE_ROOT=")
    assert "/tmp/eos-codeact-git/sandbox/slot-000" in mapped
    assert "cd /tmp/eos-codeact-git/sandbox/slot-000 && true" in mapped


@pytest.mark.asyncio
async def test_git_diff_committer_uses_strict_base_occ_batch(tmp_path) -> None:
    target = tmp_path / "app.py"
    target.write_text("old\n", encoding="utf-8")
    svc = CodeIntelligenceService(
        sandbox_id=f"git-committer-{tmp_path.name}",
        workspace_root=str(tmp_path),
    )

    diff = _workspace_diff(str(tmp_path), "app.py", "old\n", "new\n")

    result = await GitDiffCommitter(svc._write_coordinator).commit(  # type: ignore[attr-defined]
        diff,
        agent_id="alice",
        description="test git workspace",
    )

    assert result.success is True
    assert result.status == "committed"
    assert target.read_text(encoding="utf-8") == "new\n"


@pytest.mark.asyncio
async def test_git_diff_committer_aborts_when_live_hash_changed(tmp_path) -> None:
    target = tmp_path / "app.py"
    target.write_text("old\n", encoding="utf-8")
    svc = CodeIntelligenceService(
        sandbox_id=f"git-committer-conflict-{tmp_path.name}",
        workspace_root=str(tmp_path),
    )
    diff = _workspace_diff(str(tmp_path), "app.py", "old\n", "new\n")

    target.write_text("peer\n", encoding="utf-8")

    result = await GitDiffCommitter(svc._write_coordinator).commit(diff)  # type: ignore[attr-defined]

    assert result.success is False
    assert result.status == "aborted_version"
    assert target.read_text(encoding="utf-8") == "peer\n"


@pytest.mark.asyncio
async def test_service_cmd_commits_git_workspace_diff_through_occ(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CI_CODEACT_GIT_POOL_SIZE_PER_SANDBOX", "2")
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    target = repo / "app.py"
    target.write_text("old\n", encoding="utf-8")
    _commit_all(repo)

    sandbox = SimpleNamespace(process=_AsyncLocalProcess())
    svc = CodeIntelligenceService(
        sandbox_id=f"git-service-{tmp_path.name}",
        workspace_root=str(repo),
        sandbox=sandbox,
    )

    command = _wrap_bash_command(
        "cd "
        + subprocess.list2cmdline([str(repo)])
        + " && python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "Path('app.py').write_text('new\\n', encoding='utf-8')\n"
        "PY"
    )
    result = await svc.cmd(sandbox, command, timeout=60, agent_id="alice")

    assert result.exit_code == 0
    assert result.changed_paths == [str(target)]
    assert result.git_commit_status == "committed"
    assert target.read_text(encoding="utf-8") == "new\n"
    assert svc._git_workspace_pool is not None  # type: ignore[attr-defined]
    assert svc._git_workspace_pool.pool_size == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_service_cmd_reports_ambient_paths_without_occ_commit(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CI_CODEACT_GIT_POOL_SIZE_PER_SANDBOX", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    target = repo / "app.py"
    target.write_text("old\n", encoding="utf-8")
    _commit_all(repo)

    sandbox = SimpleNamespace(process=_AsyncLocalProcess())
    svc = CodeIntelligenceService(
        sandbox_id=f"git-service-ambient-{tmp_path.name}",
        workspace_root=str(repo),
        sandbox=sandbox,
    )

    command = _wrap_bash_command(
        f"cd {str(repo)!r} && python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "Path('app.py').write_text('new\\n', encoding='utf-8')\n"
        "PY"
    )
    result = await svc.cmd(sandbox, command, timeout=60, attribute_changes=False)

    assert result.changed_paths == []
    assert result.ambient_changed_paths == [str(target)]
    assert target.read_text(encoding="utf-8") == "old\n"


@pytest.mark.asyncio
async def test_daytona_codeact_shell_uses_git_workspace_auditor(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CI_CODEACT_GIT_POOL_SIZE_PER_SANDBOX", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    target = repo / "app.py"
    target.write_text("old\n", encoding="utf-8")
    _commit_all(repo)

    sandbox = SimpleNamespace(process=_AsyncLocalProcess())
    svc = CodeIntelligenceService(
        sandbox_id=f"git-codeact-{tmp_path.name}",
        workspace_root=str(repo),
        sandbox=sandbox,
    )
    ctx = ToolExecutionContext(
        cwd=repo,
        metadata={
            "daytona_sandbox": sandbox,
            "ci_sandbox": sandbox,
            "daytona_cwd": str(repo),
            "repo_root": str(repo),
            "exec_cwd": str(repo),
            "ci_service": svc,
            "agent_run_id": "run-1",
        },
    )

    result = await daytona_codeact.execute(
        daytona_codeact.input_model(
            mode="shell",
            command=(
                "python3 - <<'PY'\n"
                "from pathlib import Path\n"
                "Path('app.py').write_text('via tool\\n', encoding='utf-8')\n"
                "PY"
            ),
            timeout=60,
        ),
        ctx,
    )

    assert not result.is_error, result.output
    assert target.read_text(encoding="utf-8") == "via tool\n"
    assert result.metadata["changed_paths"] == [str(target)]
