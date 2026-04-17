"""CodeAct tool - shell or Python execution in the Daytona sandbox."""

from __future__ import annotations

import base64
import json
import re
import shlex
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic.json_schema import GenerateJsonSchema

from code_intelligence.tuning import CODE_INTELLIGENCE_TUNING
from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.ci_runtime import (
    ci_required_result,
    exec_ci_process_operation,
    get_ci_service,
)
from tools.core.decorator import tool
from tools.daytona_toolkit._daytona_utils import (
    _extract_exit_code,
    _get_cwd,
    _read_text_file_via_exec,
    _recover_sandbox,
    _require_sandbox,
    _supports_exec_transport,
    _upload_file_compat,
    _write_text_file_via_exec,
    _wrap_bash_command,
    is_coordinated_team_agent,
)
from tools.daytona_toolkit.ci_integration import destructive_shell_command_error
from tools.daytona_toolkit._shell_policy import (
    _normalize_team_shell_command,
    shell_policy_source,
)

_DESTRUCTIVE_GIT_PATTERN = re.compile(
    r"git\s+(stash|reset\s+--hard|checkout\s+--\s|checkout\s+\.\s*$|clean\s+-[fd])",
    flags=re.IGNORECASE,
)
_CODEACT_DEFAULT_TIMEOUT = CODE_INTELLIGENCE_TUNING.codeact_default_timeout
_CODEACT_WRITE_TIMEOUT = CODE_INTELLIGENCE_TUNING.codeact_write_timeout


class DaytonaCodeActInput(BaseModel):
    """Custom CodeAct input schema.

    Keep runtime parsing permissive so existing callers still flow through
    ``_resolve_mode()``, but publish a stricter JSON schema to the model.
    Anthropic-compatible models will otherwise happily emit explicit JSON
    ``null`` for optional string params and spin on empty CodeAct calls.
    """

    mode: Literal["python", "shell"] | None = Field(
        default=None,
        description=(
            "Optional explicit mode. Omit unless you need to force shell or "
            "python execution."
        ),
    )
    code: str | None = Field(
        default=None,
        description=(
            "Python code to execute. Use for multi-step helper flows; do not "
            "set alongside `command`."
        ),
    )
    command: str | None = Field(
        default=None,
        description=(
            "Shell command to execute directly. Preferred for tests, builds, "
            "and verification; do not set alongside `code`."
        ),
    )
    timeout: int = Field(
        default=_CODEACT_DEFAULT_TIMEOUT,
        description="Timeout in seconds for shell mode execution.",
    )

    @classmethod
    def model_json_schema(
        cls,
        by_alias: bool = True,
        ref_template: str = "#/$defs/{model}",
        schema_generator: type[GenerateJsonSchema] = GenerateJsonSchema,
        mode: str = "validation",
    ) -> dict[str, Any]:
        schema = super().model_json_schema(
            by_alias=by_alias,
            ref_template=ref_template,
            schema_generator=schema_generator,
            mode=mode,
        )
        props = schema.get("properties", {})

        def _strip_null_variant(name: str, expected_type: str) -> None:
            prop = props.get(name)
            if not isinstance(prop, dict):
                return
            cleaned: dict[str, Any] | None = None
            for variant in prop.get("anyOf", []):
                if isinstance(variant, dict) and variant.get("type") == expected_type:
                    cleaned = dict(variant)
                    break
            if cleaned is None:
                return
            if "title" in prop:
                cleaned["title"] = prop["title"]
            if "description" in prop:
                cleaned["description"] = prop["description"]
            cleaned.pop("default", None)
            if expected_type == "string":
                cleaned["minLength"] = max(int(cleaned.get("minLength", 1) or 1), 1)
            props[name] = cleaned

        _strip_null_variant("mode", "string")
        _strip_null_variant("code", "string")
        _strip_null_variant("command", "string")

        schema["oneOf"] = [
            {"required": ["command"]},
            {"required": ["code"]},
        ]
        return schema


class DaytonaCodeActShellOutput(BaseModel):
    command: str = Field(..., description="Shell command that was run.")
    exit_code: int | str = Field(..., description="Command exit code.")
    stdout: str = Field(..., description="Captured stdout.")
    stderr: str = Field(..., description="Captured stderr.")


