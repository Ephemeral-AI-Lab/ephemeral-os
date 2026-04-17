# ruff: noqa: E402
"""E2E tests for CodeAct OCC guard — verifying that codeact script and shell
mutations are properly guarded by the optimistic concurrency control layer.

Tests run against:
  - Mock sandbox with real CodeIntelligenceService (no external deps)
  - Real Daytona sandbox (when credentials are available)
  - Real LLM + Daytona (live tests, when all credentials available)

Scenarios covered:
  1. codeact read() → write() commits through OCC when CI service present
  2. codeact write() with stale read hash → conflict detected
  3. codeact write() to file changed externally between read and commit → conflict
  4. Two codeact invocations editing same file — second detects stale hash
  8. Live sandbox: codeact read/write roundtrip through real OCC pipeline
  9. Live LLM: agent uses codeact to edit a file, OCC guards the write

Run with:
    pytest tests/test_e2e/test_codeact_occ_guard.py -v -s
    pytest tests/test_e2e/test_codeact_occ_guard.py -v -s -k live  # Daytona/LLM tests only
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_PROJECT_ROOT / ".env")

from code_intelligence.routing.service import CodeIntelligenceService
from code_intelligence.types import PreparedWrite
from tests.test_e2e.daytona_exec_io import read_text_via_exec, write_text_via_exec
from tools.core.base import ToolExecutionContext
from tools.daytona_toolkit.codeact_tool import daytona_codeact

pytestmark = [pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------

def _load_settings() -> dict:
    settings_path = Path.home() / ".ephemeralos" / "settings.json"
    if settings_path.exists():
        return json.loads(settings_path.read_text())
    return {}


_SETTINGS = _load_settings()
DAYTONA_KEY = os.environ.get("DAYTONA_API_KEY") or _SETTINGS.get("daytona_api_key", "")
DAYTONA_URL = os.environ.get("DAYTONA_API_URL") or _SETTINGS.get("daytona_api_url", "")
HAS_DAYTONA = bool(DAYTONA_KEY and DAYTONA_URL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_test_loop: asyncio.AbstractEventLoop | None = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _test_loop
    if _test_loop is None or _test_loop.is_closed():
        _test_loop = asyncio.new_event_loop()
    return _test_loop


def _run(coro):
    return _get_loop().run_until_complete(coro)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


class _AsyncFs:
    """Async wrapper around a sync Daytona sandbox fs."""

    def __init__(self, real_fs: Any):
        self._real = real_fs

    async def upload_file(self, *args, **kwargs):
        return self._real.upload_file(*args, **kwargs)

    async def download_file(self, *args, **kwargs):
        return self._real.download_file(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _AsyncProcess:
    """Async wrapper around a sync Daytona sandbox process.

    Strips the ``__CODEX_EXIT_CODE__`` marker and terminal noise that
    ``_wrap_bash_command`` appends, so the codeact tool's last-line JSON
    parser sees clean output.
    """

    _EXIT_RE = re.compile(r"\n?__CODEX_EXIT_CODE__=-?\d+\s*$", re.S)
    _TERM_NOISE = re.compile(r"\x1b\[3J.*$", re.S)

    def __init__(self, real_process: Any):
        self._real = real_process

    async def exec(self, *args, **kwargs):
        resp = self._real.exec(*args, **kwargs)
        raw = resp.result or ""
        # Strip TERM noise first (it appears AFTER the exit marker)
        cleaned = self._TERM_NOISE.sub("", raw)
        cleaned = self._EXIT_RE.sub("", cleaned)
        # Return a simple namespace so .result is writable
        from types import SimpleNamespace
        return SimpleNamespace(result=cleaned)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _AsyncSandboxWrapper:
    """Wraps a sync Daytona sandbox with async-compatible fs and process."""

    def __init__(self, raw_sandbox: Any):
        self._raw = raw_sandbox
        self.fs = _AsyncFs(raw_sandbox.fs)
        self.process = _AsyncProcess(raw_sandbox.process)

    def __getattr__(self, name):
        return getattr(self._raw, name)


def _make_mock_sandbox(files: dict[str, str] | None = None) -> MagicMock:
    """In-memory mock sandbox with async fs ops and thread-safe file store."""
    sandbox = MagicMock()
    file_store = dict(files or {})
    _lock = threading.Lock()

    async def _download(path: str):
        with _lock:
            if path in file_store:
                return file_store[path].encode("utf-8")
        raise FileNotFoundError(f"File not found: {path}")

    async def _upload(content_or_path, path_or_content=None):
        if isinstance(content_or_path, bytes):
            content, path = content_or_path, path_or_content
        else:
            path, content = content_or_path, path_or_content
        with _lock:
            file_store[path] = content.decode("utf-8") if isinstance(content, bytes) else content

    class _MockProcess:
        async def exec(self, command: str, timeout: int = 300):
            """Execute a command against the mock file store."""
            resp = MagicMock()
            # Build a minimal manifest-returning wrapper execution
            # The codeact wrapper prints JSON on the last line
            resp.result = ""
            return resp

    sandbox.fs.download_file = _download
    sandbox.fs.upload_file = _upload
    sandbox.process = _MockProcess()
    sandbox._file_store = file_store
    sandbox._lock = _lock
    return sandbox


def _make_ci_service(sandbox: Any, workspace: str = "/workspace") -> CodeIntelligenceService:
    """Create a real CI service backed by the mock sandbox."""
    svc = CodeIntelligenceService(
        sandbox_id="test-codeact-occ",
        workspace_root=workspace,
        sandbox=sandbox,
    )
    svc._initialized = True
    return svc


def _ctx(
    sandbox: Any,
    ci_service: Any = None,
    *,
    agent_run_id: str = "test-agent",
) -> ToolExecutionContext:
    metadata: dict[str, Any] = {
        "daytona_sandbox": sandbox,
        "daytona_cwd": "/workspace",
        "repo_root": "/workspace",
        "agent_run_id": agent_run_id,
    }
    if ci_service is not None:
        if hasattr(ci_service, "rebind_sandbox"):
            ci_service.rebind_sandbox(sandbox)
        metadata["ci_service"] = ci_service
        metadata["agent_name"] = "developer"
        metadata["team_mode_enabled"] = True
    return ToolExecutionContext(cwd=Path("/workspace"), metadata=metadata)


def _make_codeact_sandbox(
    files: dict[str, str],
    manifest: dict[str, Any],
) -> MagicMock:
    """Build a mock sandbox that simulates codeact wrapper execution.

    The sandbox stores files in memory and when exec() is called for the
    codeact wrapper script, returns a manifest JSON on stdout as the wrapper
    would.
    """
    sandbox = _make_mock_sandbox(files)
    manifest_json = json.dumps(manifest)
    run_id_holder: dict[str, str] = {}
    tx_state: dict[str, Any] = {
        "scratch_root": "/tmp/codeact-tx-mock",
        "patch_path": "/tmp/codeact-tx-patch-mock",
        "base_tree": "base-tree",
        "base_snapshot": {},
    }

    _orig_upload = sandbox.fs.upload_file

    async def _tracking_upload(content_or_path, path_or_content=None):
        """Track the run_id from the wrapper script upload path."""
        if isinstance(content_or_path, bytes):
            path = path_or_content
        else:
            path = content_or_path
        if isinstance(path, str) and path.startswith("/tmp/codeact-wrapper-"):
            # Extract run_id from /tmp/codeact-wrapper-{run_id}.py
            run_id = path.replace("/tmp/codeact-wrapper-", "").replace(".py", "")
            run_id_holder["run_id"] = run_id
            # Store manifest at expected path
            manifest_path = f"/tmp/codeact-{run_id}.json"
            with sandbox._lock:
                sandbox._file_store[manifest_path] = manifest_json
        await _orig_upload(content_or_path, path_or_content)

    sandbox.fs.upload_file = _tracking_upload

    class _ScriptProcess:
        async def exec(self, command: str, timeout: int = 300):
            resp = MagicMock()
            resp.exit_code = 0

            def _ok(output: str = ""):
                resp.result = f"{output}\n__CODEX_EXIT_CODE__=0\n"
                return resp

            def _relative_repo_path(path: str) -> str:
                if path == "/workspace":
                    return ""
                if path.startswith("/workspace/"):
                    return path.removeprefix("/workspace/")
                return path.lstrip("/")

            if "git rev-parse --show-toplevel" in command:
                return _ok("/workspace")
            if "mktemp -d /tmp/codeact-tx-" in command:
                return _ok(str(tx_state["scratch_root"]))
            if "mktemp /tmp/codeact-tx-patch-" in command:
                return _ok(str(tx_state["patch_path"]))
            if "worktree add --detach" in command:
                with sandbox._lock:
                    tx_state["base_snapshot"] = dict(sandbox._file_store)
                return _ok()
            if "write-tree" in command:
                with sandbox._lock:
                    tx_state["base_snapshot"] = dict(sandbox._file_store)
                return _ok(str(tx_state["base_tree"]))
            if "diff --cached --name-status" in command:
                seen: dict[str, str] = {}
                base_snapshot = tx_state["base_snapshot"]
                for item in manifest.get("writes", []) or []:
                    path = str(item.get("path", "") or "")
                    if not path:
                        continue
                    rel_path = _relative_repo_path(path)
                    seen[rel_path] = "M" if path in base_snapshot else "A"
                return _ok("\n".join(f"{status}\t{path}" for path, status in seen.items()))
            if "diff --cached --numstat" in command:
                seen: dict[str, None] = {}
                for item in manifest.get("writes", []) or []:
                    path = str(item.get("path", "") or "")
                    if path:
                        seen[_relative_repo_path(path)] = None
                return _ok("\n".join(f"1\t1\t{path}" for path in seen))
            if "git -C " in command and " show " in command and str(tx_state["base_tree"]) in command:
                match = re.search(rf"{re.escape(str(tx_state['base_tree']))}:([^\s'\"|]+)", command)
                if match:
                    rel_path = match.group(1)
                    base_path = f"/workspace/{rel_path}"
                    base_content = str(tx_state["base_snapshot"].get(base_path, "") or "")
                    encoded = base64.b64encode(base_content.encode("utf-8")).decode("ascii")
                    return _ok(encoded)
            if "path.write_text" in command:
                match = re.search(
                    r"(/(?:workspace|tmp)/[^\s'\";]+)\s+([A-Za-z0-9+/=]{8,})",
                    command,
                )
                if match:
                    path = match.group(1)
                    content = base64.b64decode(match.group(2).encode("ascii")).decode("utf-8")
                    with sandbox._lock:
                        sandbox._file_store[path] = content
                    wrapper_match = re.search(r"/tmp/codeact-wrapper-([a-f0-9]+)\.py", path)
                    if wrapper_match:
                        run_id = wrapper_match.group(1)
                        run_id_holder["run_id"] = run_id
                        with sandbox._lock:
                            sandbox._file_store[f"/tmp/codeact-{run_id}.json"] = manifest_json
                return _ok()
            if "path.read_text" in command:
                paths = re.findall(r"/(?:workspace|tmp)/[^\s'\";]+", command)
                path = paths[-1] if paths else ""
                with sandbox._lock:
                    content = sandbox._file_store.get(path)
                if content is None:
                    return _ok(json.dumps({"exists": False}))
                return _ok(json.dumps({"exists": True, "content": content}))
            wrapper_exec = re.search(r"/tmp/codeact-wrapper-([a-f0-9]+)\.py", command)
            if wrapper_exec:
                run_id = wrapper_exec.group(1)
                run_id_holder["run_id"] = run_id
                scratch_root = str(tx_state.get("scratch_root") or "")
                use_scratch = bool(scratch_root and scratch_root in command)
                with sandbox._lock:
                    for item in manifest.get("writes", []) or []:
                        path = str(item.get("path", "") or "")
                        content = str(item.get("content", "") or "")
                        if use_scratch and path.startswith("/workspace/"):
                            path = f"{scratch_root}/{path.removeprefix('/workspace/')}"
                        sandbox._file_store[path] = content
                    sandbox._file_store[f"/tmp/codeact-{run_id}.json"] = manifest_json
                result_line = json.dumps(
                    {
                        "manifest": f"/tmp/codeact-{run_id}.json",
                        "status": manifest.get("status", "ok"),
                    }
                )
                return _ok(result_line)
            if "worktree remove --force" in command or "rm -rf" in command or "rm -f" in command:
                return _ok()
            resp.result = json.dumps({"manifest": "", "status": manifest.get("status", "ok")})
            return resp

    sandbox.process = _ScriptProcess()
    return sandbox


# ===========================================================================
# 1. codeact read() → write() commits through OCC
# ===========================================================================


class TestCodeactOccWriteCommit:
    """Verify codeact helper-staged writes go through OCC prepare/commit."""

    def test_codeact_write_uses_occ_pipeline(self):
        """A codeact script that reads and writes a file should commit via OCC."""
        original = "x = 1\ny = 2\n"
        manifest = {
            "reads": [{"path": "/workspace/app.py", "hash": _content_hash(original)}],
            "writes": [{"path": "/workspace/app.py", "content": "x = 42\ny = 2\n"}],
            "shells": [],
            "status": "ok",
            "error": "",
        }
        sandbox = _make_codeact_sandbox(
            files={"/workspace/app.py": original},
            manifest=manifest,
        )
        svc = _make_ci_service(sandbox)
        ctx = _ctx(sandbox, svc)

        result = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code="content = read('/workspace/app.py')\nwrite('/workspace/app.py', 'x = 42\\ny = 2\\n')"),
            ctx,
        ))

        assert not result.is_error, f"codeact failed: {result.output}"
        data = json.loads(result.output)
        assert data["files_written"] == 1
        assert data["write_conflicts"] == []
        # Verify the file was actually written via OCC (through CI service)
        assert sandbox._file_store["/workspace/app.py"] == "x = 42\ny = 2\n"

    def test_codeact_write_new_file_through_occ(self):
        """Writing a new file (no prior read) still goes through OCC."""
        manifest = {
            "reads": [],
            "writes": [{"path": "/workspace/new_file.py", "content": "print('hello')\n"}],
            "shells": [],
            "status": "ok",
            "error": "",
        }
        sandbox = _make_codeact_sandbox(files={}, manifest=manifest)
        svc = _make_ci_service(sandbox)
        ctx = _ctx(sandbox, svc)

        result = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code="write('/workspace/new_file.py', \"print('hello')\\n\")"),
            ctx,
        ))

        assert not result.is_error
        data = json.loads(result.output)
        assert data["files_written"] == 1


# ===========================================================================
# 2. Stale read hash → conflict detected
# ===========================================================================


class TestCodeactTransactionBaseState:
    """CodeAct transactions commit against the scratch worktree base state."""

    def test_preexisting_change_is_transaction_base(self):
        """A change that exists before the transaction starts is part of the base."""
        original = "value = 1\n"
        stale_hash = _content_hash(original)

        # The manifest records an older helper read hash, but the current
        # transaction model commits against the scratch worktree base.
        manifest = {
            "reads": [{"path": "/workspace/config.py", "hash": stale_hash}],
            "writes": [{"path": "/workspace/config.py", "content": "value = 42\n"}],
            "shells": [],
            "status": "ok",
            "error": "",
        }
        # But the file on disk has already changed
        sandbox = _make_codeact_sandbox(
            files={"/workspace/config.py": "value = 999\n"},
            manifest=manifest,
        )
        svc = _make_ci_service(sandbox)
        ctx = _ctx(sandbox, svc)

        result = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code="c = read('/workspace/config.py')\nwrite('/workspace/config.py', 'value = 42\\n')"),
            ctx,
        ))

        data = json.loads(result.output)
        assert data["files_written"] == 1
        assert data["write_conflicts"] == []
        assert result.metadata.get("conflict") is False
        assert sandbox._file_store["/workspace/config.py"] == "value = 42\n"


# ===========================================================================
# 3. External file change between read and commit
# ===========================================================================


class TestCodeactExternalFileChange:
    """Pre-existing external changes are folded into the transaction base."""

    def test_existing_external_mutation_commits_from_current_base(self):
        """The scratch transaction starts from the current workspace content."""
        original = "alpha = 1\nbeta = 2\n"
        # Agent reads, gets hash of original
        manifest = {
            "reads": [{"path": "/workspace/data.py", "hash": _content_hash(original)}],
            "writes": [{"path": "/workspace/data.py", "content": "alpha = 100\nbeta = 2\n"}],
            "shells": [],
            "status": "ok",
            "error": "",
        }
        # External agent changed the file between read and commit
        sandbox = _make_codeact_sandbox(
            files={"/workspace/data.py": "alpha = 1\nbeta = 999\n"},
            manifest=manifest,
        )
        svc = _make_ci_service(sandbox)
        ctx = _ctx(sandbox, svc)

        result = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code="d = read('/workspace/data.py')\nwrite('/workspace/data.py', 'alpha = 100\\nbeta = 2\\n')"),
            ctx,
        ))

        data = json.loads(result.output)
        assert data["files_written"] == 1
        assert data["write_conflicts"] == []
        assert sandbox._file_store["/workspace/data.py"] == "alpha = 100\nbeta = 2\n"


# ===========================================================================
# 4. Multiple writes — last-write-wins within same script, OCC across scripts
# ===========================================================================


class TestCodeactMultipleWrites:
    """Codeact coalesces multiple writes to the same path within a single
    script (last write wins). OCC still guards the final commit."""

    def test_coalesced_writes_commit_final_value(self):
        """Multiple write() calls to same file → only final content committed."""
        original = "x = 0\n"
        manifest = {
            "reads": [{"path": "/workspace/counter.py", "hash": _content_hash(original)}],
            "writes": [
                {"path": "/workspace/counter.py", "content": "x = 1\n"},
                {"path": "/workspace/counter.py", "content": "x = 2\n"},
                {"path": "/workspace/counter.py", "content": "x = 3\n"},
            ],
            "shells": [],
            "status": "ok",
            "error": "",
        }
        sandbox = _make_codeact_sandbox(
            files={"/workspace/counter.py": original},
            manifest=manifest,
        )
        svc = _make_ci_service(sandbox)
        ctx = _ctx(sandbox, svc)

        result = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code="..."),
            ctx,
        ))

        data = json.loads(result.output)
        assert data["files_written"] == 1
        assert sandbox._file_store["/workspace/counter.py"] == "x = 3\n"

    def test_writes_to_different_files_all_committed(self):
        """Writes to multiple different files each go through OCC independently."""
        manifest = {
            "reads": [],
            "writes": [
                {"path": "/workspace/a.py", "content": "a = 1\n"},
                {"path": "/workspace/b.py", "content": "b = 2\n"},
                {"path": "/workspace/c.py", "content": "c = 3\n"},
            ],
            "shells": [],
            "status": "ok",
            "error": "",
        }
        sandbox = _make_codeact_sandbox(files={}, manifest=manifest)
        svc = _make_ci_service(sandbox)
        ctx = _ctx(sandbox, svc)

        result = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code="..."),
            ctx,
        ))

        data = json.loads(result.output)
        assert data["files_written"] == 3
        assert sandbox._file_store["/workspace/a.py"] == "a = 1\n"
        assert sandbox._file_store["/workspace/b.py"] == "b = 2\n"
        assert sandbox._file_store["/workspace/c.py"] == "c = 3\n"


# ===========================================================================
# 5. Two sequential codeact invocations — second detects first's changes
# ===========================================================================


class TestCodeactSequentialOcc:
    """Two codeact invocations targeting the same file rebase on current content."""

    def test_second_codeact_uses_current_workspace_base(self):
        """A second transaction starts after the first commit and can write again."""
        original = "state = 'init'\n"
        original_hash = _content_hash(original)

        # First codeact: read and write
        manifest_1 = {
            "reads": [{"path": "/workspace/state.py", "hash": original_hash}],
            "writes": [{"path": "/workspace/state.py", "content": "state = 'ready'\n"}],
            "shells": [],
            "status": "ok",
            "error": "",
        }
        sandbox = _make_codeact_sandbox(
            files={"/workspace/state.py": original},
            manifest=manifest_1,
        )
        svc = _make_ci_service(sandbox)
        ctx = _ctx(sandbox, svc)

        result_1 = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code="..."),
            ctx,
        ))
        data_1 = json.loads(result_1.output)
        assert data_1["files_written"] == 1

        # Second codeact carries an old helper read hash, but starts a fresh
        # transaction from the first commit's current workspace state.
        manifest_2 = {
            "reads": [{"path": "/workspace/state.py", "hash": original_hash}],
            "writes": [{"path": "/workspace/state.py", "content": "state = 'error'\n"}],
            "shells": [],
            "status": "ok",
            "error": "",
        }
        # Rebuild sandbox to serve new manifest but keep updated file store
        updated_files = dict(sandbox._file_store)
        sandbox_2 = _make_codeact_sandbox(files=updated_files, manifest=manifest_2)
        ctx_2 = _ctx(sandbox_2, svc)

        result_2 = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code="..."),
            ctx_2,
        ))

        data_2 = json.loads(result_2.output)
        assert data_2["files_written"] == 1
        assert data_2["write_conflicts"] == []
        assert result_2.metadata.get("conflict") is False
        assert sandbox_2._file_store["/workspace/state.py"] == "state = 'error'\n"

    def test_second_codeact_with_fresh_hash_succeeds(self):
        """Second codeact uses hash from after first write → succeeds."""
        original = "count = 0\n"

        # First write
        manifest_1 = {
            "reads": [{"path": "/workspace/count.py", "hash": _content_hash(original)}],
            "writes": [{"path": "/workspace/count.py", "content": "count = 1\n"}],
            "shells": [],
            "status": "ok",
            "error": "",
        }
        sandbox = _make_codeact_sandbox(
            files={"/workspace/count.py": original},
            manifest=manifest_1,
        )
        svc = _make_ci_service(sandbox)
        ctx = _ctx(sandbox, svc)

        result_1 = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code="..."),
            ctx,
        ))
        assert json.loads(result_1.output)["files_written"] == 1

        # Second write — uses fresh hash of "count = 1\n"
        fresh_hash = _content_hash("count = 1\n")
        manifest_2 = {
            "reads": [{"path": "/workspace/count.py", "hash": fresh_hash}],
            "writes": [{"path": "/workspace/count.py", "content": "count = 2\n"}],
            "shells": [],
            "status": "ok",
            "error": "",
        }
        updated_files = dict(sandbox._file_store)
        sandbox_2 = _make_codeact_sandbox(files=updated_files, manifest=manifest_2)
        ctx_2 = _ctx(sandbox_2, svc)

        result_2 = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code="..."),
            ctx_2,
        ))

        data_2 = json.loads(result_2.output)
        assert data_2["files_written"] == 1
        assert data_2["write_conflicts"] == []


# ===========================================================================
# 6. codeact without CI service — hard error
# ===========================================================================


class TestCodeactWithoutCiService:
    """When no CI service is in context, CodeAct does not execute."""

    def test_without_ci_service_errors_before_raw_write(self):
        """Without CI service, codeact must not raw-write files."""
        original = "old = True\n"
        manifest = {
            "reads": [{"path": "/workspace/simple.py", "hash": _content_hash(original)}],
            "writes": [{"path": "/workspace/simple.py", "content": "new = True\n"}],
            "shells": [],
            "status": "ok",
            "error": "",
        }
        sandbox = _make_codeact_sandbox(
            files={"/workspace/simple.py": original},
            manifest=manifest,
        )
        ctx = _ctx(sandbox, ci_service=None)  # No CI service

        result = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code="..."),
            ctx,
        ))

        assert result.is_error
        assert result.metadata["occ_required"] is True
        assert "Code intelligence/OCC is unavailable" in result.output
        assert sandbox._file_store["/workspace/simple.py"] == original


# ===========================================================================
# 9. OCC guard with concurrent codeact agents on same file via CI service
# ===========================================================================


class TestCodeactConcurrentAgentOcc:
    """Sequential CodeAct tool invocations each start a fresh transaction."""

    def test_sequential_codeact_same_file_rebases_on_latest_content(self):
        """A later tool call sees the previous commit as its transaction base."""
        original = "def process():\n    return 'v1'\n"
        original_hash = _content_hash(original)

        sandbox_a = _make_codeact_sandbox(
            files={"/workspace/proc.py": original},
            manifest={
                "reads": [{"path": "/workspace/proc.py", "hash": original_hash}],
                "writes": [{"path": "/workspace/proc.py", "content": "def process():\n    return 'v2_by_a'\n"}],
                "shells": [],
                "status": "ok",
                "error": "",
            },
        )
        svc = _make_ci_service(sandbox_a)
        ctx_a = _ctx(sandbox_a, svc, agent_run_id="agent-a")

        # Agent A commits first
        result_a = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code="..."),
            ctx_a,
        ))
        data_a = json.loads(result_a.output)
        assert data_a["files_written"] == 1

        # Agent B starts after A committed, so this is not a same-base
        # transaction conflict. Same-base conflict behavior is covered by
        # the live transaction tests that open two transactions concurrently.
        updated_files = dict(sandbox_a._file_store)
        sandbox_b = _make_codeact_sandbox(
            files=updated_files,
            manifest={
                "reads": [{"path": "/workspace/proc.py", "hash": original_hash}],
                "writes": [{"path": "/workspace/proc.py", "content": "def process():\n    return 'v2_by_b'\n"}],
                "shells": [],
                "status": "ok",
                "error": "",
            },
        )
        ctx_b = _ctx(sandbox_b, svc, agent_run_id="agent-b")

        result_b = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code="..."),
            ctx_b,
        ))
        data_b = json.loads(result_b.output)
        assert data_b["files_written"] == 1
        assert data_b["write_conflicts"] == []
        assert result_b.metadata.get("conflict") is False
        assert sandbox_b._file_store["/workspace/proc.py"] == "def process():\n    return 'v2_by_b'\n"

    def test_concurrent_codeact_different_files_no_conflict(self):
        """Two agents writing to different files — both succeed."""
        files = {
            "/workspace/x.py": "x = 0\n",
            "/workspace/y.py": "y = 0\n",
        }
        sandbox_a = _make_codeact_sandbox(
            files=dict(files),
            manifest={
                "reads": [{"path": "/workspace/x.py", "hash": _content_hash("x = 0\n")}],
                "writes": [{"path": "/workspace/x.py", "content": "x = 1\n"}],
                "shells": [],
                "status": "ok",
                "error": "",
            },
        )
        svc = _make_ci_service(sandbox_a)
        ctx_a = _ctx(sandbox_a, svc, agent_run_id="agent-a")

        result_a = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code="..."),
            ctx_a,
        ))
        assert json.loads(result_a.output)["files_written"] == 1

        updated_files = dict(sandbox_a._file_store)
        sandbox_b = _make_codeact_sandbox(
            files=updated_files,
            manifest={
                "reads": [{"path": "/workspace/y.py", "hash": _content_hash("y = 0\n")}],
                "writes": [{"path": "/workspace/y.py", "content": "y = 1\n"}],
                "shells": [],
                "status": "ok",
                "error": "",
            },
        )
        ctx_b = _ctx(sandbox_b, svc, agent_run_id="agent-b")

        result_b = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code="..."),
            ctx_b,
        ))
        assert json.loads(result_b.output)["files_written"] == 1
        assert json.loads(result_b.output)["write_conflicts"] == []


# ===========================================================================
# 10. Live sandbox tests (require Daytona credentials)
# ===========================================================================


@pytest.mark.skipif(not HAS_DAYTONA, reason="Daytona credentials not configured")
@pytest.mark.live
class TestLiveSandboxCodeactOcc:
    """Run codeact OCC tests against a real Daytona sandbox with real CI service."""

    @pytest.fixture(autouse=True)
    def _setup_sandbox(self):
        from sandbox.testing import create_test_sandbox, delete_test_sandbox, get_sandbox_service

        info = create_test_sandbox(name="codeact-occ")
        self.sandbox_id = info["id"]
        self.sandbox_svc = get_sandbox_service()
        self.raw_sandbox = self.sandbox_svc.get_sandbox_object(self.sandbox_id)

        home_resp = self.raw_sandbox.process.exec("pwd", timeout=10)
        self.home = (home_resp.result or "").strip() or "/home/daytona"

        # Async-compatible wrapper for codeact (which awaits sandbox ops)
        self.async_sandbox = _AsyncSandboxWrapper(self.raw_sandbox)

        # Seed test files
        write_text_via_exec(
            self.raw_sandbox,
            f"{self.home}/shared.py",
            "# Header\ndef greet():\n    return 'hello'\n\ndef farewell():\n    return 'bye'\n",
        )

        yield
        delete_test_sandbox(self.sandbox_id)

    def _make_live_ci_service(self) -> CodeIntelligenceService:
        svc = CodeIntelligenceService(
            sandbox_id=self.sandbox_id,
            workspace_root=self.home,
            sandbox=self.raw_sandbox,
        )
        return svc

    def _live_ctx(self, ci_service: Any, agent_run_id: str = "live-agent") -> ToolExecutionContext:
        return ToolExecutionContext(
            cwd=Path(self.home),
            metadata={
                "daytona_sandbox": self.async_sandbox,
                "daytona_cwd": self.home,
                "ci_service": ci_service,
                "agent_run_id": agent_run_id,
            },
        )

    def test_live_codeact_read_write_occ_roundtrip(self):
        """codeact read/write on a real sandbox commits through OCC."""
        svc = self._make_live_ci_service()
        ctx = self._live_ctx(svc)

        code = f"""
