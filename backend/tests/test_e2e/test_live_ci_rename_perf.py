"""Live Daytona timing + correctness test for `ci_rename_symbol`.

Measures end-to-end latency of the LSP-backed rename tool when jedi runs
inside a real Daytona sandbox via ``process.exec``. The upper bounds are
loose because sandbox latency varies; the test's value is the printed
timing numbers plus a correctness assertion that the rename actually
rewrites every call/import site.

Run with:
    .venv/bin/python -m pytest backend/tests/test_e2e/test_live_ci_rename_perf.py \
        -m live -v -s
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from dotenv import load_dotenv

from code_intelligence.routing.service import CodeIntelligenceService
from tools.ci_toolkit.rename_tool import ci_rename_symbol
from tools.core.base import ToolExecutionContext
from tools.daytona_toolkit._daytona_utils import _extract_exit_code, _wrap_bash_command

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_PROJECT_ROOT / ".env")


def _load_settings() -> dict[str, Any]:
    settings_path = Path.home() / ".ephemeralos" / "settings.json"
    if settings_path.exists():
        return json.loads(settings_path.read_text())
    return {}


_SETTINGS = _load_settings()
HAS_DAYTONA = bool(
    (os.environ.get("DAYTONA_API_KEY") or _SETTINGS.get("daytona_api_key", ""))
    and (os.environ.get("DAYTONA_API_URL") or _SETTINGS.get("daytona_api_url", ""))
)

pytestmark = [pytest.mark.e2e, pytest.mark.live]

_TERM_NOISE = re.compile(r"\x1b\[3J.*$", re.S)


class _AsyncFs:
    def __init__(self, real_fs: Any):
        self._real = real_fs

    async def upload_file(self, *args, **kwargs):
        return self._real.upload_file(*args, **kwargs)

    async def download_file(self, *args, **kwargs):
        return self._real.download_file(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _AsyncProcess:
    def __init__(self, real_process: Any):
        self._real = real_process

    async def exec(self, *args, **kwargs):
        response = self._real.exec(*args, **kwargs)
        stdout = _TERM_NOISE.sub("", getattr(response, "result", "") or "")
        return SimpleNamespace(result=stdout, exit_code=getattr(response, "exit_code", None))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _AsyncSandboxWrapper:
    def __init__(self, raw_sandbox: Any):
        self._raw = raw_sandbox
        self.fs = _AsyncFs(raw_sandbox.fs)
        self.process = _AsyncProcess(raw_sandbox.process)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


@dataclass
class LiveRenameEnv:
    sandbox_id: str
    raw_sandbox: Any
    async_sandbox: Any
    home: str
    root_dir: str

    def exec(self, command: str, *, timeout: int = 180) -> tuple[int, str]:
        response = self.raw_sandbox.process.exec(
            _wrap_bash_command(command), timeout=timeout
        )
        raw = _TERM_NOISE.sub("", getattr(response, "result", "") or "")
        cleaned, exit_code = _extract_exit_code(
            raw, fallback_exit_code=getattr(response, "exit_code", None),
        )
        return exit_code, cleaned

    def exec_checked(self, command: str, *, timeout: int = 180) -> str:
        exit_code, stdout = self.exec(command, timeout=timeout)
        if exit_code != 0:
            detail = stdout.strip() or f"exit {exit_code}"
            raise AssertionError(f"Sandbox command failed: {detail}")
        return stdout

    def write_file(self, path: str, content: str) -> None:
        # Heredoc-safe write: base64 the content to avoid quoting issues.
        import base64

        payload = base64.b64encode(content.encode("utf-8")).decode("ascii")
        self.exec_checked(
            f"mkdir -p {shlex.quote(os.path.dirname(path) or '.')} && "
            f"echo {shlex.quote(payload)} | base64 -d > {shlex.quote(path)}"
        )

    def read_file(self, path: str) -> str:
        return self.exec_checked(f"cat {shlex.quote(path)}")


@pytest.fixture
def live_rename_env():
    if not HAS_DAYTONA:
        pytest.skip("Daytona credentials not configured")

    from sandbox.testing import create_test_sandbox, delete_test_sandbox, get_sandbox_service

    info = create_test_sandbox(name="rename-perf-live")
    sandbox_id = info["id"]
    try:
        sandbox_svc = get_sandbox_service()
        raw_sandbox = sandbox_svc.get_sandbox_object(sandbox_id)
        home_resp = raw_sandbox.process.exec("pwd", timeout=10)
        home = (getattr(home_resp, "result", "") or "").strip() or "/home/daytona"
        env = LiveRenameEnv(
            sandbox_id=sandbox_id,
            raw_sandbox=raw_sandbox,
            async_sandbox=_AsyncSandboxWrapper(raw_sandbox),
            home=home,
            root_dir=f"{home}/ci_rename_perf_{uuid.uuid4().hex[:8]}",
        )
        env.exec_checked(f"mkdir -p {shlex.quote(env.root_dir)}")
        yield env
    finally:
        delete_test_sandbox(sandbox_id)


def test_live_ci_rename_timing_and_correctness(live_rename_env: LiveRenameEnv):
    """Measure dry-run + commit rename timing on a 3-file project."""
    env = live_rename_env
    pkg = f"{env.root_dir}/pkg"
    a_path = f"{pkg}/a.py"
    b_path = f"{pkg}/b.py"
    c_path = f"{pkg}/c.py"

    env.write_file(f"{pkg}/__init__.py", "")
    env.write_file(a_path, "def foo(x):\n    return x + 1\n")
    env.write_file(
        b_path, "from pkg.a import foo\n\ndef run():\n    return foo(10)\n",
    )
    env.write_file(
        c_path,
        "from pkg.a import foo\n\ndef call_twice():\n    return foo(1) + foo(2)\n",
    )

    svc = CodeIntelligenceService(
        sandbox_id=env.sandbox_id,
        workspace_root=env.root_dir,
        sandbox=env.raw_sandbox,
    )
    # Ensure jedi is installed in the sandbox image.
    t_init = time.perf_counter()
    svc.ensure_initialized(wait=True)
    svc.lsp_client.ensure_ready(install_missing=True)
    init_dt = time.perf_counter() - t_init

    ctx = ToolExecutionContext(
        cwd=Path(env.root_dir),
        metadata={
            "daytona_sandbox": env.async_sandbox,
            "daytona_cwd": env.root_dir,
            "repo_root": env.root_dir,
            "ci_service": svc,
            "agent_run_id": f"rename-live-{uuid.uuid4().hex[:8]}",
        },
    )

    # --- Dry run ---
    t0 = time.perf_counter()
    dry = asyncio.run(
        ci_rename_symbol.execute(
            ci_rename_symbol.input_model(
                file_path=a_path, line=1, character=0, new_name="bar", dry_run=True,
            ),
            ctx,
        )
    )
    dry_dt = time.perf_counter() - t0
    assert not dry.is_error, dry.output
    dry_data = json.loads(dry.output)
    assert dry_data["status"] == "dry_run"
    dry_paths = {f["file_path"] for f in dry_data["files"]}

    # --- Commit ---
    t1 = time.perf_counter()
    commit = asyncio.run(
        ci_rename_symbol.execute(
            ci_rename_symbol.input_model(
                file_path=a_path, line=1, character=0, new_name="bar",
            ),
            ctx,
        )
    )
    commit_dt = time.perf_counter() - t1
    assert not commit.is_error, commit.output
    commit_data = json.loads(commit.output)
    assert commit_data["status"] == "renamed"
    assert all(f["status"] == "renamed" for f in commit_data["files"])
    renamed_paths = {f["file_path"] for f in commit_data["files"]}

    # Correctness: all three files now use `bar`, none mention `foo`.
    a_new = env.read_file(a_path)
    b_new = env.read_file(b_path)
    c_new = env.read_file(c_path)
    assert "def bar" in a_new and "def foo" not in a_new
    assert "from pkg.a import bar" in b_new and "foo" not in b_new
    assert "from pkg.a import bar" in c_new and "foo" not in c_new

    # Both dry-run and commit should have touched the same 3 files.
    assert renamed_paths == dry_paths
    assert len(renamed_paths) == 3

    print(
        "\n[ci_rename_symbol live timing] "
        f"ensure_initialized={init_dt:.2f}s "
        f"dry_run={dry_dt:.2f}s "
        f"commit={commit_dt:.2f}s "
        f"files={len(renamed_paths)}"
    )

    # Loose upper bounds — generous to accommodate Daytona latency swings.
    assert dry_dt < 15.0, f"dry-run rename took {dry_dt:.2f}s"
    assert commit_dt < 20.0, f"commit rename took {commit_dt:.2f}s"