class DaytonaCodeActOutput(BaseModel):
    cwd: str = Field(..., description="Current sandbox working directory.")
    status: str = Field(..., description="Execution status: ok or error.")
    files_written: int = Field(..., description="Number of Python helper write calls observed.")
    shells_run: int = Field(..., description="Number of shell commands executed.")
    shell_summaries: list[str] = Field(
        default_factory=list,
        description="Compact summaries of the first shell commands.",
    )
    shell_outputs: list[DaytonaCodeActShellOutput] = Field(
        default_factory=list,
        description="Captured output for the first shell commands.",
    )
    script_stdout: str = Field(..., description="Python wrapper stdout before the manifest line.")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal warnings.")
    error: str = Field(default="", description="Error detail when status is error.")


def _destructive_git_command_error(command: str) -> str | None:
    if _DESTRUCTIVE_GIT_PATTERN.search(command or ""):
        return (
            "BLOCKED: destructive git commands (stash, reset --hard, checkout --, clean) "
            "are forbidden. They destroy other agents' work and bypass process audit. "
            "Use targeted edit tools instead."
        )
    return None


def _format_codeact_error(
    *,
    stdout: str,
    manifest_error: str = "",
) -> str:
    detail = manifest_error.strip() or stdout[:4000]
    lines = ["CodeAct execution error:"]
    if detail:
        lines.append(detail)
    if "blocked in codeact" in detail or "subprocess" in detail or "os.system" in detail:
        lines.append(
            "Use `daytona_codeact(command=\"...\")` or `shell(\"...\")` inside Python mode; "
            "do not import `subprocess` or call `os.system()`."
        )
    return "\n".join(lines)


def _python_literal_or_none(value: str | None) -> str:
    if not value or str(value).strip().lower() == "none":
        return "None"
    return json.dumps(value)