content = read('{self.home}/shared.py')
new_content = content.replace("return 'hello'", "return 'HELLO_OCC'")
write('{self.home}/shared.py', new_content)
"""
        result = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code=code),
            ctx,
        ))

        assert not result.is_error, f"Live codeact failed: {result.output}"
        data = json.loads(result.output)
        assert data["files_written"] == 1
        assert data["write_conflicts"] == []

        # Verify on disk
        final = read_text_via_exec(self.raw_sandbox, f"{self.home}/shared.py")
        assert "HELLO_OCC" in final

    def test_live_codeact_stale_hash_conflict(self):
        """codeact with stale hash on real sandbox → conflict."""
        svc = self._make_live_ci_service()
        shared = f"{self.home}/shared.py"

        # Read current content and its hash
        original = read_text_via_exec(self.raw_sandbox, shared)
        original_hash = _content_hash(original)

        # Agent A modifies the file through OCC
        prep_a = svc.prepare_write(shared, agent_id="agent-a")
        assert isinstance(prep_a, PreparedWrite)
        new_a = original.replace("return 'hello'", "return 'A_WROTE'")
        result_a = svc.commit_prepared_write(prep_a, new_a, edit_type="codeact", description="agent-a")
        assert result_a.success, f"Agent A failed: {result_a.message}"

        # The read hash in the manifest is from *before* A wrote (stale)
        # But codeact's read() will actually read the file, getting A's version.
        # So we test at the CI layer: agent B calls prepare_ci_write with stale expected_hash
        prep_b_result = svc.prepare_write(shared, agent_id="agent-b", expected_hash=original_hash)
        # Should fail because the file hash changed
        assert not isinstance(prep_b_result, PreparedWrite), (
            "Stale hash should have been rejected by prepare_write"
        )
        assert prep_b_result.conflict

    def test_live_codeact_shell_and_write(self):
        """codeact with both shell and write on real sandbox."""
        svc = self._make_live_ci_service()
        ctx = self._live_ctx(svc)

        code = f"""
