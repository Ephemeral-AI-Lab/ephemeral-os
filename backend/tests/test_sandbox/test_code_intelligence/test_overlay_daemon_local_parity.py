"""Parity tests for the daemon-local overlay auditor branch."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sandbox.code_intelligence.overlay import auditor as overlay_auditor_module
from sandbox.code_intelligence.overlay.command_executor import AuditedCommandExecutor
from sandbox.code_intelligence.registry import dispose_all_code_intelligence
from sandbox.code_intelligence.service import CodeIntelligenceService


def _make_executor(
    svc: CodeIntelligenceService,
    sandbox_id: str,
    workspace_root: str,
    *,
    daemon_local: bool,
    bridge,
) -> AuditedCommandExecutor:
    executor = AuditedCommandExecutor(
        sandbox_id=sandbox_id,
        workspace_root=workspace_root,
        write_coordinator=svc._write_coordinator,
        rebind_sandbox=lambda _sandbox: None,
        transport=None,
        daemon_local=daemon_local,
    )
    executor._exec_sandbox_process = bridge  # type: ignore[assignment]
    return executor


@pytest.fixture(autouse=True)
def _registry() -> None:
    dispose_all_code_intelligence()
    yield
    dispose_all_code_intelligence()


def _meta_line(**overrides: Any) -> str:
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
        "run_timings": {"total": 0.6, "classify": 0.07},
        "warnings": [],
    }
    base.update(overrides)
    return json.dumps({"_meta": base}, separators=(",", ":"))


def _reject_line() -> str:
    return json.dumps(
        {
            "_reject": {
                "reason": "overlay_rejected_dotgit_writes",
                "paths": [".git/config"],
                "run_timings": {"total": 0.6, "classify": 0.07},
            }
        },
        separators=(",", ":"),
    )


class _ScriptedSandbox:
    def __init__(self, *, diff_contents: str, user_exit: int, stdout: str) -> None:
        self._diff_contents = diff_contents
        self._user_exit = user_exit
        self._stdout = stdout

    async def exec(self, command: str, timeout: int | None = None) -> SimpleNamespace:
        del timeout
        if "unshare -Urm" in command:
            match = re.search(r"--run-dir\s+(\S+)", command)
            if match is None:
                return SimpleNamespace(result="missing run-dir", exit_code=1)
            run_dir = Path(match.group(1))
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "diff.ndjson").write_text(
                self._diff_contents, encoding="utf-8"
            )
            (run_dir / "stdout.bin").write_text(self._stdout, encoding="utf-8")
            return SimpleNamespace(result="", exit_code=self._user_exit)
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


async def _noop_exec(sandbox: _ScriptedSandbox, command: str, *, timeout=None):
    return await sandbox.exec(command, timeout=timeout)


async def _should_not_exec(_sandbox: Any, _command: str, *, timeout=None) -> None:
    del timeout
    raise AssertionError("daemon-local overlay branch should not call _do_exec")


def _init_repo(path: Path) -> None:
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
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "seed"], check=True)


def _make_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _init_repo(repo)
    (repo / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    (repo / "app.py").write_text("old\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("foo==0.1\n", encoding="utf-8")
    _commit_all(repo)
    return repo


def _case_payload(case: str) -> tuple[str, int, str]:
    if case == "gitinclude":
        return (
            "\n".join(
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
                        },
                        separators=(",", ":"),
                    ),
                ]
            ),
            0,
            "gitinclude stdout\n",
        )
    if case == "gitignore":
        return (
            _meta_line(
                exit_code=0,
                gitignore_changes=1,
                gitignore_paths=[".venv/cfg"],
                direct_merged_bytes=10,
            ),
            0,
            "gitignore stdout\n",
        )
    if case == "mixed":
        return (
            "\n".join(
                [
                    _meta_line(
                        gitinclude_changes=1,
                        gitignore_changes=1,
                        gitignore_paths=[".venv/cfg"],
                        upper_files=2,
                        direct_merged_bytes=10,
                    ),
                    json.dumps(
                        {
                            "path": "app.py",
                            "kind": "modify",
                            "base_content": "old\n",
                            "base_existed": True,
                            "final_content": "mixed\n",
                            "strict_base": True,
                        },
                        separators=(",", ":"),
                    ),
                ]
            ),
            0,
            "mixed stdout\n",
        )
    if case == "aborted_version":
        return (
            "\n".join(
                [
                    _meta_line(gitinclude_changes=1, upper_files=1, upper_bytes=8),
                    json.dumps(
                        {
                            "path": "requirements.txt",
                            "kind": "modify",
                            "base_content": "foo==0.1\n",
                            "base_existed": True,
                            "final_content": "foo==0.2\n",
                            "strict_base": True,
                        },
                        separators=(",", ":"),
                    ),
                ]
            ),
            0,
            "aborted stdout\n",
        )
    if case == "policy_reject":
        return _reject_line(), 201, "reject stdout\n"
    raise AssertionError(case)


def _prepare_case_repo(repo: Path, case: str) -> None:
    if case in {"gitignore", "mixed"}:
        (repo / ".venv").mkdir()
        (repo / ".venv" / "cfg").write_text("home=/usr\n", encoding="utf-8")
    if case == "aborted_version":
        (repo / "requirements.txt").write_text("peer-changed\n", encoding="utf-8")


def _install_daemon_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    *,
    diff_contents: str,
    user_exit: int,
    stdout: str,
) -> list[list[str]]:
    calls: list[list[str]] = []

    def _fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(list(argv))
        assert argv[:2] == ["unshare", "-Urm"]
        match = re.search(r"--run-dir\s+(\S+)", argv[-1])
        if match is None:
            return subprocess.CompletedProcess(argv, 1, "", "missing run-dir")
        run_dir = Path(match.group(1))
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "stdout.bin").write_text(stdout, encoding="utf-8")
        (run_dir / "diff.ndjson").write_text(diff_contents, encoding="utf-8")
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "exit_code": user_exit,
                    "rejected": (
                        {
                            "reason": "overlay_rejected_dotgit_writes",
                            "paths": [".git/config"],
                        }
                        if user_exit == 201
                        else None
                    ),
                    "run_timings": {"total": 0.6},
                },
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, user_exit, "", "")

    monkeypatch.setattr(overlay_auditor_module.subprocess, "run", _fake_run)
    return calls


def _normalize_paths(value: Any, repo: Path) -> Any:
    if isinstance(value, str):
        return value.replace(str(repo), "<repo>")
    if isinstance(value, list):
        return [_normalize_paths(item, repo) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_paths(item, repo) for key, item in value.items()}
    return value


def _normalized_result(result: SimpleNamespace, repo: Path) -> dict[str, Any]:
    payload = dict(vars(result))
    payload = _normalize_paths(payload, repo)
    payload["overlay_run_timings"] = sorted(payload["overlay_run_timings"])
    payload.pop("overlay_stage_timings", None)
    return payload


async def _run_multistage(
    repo: Path,
    *,
    diff_contents: str,
    user_exit: int,
    stdout: str,
    case: str,
) -> SimpleNamespace:
    _prepare_case_repo(repo, case)
    svc = CodeIntelligenceService(
        sandbox_id=f"phase6-multi-{repo.name}",
        workspace_root=str(repo),
    )
    executor = _make_executor(
        svc,
        f"phase6-multi-{repo.name}",
        str(repo),
        daemon_local=False,
        bridge=_noop_exec,
    )
    sandbox = _ScriptedSandbox(
        diff_contents=diff_contents,
        user_exit=user_exit,
        stdout=stdout,
    )
    return await executor.cmd(sandbox, "echo phase6", timeout=60)


async def _run_daemon_local(
    repo: Path,
    *,
    diff_contents: str,
    user_exit: int,
    stdout: str,
    case: str,
) -> SimpleNamespace:
    _prepare_case_repo(repo, case)
    svc = CodeIntelligenceService(
        sandbox_id=f"phase6-daemon-{repo.name}",
        workspace_root=str(repo),
        daemon_local=True,
    )
    executor = _make_executor(
        svc,
        f"phase6-daemon-{repo.name}",
        str(repo),
        daemon_local=True,
        bridge=_should_not_exec,
    )
    return await executor.cmd(None, "echo phase6", timeout=60)


@pytest.mark.parametrize(
    "case,expected_status",
    [
        ("gitinclude", "committed"),
        ("gitignore", "noop"),
        ("mixed", "committed"),
        ("aborted_version", "aborted_version"),
        ("policy_reject", "rejected"),
    ],
)
@pytest.mark.asyncio
async def test_daemon_local_branch_matches_multistage_result_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_status: str,
) -> None:
    diff_contents, user_exit, stdout = _case_payload(case)
    multi_repo = _make_repo(tmp_path, f"{case}-multi")
    daemon_repo = _make_repo(tmp_path, f"{case}-daemon")

    multistage = await _run_multistage(
        multi_repo,
        diff_contents=diff_contents,
        user_exit=user_exit,
        stdout=stdout,
        case=case,
    )
    calls = _install_daemon_subprocess(
        monkeypatch,
        diff_contents=diff_contents,
        user_exit=user_exit,
        stdout=stdout,
    )
    daemon_local = await _run_daemon_local(
        daemon_repo,
        diff_contents=diff_contents,
        user_exit=user_exit,
        stdout=stdout,
        case=case,
    )

    assert len(calls) == 1
    assert "--snap" not in calls[0][-1]
    assert daemon_local.git_commit_status == expected_status
    assert _normalized_result(daemon_local, daemon_repo) == _normalized_result(
        multistage,
        multi_repo,
    )
    assert "unshare" in daemon_local.overlay_stage_timings
    assert "git_snapshot" not in daemon_local.overlay_stage_timings