_WRAPPER_TEMPLATE = r'''
import base64, hashlib, importlib, json, os, re, shlex, subprocess, traceback

_RUN_ID = "{run_id}"
_MANIFEST = {{"reads": [], "writes": [], "shells": [], "status": "ok", "error": ""}}
_CODEACT_CWD = {codeact_cwd}
_CODEACT_REPO_ROOT = {codeact_repo_root}
_ENFORCE_TEAM_SHELL_POLICY = {enforce_team_shell_policy}
_USER_LOCAL_BIN_EXPORT = 'export PATH="$HOME/.local/bin:$PATH"'
_PROJECT_VENV_BIN_EXPORT = 'if [ -d .venv/bin ]; then export PATH="$PWD/.venv/bin:$PATH"; fi'
_PYTHON3_SHIM = 'if command -v python3 >/dev/null 2>&1; then python() {{ command python3 "$@"; }}; fi'
_BLOCKED_MODULES = frozenset({{"subprocess", "shutil"}})
_DESTRUCTIVE_GIT_PATTERN = re.compile(
    r"git\s+(stash|reset\s+--hard|checkout\s+--\s|checkout\s+\.\s*$|clean\s+-[fd])",
    flags=re.IGNORECASE,
)
_DESTRUCTIVE_SHELL_PATTERN = re.compile(
    r"(?:^|[;&|]\s*)(?:"
    r"rm\s+(?:-\S*[rR]\S*\s+|--recursive\s+)(?:/(?:testbed|workspace|home|opt|usr|var|etc|tmp)\b|/\s|/\.\.|\.\.)"
    r"|mv\s+/(?:testbed|workspace|home|opt|usr|var|etc)(?:/[^/\s]*)?(?:\s|$)"
    r"|chmod\s+(?:-\S*R\S*\s+|--recursive\s+)\S*\s+/"
    r"|chown\s+(?:-\S*R\S*\s+|--recursive\s+)\S*\s+/"
    r"|rm\s+-\S*[rR]\S*\s+\.\s*$"
    r"|mkfs\b|dd\s+.*of=/"
    r")",
    flags=re.IGNORECASE,
)
{shell_policy_source}

def _normalize_path(path):
    if os.path.isabs(path):
        return path
    return os.path.abspath(path)

def read(path):
    resolved = _normalize_path(path)
    with open(resolved, "r", encoding="utf-8") as f:
        content = f.read()
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    _MANIFEST["reads"].append({{"path": resolved, "hash": h}})
    return content

def write(path, content):
    resolved = _normalize_path(path)
    parent = os.path.dirname(resolved)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(resolved, "w", encoding="utf-8") as f:
        f.write(content)
    _MANIFEST["writes"].append({{"path": resolved, "content": content}})
    return resolved

def _block_shell_command(command, message):
    _MANIFEST["shells"].append(
        {{
            "command": command,
            "stdout": "",
            "stderr": message,
            "exit_code": -1,
            "blocked": True,
        }}
    )
    raise RuntimeError(message)

def shell(command, timeout={codeact_default_timeout}):
    if _ENFORCE_TEAM_SHELL_POLICY:
        command, policy_warnings = _normalize_team_shell_command(
            command,
            repo_root=_CODEACT_REPO_ROOT,
        )
        _MANIFEST.setdefault("warnings", []).extend(policy_warnings)
    if _DESTRUCTIVE_GIT_PATTERN.search(command or ""):
        _block_shell_command(
            command,
            "BLOCKED: destructive git commands (stash, reset --hard, checkout --, clean) "
            "are forbidden. They destroy other agents' work and bypass process audit. "
            "Use targeted edit tools instead.",
        )
    if _DESTRUCTIVE_SHELL_PATTERN.search(command or ""):
        _block_shell_command(
            command,
            "BLOCKED: destructive shell command that targets workspace or system "
            "directories is forbidden. Use targeted file operations instead.",
        )
    try:
        wrapped = f"{{_USER_LOCAL_BIN_EXPORT}} && {{_PROJECT_VENV_BIN_EXPORT}} && {{_PYTHON3_SHIM}} && {{command}}"
        proc = subprocess.run(
            ["env", "-u", "LC_ALL", "bash", "-o", "pipefail", "-lc", wrapped],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=_CODEACT_CWD or None,
        )
        result = {{
            "command": command,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        }}
    except subprocess.TimeoutExpired:
        result = {{
            "command": command,
            "stdout": "",
            "stderr": "timeout",
            "exit_code": -1,
        }}
    except Exception as exc:
        result = {{
            "command": command,
            "stdout": "",
            "stderr": str(exc),
            "exit_code": -1,
        }}
    _MANIFEST["shells"].append(result)
    return result

import builtins as _builtins_mod
_real_import = _builtins_mod.__import__

def _guarded_import(name, *args, **kwargs):
    top = name.split(".")[0]
    if top in _BLOCKED_MODULES:
        raise ImportError(
            f"import {{name!r}} is blocked in codeact. "
            "Use daytona_codeact shell mode for commands and read()/write() for file I/O."
        )
    return _real_import(name, *args, **kwargs)

_sandbox_builtins = dict(vars(_builtins_mod))
_sandbox_builtins["__import__"] = _guarded_import

_real_import_module = importlib.import_module

def _guarded_import_module(name, package=None):
    top = name.split(".")[0]
    if top in _BLOCKED_MODULES:
        raise ImportError(
            f"import {{name!r}} is blocked in codeact. "
            "Use daytona_codeact shell mode for commands and read()/write() for file I/O."
        )
    return _real_import_module(name, package)

importlib.import_module = _guarded_import_module

if _ENFORCE_TEAM_SHELL_POLICY:
    def _blocked_os_process(*args, **kwargs):
        raise RuntimeError(
            "CodeAct policy error: coordinated team lanes must use `daytona_codeact` shell mode "
            "or `shell(\"...\")` inside Python mode for repo commands. Replace `os.system()`/"
            "`os.popen()` wrappers."
        )

    os.system = _blocked_os_process
    os.popen = _blocked_os_process

try:
    _CODE = base64.b64decode("{code_b64}").decode("utf-8")
    exec(
        _CODE,
        {{"read": read, "write": write, "shell": shell, "__name__": "__codeact__", "__builtins__": _sandbox_builtins}},
    )
except Exception:
    _MANIFEST["status"] = "error"
    _MANIFEST["error"] = traceback.format_exc()[:2000]

with open("/tmp/codeact-{run_id}.json", "w", encoding="utf-8") as f:
    json.dump(_MANIFEST, f)

print(json.dumps({{"manifest": "/tmp/codeact-{run_id}.json", "status": _MANIFEST["status"]}}))
'''