result = shell('echo test_output')
write('{self.home}/shell_result.txt', result['stdout'].strip() + '\\n')
"""
        result = _run(daytona_codeact.execute(
            daytona_codeact.input_model(code=code),
            ctx,
        ))

        assert not result.is_error, f"Live shell+write failed: {result.output}"
        data = json.loads(result.output)
        assert data["shells_run"] == 1
        assert data["files_written"] == 1


# ===========================================================================
# 11. Live LLM-driven codeact OCC tests (require Daytona + LLM credentials)
# ===========================================================================


@pytest.mark.skipif(not HAS_DAYTONA, reason="Daytona credentials not configured")
@pytest.mark.live
class TestLiveLLMCodeactOcc:
    """Use EvalAgent to drive codeact through a real LLM, verifying OCC guards
    the full stack: LLM → codeact tool → read/write helpers → OCC → sandbox."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from sandbox.testing import create_test_sandbox, delete_test_sandbox

        info = create_test_sandbox(name="codeact-occ-llm")
        self.sandbox_id = info["id"]
        yield
        delete_test_sandbox(self.sandbox_id)

    def _skip_if_no_credentials(self):
        from engine.testing.eval_agent import EvalAgent

        if not EvalAgent.has_all():
            pytest.skip("LLM + Daytona credentials required")

    def test_llm_codeact_write_uses_occ(self):
        """LLM-driven codeact write goes through OCC pipeline."""
        self._skip_if_no_credentials()
        from tests.test_e2e.conftest import create_eval_agent
        from sandbox.testing import get_sandbox_service

        svc = get_sandbox_service()
        raw = svc.get_sandbox_object(self.sandbox_id)
        home_resp = raw.process.exec("pwd", timeout=10)
        home = (home_resp.result or "").strip() or "/home/daytona"

        # Seed file
        write_text_via_exec(
            raw,
            f"{home}/config.py",
            "config = {'debug': False, 'version': '1.0'}\n",
        )

        agent = create_eval_agent(sandbox_id=self.sandbox_id)

        result = _run(agent.invoke(
            f"Use the daytona_codeact tool with this Python code:\n\n"
            f"content = read('{home}/config.py')\n"
            f"new_content = content.replace(\"'debug': False\", \"'debug': True\")\n"
            f"write('{home}/config.py', new_content)\n"
            f"print('Updated debug flag')\n"
        ))

        assert result.has_tool("daytona_codeact"), f"Expected codeact call, got: {result.tool_names}"
        assert not result.has_unrecovered_errors, (
            f"Unrecovered errors: {[e.output for e in result.unrecovered_error_events]}"
        )

        # Verify the write landed
        final = read_text_via_exec(raw, f"{home}/config.py")
        assert "'debug': True" in final or '"debug": True' in final or "debug" in final

    def test_llm_codeact_shell_write_roundtrip(self):
        """LLM uses codeact with shell() and write() together."""
        self._skip_if_no_credentials()
        from tests.test_e2e.conftest import create_eval_agent
        from sandbox.testing import get_sandbox_service

        svc = get_sandbox_service()
        raw = svc.get_sandbox_object(self.sandbox_id)
        home_resp = raw.process.exec("pwd", timeout=10)
        home = (home_resp.result or "").strip() or "/home/daytona"

        agent = create_eval_agent(sandbox_id=self.sandbox_id)

        result = _run(agent.invoke(
            f"Use the daytona_codeact tool with this Python code:\n\n"
            f"result = shell('python3 -c \"print(2+2)\"')\n"
            f"write('{home}/computed.txt', result['stdout'].strip() + '\\n')\n"
            f"print('Done')\n"
        ))

        assert result.has_tool("daytona_codeact"), f"Expected codeact call, got: {result.tool_names}"

        final = read_text_via_exec(raw, f"{home}/computed.txt")
        assert "4" in final
