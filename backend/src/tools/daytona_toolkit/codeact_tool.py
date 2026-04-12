"""CodeAct tool — multi-step code thinking and execution in a sandbox.

Executes a Python script in the sandbox with atomic file I/O.
The script has access to read(), write(), and shell() helpers. All writes
are staged and committed atomically after the script finishes.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid

from tools.core.base import ToolExecutionContext, ToolResult
from tools.daytona_toolkit.tools import (
    _get_cwd,
    _recover_sandbox,
    _require_sandbox,
    _wrap_bash_command,
)
from tools.daytona_toolkit.ci_integration import (
    prime_cache_after_write,
    record_edit_in_ledger,
)
from tools.daytona_toolkit.codeact_policy import resolve_policy
from tools.core.decorator import tool

logger = logging.getLogger(__name__)

_WRAPPER_TEMPLATE = r'''
import base64, hashlib, json, os, subprocess, sys, traceback

_RUN_ID = "{run_id}"
_MANIFEST = {{"reads": [], "writes": [], "shells": [], "status": "ok", "error": ""}}
_CODEACT_CWD = {codeact_cwd}

def read(path):
    """Read a file and track the read."""
    with open(path, "r") as f:
        content = f.read()
    h = hashlib.sha256(content.encode()).hexdigest()[:16]
    _MANIFEST["reads"].append({{"path": path, "hash": h}})
    return content

def write(path, content):
    """Stage a file write (not written to disk until commit)."""
    _MANIFEST["writes"].append({{"path": path, "content": content}})

def shell(command, timeout=300):
    """Execute a shell command."""
    try:
        proc = subprocess.run(
            ["env", "-u", "LC_ALL", "bash", "-o", "pipefail", "-lc", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=_CODEACT_CWD or None,
        )
        result = {{"command": command, "stdout": proc.stdout, "stderr": proc.stderr, "exit_code": proc.returncode}}
    except subprocess.TimeoutExpired:
        result = {{"command": command, "stdout": "", "stderr": "timeout", "exit_code": -1}}
    except Exception as e:
        result = {{"command": command, "stdout": "", "stderr": str(e), "exit_code": -1}}
    _MANIFEST["shells"].append(result)
    return result

try:
    _CODE = base64.b64decode("{code_b64}").decode("utf-8")
    exec(_CODE, {{"read": read, "write": write, "shell": shell, "__name__": "__codeact__"}})
except Exception as e:
    _MANIFEST["status"] = "error"
    _MANIFEST["error"] = traceback.format_exc()[:2000]

# Write manifest
with open("/tmp/codeact-{run_id}.json", "w") as f:
    json.dump(_MANIFEST, f)

print(json.dumps({{"manifest": "/tmp/codeact-{run_id}.json", "status": _MANIFEST["status"]}}))
'''


def _build_wrapper(code: str, *, run_id: str, cwd: str | None) -> str:
    code_b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
    return _WRAPPER_TEMPLATE.format(
        run_id=run_id,
        code_b64=code_b64,
        codeact_cwd=json.dumps(cwd) if cwd else "None",
    )


def _build_exec_command(script_path: str, *, cwd: str | None) -> str:
    command = f"python3 {script_path}"
    if cwd:
        command = f"cd {json.dumps(cwd)} && {command}"
    return _wrap_bash_command(command)


@tool(
    name="daytona_codeact",
    description="Execute Python code with atomic file I/O via read(), write(), and shell() helpers.",
    background="optional",
)
async def daytona_codeact(
    code: str,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Execute multi-step code with atomic file I/O in the Daytona sandbox.

    Args:
        code: Python code to execute in the sandbox. Has access to read(path), write(path, content), and shell(command, timeout=300). All writes are staged and committed atomically after execution.

    Returns:
        status (str): Execution status — ok or error
        files_written (int): Number of files committed
        shells_run (int): Number of shell commands executed
        error (str): Error message if failed
    """
    try:
        sandbox = await _require_sandbox(context)
    except Exception as exc:
        return ToolResult(output=str(exc), is_error=True)

    policy = resolve_policy(context)

    preflight_error = policy.preflight(code)
    if preflight_error is not None:
        return ToolResult(output=preflight_error, is_error=True)

    run_id = uuid.uuid4().hex[:8]
    # Build and upload wrapper script
    repo_cwd = _get_cwd(context)
    if repo_cwd is None:
        logger.warning("daytona_codeact: no daytona_cwd set — shell() will use sandbox default cwd")
    wrapper = _build_wrapper(code, run_id=run_id, cwd=repo_cwd)
    script_path = f"/tmp/codeact-wrapper-{run_id}.py"
    exec_command = _build_exec_command(script_path, cwd=repo_cwd)

    try:
        await sandbox.fs.upload_file(wrapper.encode("utf-8"), script_path)
    except Exception as exc:
        try:
            sandbox = await _recover_sandbox(context, exc)
            await sandbox.fs.upload_file(wrapper.encode("utf-8"), script_path)
        except Exception as recovery_exc:
            return ToolResult(output=f"Failed to upload script: {recovery_exc}", is_error=True)

    # Execute
    try:
        response = await sandbox.process.exec(
            exec_command,
            timeout=300,
        )
        stdout = response.result or ""
    except Exception as exc:
        try:
            sandbox = await _recover_sandbox(context, exc)
            response = await sandbox.process.exec(
                exec_command,
                timeout=300,
            )
            stdout = response.result or ""
        except Exception as recovery_exc:
            return ToolResult(output=f"Execution failed: {recovery_exc}", is_error=True)

    # Parse output
    stdout_lines = stdout.splitlines()
    script_stdout = "\n".join(stdout_lines[:-1]).strip() if stdout_lines else ""
    try:
        result_line = stdout_lines[-1] if stdout_lines else "{}"
        result = json.loads(result_line)
    except (json.JSONDecodeError, IndexError):
        return ToolResult(
            output=f"Script output:\n{stdout[:4000]}",
            metadata={"status": "unknown"},
        )

    if result.get("status") == "error":
        return ToolResult(
            output=f"CodeAct execution error:\n{stdout[:4000]}",
            is_error=True,
        )

    # Read manifest
    manifest_path = result.get("manifest", "")
    if not manifest_path:
        return ToolResult(output=f"Script output:\n{stdout[:4000]}")

    try:
        raw = await sandbox.fs.download_file(manifest_path)
        manifest = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except Exception:
        return ToolResult(output=f"Script completed but manifest unreadable:\n{stdout[:4000]}")

    manifest_error = policy.post_manifest(manifest)
    if manifest_error is not None:
        return ToolResult(output=manifest_error, is_error=True)

    # Commit staged writes
    writes = manifest.get("writes", [])
    committed = 0
    errors = []
    warnings = policy.commit_warnings(writes)

    for w in writes:
        path = w.get("path", "")
        content = w.get("content", "")
        try:
            await sandbox.fs.upload_file(content.encode("utf-8"), path)
            prime_cache_after_write(context, path, content)
            record_edit_in_ledger(context, path, edit_type="codeact")
            committed += 1
        except Exception as exc:
            errors.append(f"{path}: {exc}")

    # Build output
    shells = manifest.get("shells", [])
    shell_summaries = []
    shell_outputs = []
    for sh in shells[:3]:
        cmd = sh.get("command", "")[:80]
        exit_code = sh.get("exit_code", "?")
        shell_summaries.append(f"$ {cmd} → exit {exit_code}")
        shell_outputs.append(
            {
                "command": sh.get("command", ""),
                "exit_code": exit_code,
                "stdout": sh.get("stdout", ""),
                "stderr": sh.get("stderr", ""),
            }
        )

    output = json.dumps(
        {
            "cwd": _get_cwd(context) or "",
            "status": manifest.get("status", "unknown"),
            "files_written": committed,
            "shells_run": len(shells),
            "shell_summaries": shell_summaries,
            "shell_outputs": shell_outputs,
            "script_stdout": script_stdout,
            "write_errors": errors or [],
            "warnings": warnings,
            "error": manifest.get("error", "")[:500] if manifest.get("error") else "",
        }
    )

    return ToolResult(
        output=output,
        is_error=bool(errors),
        metadata={
            "status": manifest.get("status", "unknown"),
            "files_written": committed,
            "shells_run": len(shells),
        },
    )