def _build_wrapper(
    code: str,
    *,
    enforce_team_shell_policy: bool,
    run_id: str,
    cwd: str | None,
    repo_root: str | None,
) -> str:
    code_b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
    return _WRAPPER_TEMPLATE.format(
        run_id=run_id,
        code_b64=code_b64,
        codeact_cwd=_python_literal_or_none(cwd),
        codeact_repo_root=_python_literal_or_none(repo_root),
        enforce_team_shell_policy="True" if enforce_team_shell_policy else "False",
        shell_policy_source=shell_policy_source(),
        codeact_default_timeout=_CODEACT_DEFAULT_TIMEOUT,
    )


def _build_exec_command(script_path: str, *, cwd: str | None) -> str:
    command = f"python3 {script_path}"
    if cwd:
        command = f"cd {json.dumps(cwd)} && {command}"
    return _wrap_bash_command(command)


def _resolve_mode(
    *,
    mode: Literal["python", "shell"] | None,
    code: str | None,
    command: str | None,
) -> tuple[Literal["python", "shell"] | None, str | None]:
    has_code = isinstance(code, str) and bool(code.strip())
    has_command = isinstance(command, str) and bool(command.strip())
    if mode == "python":
        if not has_code or has_command:
            return None, "`mode=\"python\"` requires `code` and forbids `command`."
        return "python", None
    if mode == "shell":
        if not has_command or has_code:
            return None, "`mode=\"shell\"` requires `command` and forbids `code`."
        return "shell", None
    if has_code and has_command:
        return None, "Provide either `code` or `command`, not both."
    if has_code:
        return "python", None
    if has_command:
        return "shell", None
    return None, "Provide `code` for Python mode or `command` for shell mode."


async def _exec_shell_command(
    context: ToolExecutionContext,
    sandbox: object,
    *,
    command: str,
    cwd: str | None,
    timeout: int,
) -> dict[str, object]:
    wrapped_command = command if not cwd else f"cd {shlex.quote(cwd)} && {command}"
    response = await exec_ci_process_operation(
        context,
        sandbox,
        _wrap_bash_command(wrapped_command),
        timeout=timeout,
        description="daytona_codeact shell",
        edit_type="codeact",
    )
    stdout = getattr(response, "result", "") or ""
    fallback_exit_code = getattr(response, "exit_code", None)
    cleaned_stdout, exit_code = _extract_exit_code(stdout, fallback_exit_code=fallback_exit_code)
    return {
        "command": command,
        "stdout": cleaned_stdout,
        "stderr": cleaned_stdout if exit_code != 0 else "",
        "exit_code": exit_code,
    }


async def _run_shell_with_recovery(
    context: ToolExecutionContext,
    sandbox: object,
    *,
    command: str,
    cwd: str | None,
    timeout: int,
) -> tuple[dict[str, object] | None, object, ToolResult | None]:
    try:
        return (
            await _exec_shell_command(
                context,
                sandbox,
                command=command,
                cwd=cwd,
                timeout=timeout,
            ),
            sandbox,
            None,
        )
    except Exception as exc:
        try:
            sandbox = await _recover_sandbox(context, exc)
            return (
                await _exec_shell_command(
                    context,
                    sandbox,
                    command=command,
                    cwd=cwd,
                    timeout=timeout,
                ),
                sandbox,
                None,
            )
        except Exception as recovery_exc:
            return None, sandbox, ToolResult(output=f"Execution failed: {recovery_exc}", is_error=True)


def _build_tool_output(
    *,
    context: ToolExecutionContext,
    status: str,
    files_written: int,
    shells: list[dict[str, object]],
    script_stdout: str,
    warnings: list[str],
    error: str = "",
) -> ToolResult:
    shell_summaries: list[str] = []
    shell_outputs: list[dict[str, object]] = []
    for shell_result in shells[:3]:
        command = str(shell_result.get("command", "") or "")
        exit_code = shell_result.get("exit_code", "?")
        shell_summaries.append(f"$ {command[:80]} -> exit {exit_code}")
        shell_outputs.append(
            {
                "command": command,
                "exit_code": exit_code,
                "stdout": str(shell_result.get("stdout", "") or ""),
                "stderr": str(shell_result.get("stderr", "") or ""),
            }
        )

    is_error = status == "error"

    return ToolResult(
        output=json.dumps(
            {
                "cwd": _get_cwd(context) or "",
                "status": status,
                "files_written": files_written,
                "shells_run": len(shells),
                "shell_summaries": shell_summaries,
                "shell_outputs": shell_outputs,
                "script_stdout": script_stdout,
                "warnings": warnings,
                "error": error[:500] if error else "",
            }
        ),
        is_error=is_error,
        metadata={
            "status": status,
            "files_written": files_written,
            "shells_run": len(shells),
        },
    )


