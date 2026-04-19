"""Tests for ``CodeIntelligenceService.cmd`` fail-closed semantics.

``svc.cmd`` uses Git workspace auditing. It must fail closed if the
workspace cannot be represented as a Git diff and must not fall back to
unaudited process execution.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess
from types import SimpleNamespace

import pytest
from code_intelligence.routing.git_workspace_types import GitWorkspacePrepareError
from code_intelligence.routing.service import (
    CodeIntelligenceService,
    dispose_all_code_intelligence,
)
from tools.daytona_toolkit._daytona_utils import _wrap_bash_command


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    dispose_all_code_intelligence()
    yield
    dispose_all_code_intelligence()


@pytest.mark.asyncio
async def test_cmd_raises_when_git_workspace_prepare_fails(tmp_path) -> None:
    sandbox = SimpleNamespace(process=_AsyncLocalProcess())
    svc = CodeIntelligenceService(
        sandbox_id=f"sandbox-cmd-git-fail-{tmp_path.name}",
        workspace_root=str(tmp_path),
        sandbox=sandbox,
    )

    with pytest.raises(GitWorkspacePrepareError) as excinfo:
        await svc.cmd(sandbox, _wrap_bash_command("echo hi"))

    assert "workspace root" in str(excinfo.value) or "git" in str(excinfo.value)


@pytest.mark.asyncio
async def test_cmd_uses_git_workspace_auditor(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "seed"], check=True)

    sandbox = SimpleNamespace(process=_AsyncLocalProcess())
    svc = CodeIntelligenceService(
        sandbox_id=f"sandbox-cmd-git-{tmp_path.name}",
        workspace_root=str(repo),
        sandbox=sandbox,
    )

    result = await svc.cmd(
        sandbox,
        _wrap_bash_command(f"cd {shlex.quote(str(repo))} && echo hi"),
        timeout=60,
    )

    assert result.exit_code == 0
    assert result.changed_paths == []
    assert result.result
    assert svc._git_workspace_auditor is not None  # type: ignore[attr-defined]


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