async def _execute_python_wrapper(
    context: ToolExecutionContext,
    sandbox: object,
    *,
    code: str,
    cwd: str | None,
    repo_root: str | None,
    enforce_team_shell_policy: bool,
) -> tuple[str | None, object, ToolResult | None]:
    run_id = uuid.uuid4().hex[:8]
    wrapper = _build_wrapper(
        code,
        run_id=run_id,
        cwd=cwd,
        repo_root=repo_root,
        enforce_team_shell_policy=enforce_team_shell_policy,
    )
    script_path = f"/tmp/codeact-wrapper-{run_id}.py"
    exec_command = _build_exec_command(script_path, cwd=cwd)
    try:
        await _write_text_file_via_exec(
            sandbox,
            script_path,
            wrapper,
            timeout=_CODEACT_WRITE_TIMEOUT,
        )
    except Exception as exc:
        try:
            sandbox = await _recover_sandbox(context, exc)
            try:
                await _write_text_file_via_exec(
                    sandbox,
                    script_path,
                    wrapper,
                    timeout=_CODEACT_WRITE_TIMEOUT,
                )
            except Exception:
                if _supports_exec_transport(sandbox):
                    raise
                await _upload_file_compat(sandbox, wrapper.encode("utf-8"), script_path)
        except Exception as recovery_exc:
            return None, sandbox, ToolResult(
                output=f"Failed to upload script: {recovery_exc}",
                is_error=True,
            )

    try:
        response = await exec_ci_process_operation(
            context,
            sandbox,
            exec_command,
            timeout=_CODEACT_DEFAULT_TIMEOUT,
            description="daytona_codeact python",
            edit_type="codeact",
        )
        return getattr(response, "result", "") or "", sandbox, None
    except Exception as exc:
        try:
            sandbox = await _recover_sandbox(context, exc)
            response = await exec_ci_process_operation(
                context,
                sandbox,
                exec_command,
                timeout=_CODEACT_DEFAULT_TIMEOUT,
                description="daytona_codeact python",
                edit_type="codeact",
            )
            return getattr(response, "result", "") or "", sandbox, None
        except Exception as recovery_exc:
            return None, sandbox, ToolResult(
                output=f"Execution failed: {recovery_exc}",
                is_error=True,
            )


def _shell_error_result(message: str) -> ToolResult:
    return ToolResult(output=message, is_error=True)


def _ci_required_result() -> ToolResult:
    return ci_required_result(
        "daytona_codeact",
        "Command execution and Python CodeAct are disabled without CI service.",
    )


def _shell_result_error_detail(shell_result: dict[str, object]) -> str:
    return str(shell_result.get("stderr", "") or shell_result.get("stdout", "") or "")

@tool(
    name="daytona_codeact",
    description=(
        "Execute either Python code or a direct shell command in the Daytona sandbox. "
        "Use `command` for tests, builds, and verification; use `code` for multi-step "
        "Python with read()/write()/shell() helpers. stdout and stderr are already "
        "captured; do not append shell capture plumbing such as `2>&1` or `2>/dev/null`. "
        "Coordinated team commands already run from the repo root, so do not "
        "prefix them with `cd /testbed &&` or another repo-root cd."
    ),
    short_description="Run shell commands or Python in the sandbox.",
    input_model=DaytonaCodeActInput,
    output_model=DaytonaCodeActOutput,
    background="optional",
)
async def daytona_codeact(
    mode: Literal["python", "shell"] | None = None,
    code: str | None = None,
    command: str | None = None,
    timeout: int = _CODEACT_DEFAULT_TIMEOUT,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    """Execute shell commands or Python code in the Daytona sandbox."""
    resolved_mode, mode_error = _resolve_mode(mode=mode, code=code, command=command)
    if mode_error is not None:
        return ToolResult(output=mode_error, is_error=True)

    assert resolved_mode is not None

    repo_cwd = _get_cwd(context)

    if resolved_mode == "shell":
        direct_command = command or ""
        normalization_warnings: list[str] = []
        if is_coordinated_team_agent(context):
            direct_command, normalization_warnings = _normalize_team_shell_command(
                direct_command,
                repo_root=repo_cwd,
            )
        destructive_error = _destructive_git_command_error(direct_command)
        if destructive_error is None:
            destructive_error = destructive_shell_command_error(direct_command)
        if destructive_error is not None:
            return _shell_error_result(destructive_error)

    try:
        sandbox = await _require_sandbox(context)
    except Exception as exc:
        return ToolResult(output=str(exc), is_error=True)

    if get_ci_service(context) is None:
        return _ci_required_result()

    if resolved_mode == "shell":
        shell_result, sandbox, tool_error = await _run_shell_with_recovery(
            context,
            sandbox,
            command=direct_command,
            cwd=repo_cwd,
            timeout=timeout,
        )
        if tool_error is not None:
            return tool_error
        assert shell_result is not None
        exit_code = int(shell_result.get("exit_code", 1))
        return _build_tool_output(
            context=context,
            status="ok" if exit_code == 0 else "error",
            files_written=0,
            shells=[shell_result],
            script_stdout="",
            warnings=list(normalization_warnings),
            error=_shell_result_error_detail(shell_result) if exit_code != 0 else "",
        )

    stdout, sandbox, tool_error = await _execute_python_wrapper(
        context,
        sandbox,
        code=code or "",
        cwd=repo_cwd,
        repo_root=repo_cwd,
        enforce_team_shell_policy=is_coordinated_team_agent(context),
    )
    if tool_error is not None:
        return tool_error
    assert stdout is not None

    stdout, _ = _extract_exit_code(stdout, fallback_exit_code=0)
    stdout_lines = stdout.splitlines()
    script_stdout = "\n".join(stdout_lines[:-1]).strip() if stdout_lines else ""
    try:
        result_line = stdout_lines[-1] if stdout_lines else "{}"
        result = json.loads(result_line)
    except (json.JSONDecodeError, IndexError):
        return _build_tool_output(
            context=context,
            status="unknown",
            files_written=0,
            shells=[],
            script_stdout=stdout[:4000],
            warnings=["CodeAct result line was not valid JSON."],
        )

    manifest_path = str(result.get("manifest", "") or "")
    if not manifest_path:
        if result.get("status") == "error":
            return ToolResult(
                output=f"CodeAct execution error:\n{stdout[:4000]}",
                is_error=True,
            )
        return _build_tool_output(
            context=context,
            status="unknown",
            files_written=0,
            shells=[],
            script_stdout=stdout[:4000],
            warnings=["CodeAct wrapper did not return a manifest path."],
        )

    try:
        manifest_text, _ = await _read_text_file_via_exec(sandbox, manifest_path)
        manifest = json.loads(manifest_text)
    except Exception:
        if result.get("status") == "error":
            return ToolResult(
                output=_format_codeact_error(stdout=stdout),
                is_error=True,
            )
        return _build_tool_output(
            context=context,
            status="unknown",
            files_written=0,
            shells=[],
            script_stdout=stdout[:4000],
            warnings=["CodeAct completed but its manifest could not be read."],
        )

    shells = list(manifest.get("shells", []) or [])
    if result.get("status") == "error":
        manifest_error = str(manifest.get("error", "") or "")
        return ToolResult(
            output=_format_codeact_error(stdout=stdout, manifest_error=manifest_error),
            is_error=True,
            metadata={
                "status": manifest.get("status", "error"),
                "shells_run": len(shells),
            },
        )

    warnings = [str(w) for w in (manifest.get("warnings", []) or [])]
    writes = list(manifest.get("writes", []) or [])
    return _build_tool_output(
        context=context,
        status="ok",
        files_written=len(writes),
        shells=shells,
        script_stdout=script_stdout,
        warnings=warnings,
        error=str(manifest.get("error", "") or ""),
    )
