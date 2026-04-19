"""CodeAct tool - shell or Python execution in the Daytona sandbox."""

from __future__ import annotations

import base64
import json
import shlex
import uuid
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field
from pydantic.json_schema import GenerateJsonSchema

from code_intelligence.tuning import CODE_INTELLIGENCE_TUNING
from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.ci_runtime import ci_required_result, get_ci_service
from tools.core.decorator import tool
from tools.daytona_toolkit._commit import FileChangeResult, submit_codeact_cmd
from tools.daytona_toolkit._daytona_utils import (
    _extract_exit_code,
    _format_shell_stdout,
    _get_cwd,
    _read_text_file_via_exec,
    _recover_sandbox,
    _require_sandbox,
    _write_text_file_via_exec,
    _wrap_bash_command,
)
from tools.daytona_toolkit.hooks.prehook.codeact_file_edit_policy import (
    FILE_EDIT_POLICY_MESSAGE,
    should_disable_codeact_file_edits,
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
    files_written: int = Field(
        ...,
        description="Number of helper or audited process file writes observed.",
    )
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



BuildToolOutput = Callable[..., ToolResult]


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
    if "daytona_codeact is for runtime commands" in detail:
        lines.append(
            "Use `daytona_edit_file`, `daytona_write_file`, "
            "`daytona_rename_symbol`, `daytona_delete_file`, or "
            "`daytona_move_file` for file changes."
        )
    return "\n".join(lines)


def _python_literal_or_none(value: str | None) -> str:
    if not value or str(value).strip().lower() == "none":
        return "None"
    return json.dumps(value)


_WRAPPER_TEMPLATE = r'''
import base64, hashlib, importlib, io, json, os, pathlib, re, shlex, subprocess, traceback

_RUN_ID = "{run_id}"
_CODE_PREVIEW = {code_preview}
_MANIFEST = {{"reads": [], "writes": [], "shells": [], "status": "ok", "error": ""}}
_CODEACT_CWD = {codeact_cwd}
_DISABLE_CODEACT_FILE_EDITS = {disable_codeact_file_edits}
_FILE_EDIT_POLICY_MESSAGE = {codeact_file_edit_policy_message}
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
_CODEACT_SHELL_FILE_EDIT_PATTERNS = (
    (
        re.compile(
            r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:g?sed|sed)\b(?:(?![;&|]).)*\s-[A-Za-z]*i(?:\b|[=.])",
            flags=re.IGNORECASE | re.DOTALL,
        ),
        "in-place sed",
    ),
    (
        re.compile(
            r"(?:^|[;&|]\s*)perl\b(?:(?![;&|]).)*\s-\S*i\S*",
            flags=re.IGNORECASE | re.DOTALL,
        ),
        "in-place perl",
    ),
    (
        re.compile(
            r"(?:^|[;&|]\s*)tee\b(?:\s+-[A-Za-z]+)*\s+(?!/dev/null(?:\s|$))\S+",
            flags=re.IGNORECASE,
        ),
        "tee file write",
    ),
    (
        re.compile(
            r"(?:^|[;&|]\s*)(?:touch|truncate|cp|mv|install|rm|rmdir)\b"
            r"|(?:^|[;&|]\s*)git\s+(?:rm|mv)\b",
            flags=re.IGNORECASE,
        ),
        "filesystem mutation command",
    ),
    (
        re.compile(
            r"(?:^|[;&|]\s*)python(?:3(?:\.\d+)?)?\b.*"
            r"(?:write_text|write_bytes|"
            r"\bopen\s*\([^)]*,\s*['\"][^'\"]*[wax+]|"
            r"\bshutil\.|\bos\.(?:remove|unlink|rename|replace)|"
            r"\bPath\s*\([^)]*\)\.(?:touch|unlink|rename|replace|mkdir))",
            flags=re.IGNORECASE | re.DOTALL,
        ),
        "inline Python file mutation",
    ),
)
_CODEACT_SHELL_OUTPUT_REDIRECTION_PATTERN = re.compile(
    r"(?<![<>&])(?:\b\d*)?(?:>>?|&>)\s*(?!&\d\b)(?!/dev/null(?:\s|$))\S+",
    flags=re.IGNORECASE,
)

def _mask_shell_quoted_text(command):
    out = []
    quote = None
    escaped = False
    for char in command:
        if escaped:
            out.append("x" if quote else char)
            escaped = False
            continue
        if char == "\\":
            out.append("x" if quote else char)
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
                out.append(char)
            else:
                out.append("x" if not char.isspace() else char)
            continue
        if char in {{"'", '"'}}:
            quote = char
        out.append(char)
    return "".join(out)

def _codeact_shell_file_edit_error(command):
    if _CODEACT_SHELL_OUTPUT_REDIRECTION_PATTERN.search(_mask_shell_quoted_text(command or "")):
        return "shell output redirection"
    for pattern, kind in _CODEACT_SHELL_FILE_EDIT_PATTERNS:
        if pattern.search(command or ""):
            return kind
    return None

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
    if _DISABLE_CODEACT_FILE_EDITS:
        raise RuntimeError(_FILE_EDIT_POLICY_MESSAGE)
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
    if _DISABLE_CODEACT_FILE_EDITS:
        edit_kind = _codeact_shell_file_edit_error(command)
        if edit_kind:
            _block_shell_command(
                command,
                f"{{_FILE_EDIT_POLICY_MESSAGE}} Detected {{edit_kind}}.",
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
_real_open = _builtins_mod.open
_real_io_open = io.open
_real_path_open = pathlib.Path.open

def _is_write_mode(mode):
    text = str(mode or "r")
    return any(flag in text for flag in ("w", "a", "x", "+"))

def _guarded_open(file, mode="r", *args, **kwargs):
    if _DISABLE_CODEACT_FILE_EDITS and _is_write_mode(mode):
        raise RuntimeError(_FILE_EDIT_POLICY_MESSAGE)
    return _real_open(file, mode, *args, **kwargs)

def _guarded_io_open(file, mode="r", *args, **kwargs):
    if _DISABLE_CODEACT_FILE_EDITS and _is_write_mode(mode):
        raise RuntimeError(_FILE_EDIT_POLICY_MESSAGE)
    return _real_io_open(file, mode, *args, **kwargs)

def _guarded_path_open(self, mode="r", *args, **kwargs):
    if _DISABLE_CODEACT_FILE_EDITS and _is_write_mode(mode):
        raise RuntimeError(_FILE_EDIT_POLICY_MESSAGE)
    return _real_path_open(self, mode, *args, **kwargs)

def _guarded_import(name, *args, **kwargs):
    top = name.split(".")[0]
    if top in _BLOCKED_MODULES:
        raise ImportError(
            f"import {{name!r}} is blocked in codeact. "
            "Use daytona_codeact shell mode for commands and read() for file reads; "
            "use Daytona edit/write tools for file changes."
        )
    return _real_import(name, *args, **kwargs)

_sandbox_builtins = dict(vars(_builtins_mod))
_sandbox_builtins["__import__"] = _guarded_import
_sandbox_builtins["open"] = _guarded_open

_real_import_module = importlib.import_module

def _guarded_import_module(name, package=None):
    top = name.split(".")[0]
    if top in _BLOCKED_MODULES:
        raise ImportError(
            f"import {{name!r}} is blocked in codeact. "
            "Use daytona_codeact shell mode for commands and read() for file reads; "
            "use Daytona edit/write tools for file changes."
        )
    return _real_import_module(name, package)

importlib.import_module = _guarded_import_module

def _blocked_file_edit_call(*args, **kwargs):
    raise RuntimeError(_FILE_EDIT_POLICY_MESSAGE)

if _DISABLE_CODEACT_FILE_EDITS:
    for _name in (
        "remove",
        "unlink",
        "rename",
        "replace",
        "mkdir",
        "makedirs",
        "rmdir",
        "removedirs",
        "chmod",
        "chown",
        "truncate",
    ):
        if hasattr(os, _name):
            setattr(os, _name, _blocked_file_edit_call)
    for _name in (
        "write_text",
        "write_bytes",
        "touch",
        "unlink",
        "rename",
        "replace",
        "mkdir",
        "chmod",
    ):
        if hasattr(pathlib.Path, _name):
            setattr(pathlib.Path, _name, _blocked_file_edit_call)
    pathlib.Path.open = _guarded_path_open
    io.open = _guarded_io_open

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
    disable_codeact_file_edits: bool,
    run_id: str,
    cwd: str | None,
) -> str:
    code_b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
    return _WRAPPER_TEMPLATE.format(
        run_id=run_id,
        code_preview=json.dumps(code),
        code_b64=code_b64,
        codeact_cwd=_python_literal_or_none(cwd),
        disable_codeact_file_edits="True" if disable_codeact_file_edits else "False",
        codeact_file_edit_policy_message=json.dumps(FILE_EDIT_POLICY_MESSAGE),
        codeact_default_timeout=_CODEACT_DEFAULT_TIMEOUT,
    )


def _build_exec_command(script_path: str, *, cwd: str | None) -> str:
    command = f"python3 {script_path}"
    if cwd:
        command = f"cd {json.dumps(cwd)} && {command}"
    return _wrap_bash_command(command)


async def _execute_python_wrapper(
    context: ToolExecutionContext,
    sandbox: object,
    *,
    code: str,
    cwd: str | None,
    disable_codeact_file_edits: bool,
    build_tool_output: BuildToolOutput,
) -> tuple[str | None, object, ToolResult | None, list[str]]:
    run_id = uuid.uuid4().hex[:8]
    wrapper = _build_wrapper(
        code,
        run_id=run_id,
        cwd=cwd,
        disable_codeact_file_edits=disable_codeact_file_edits,
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
            await _write_text_file_via_exec(
                sandbox,
                script_path,
                wrapper,
                timeout=_CODEACT_WRITE_TIMEOUT,
            )
        except Exception as recovery_exc:
            return (
                None,
                sandbox,
                ToolResult(
                    output=f"Failed to upload script: {recovery_exc}",
                    is_error=True,
                ),
                [],
            )

    if get_ci_service(context) is None:
        return (
            None,
            sandbox,
            ci_required_result(
                "daytona_codeact",
                "Python CodeAct requires svc.cmd (code intelligence service unavailable).",
            ),
            [],
        )

    async def _submit(active_sandbox: object) -> FileChangeResult[Any]:
        return await submit_codeact_cmd(
            context,
            command=exec_command,
            description="daytona_codeact python",
            timeout=_CODEACT_DEFAULT_TIMEOUT,
            sandbox=active_sandbox,
        )

    try:
        change = await _submit(sandbox)
    except Exception as exc:
        try:
            sandbox = await _recover_sandbox(context, exc)
            change = await _submit(sandbox)
        except Exception as recovery_exc:
            return (
                None,
                sandbox,
                ToolResult(
                    output=f"Execution failed: {recovery_exc}",
                    is_error=True,
                ),
                [],
            )
    changed_paths = list(change.changed_paths)
    if not change.success:
        error_detail = f"git workspace commit aborted: {change.conflict_reason or 'unknown reason'}"
        return (
            None,
            sandbox,
            build_tool_output(
                context=context,
                status="error",
                files_written=len(changed_paths),
                shells=[],
                script_stdout="",
                warnings=[],
                error=error_detail,
                changed_paths=changed_paths,
                ambient_changed_paths=list(change.ambient_changed_paths),
            ),
            changed_paths,
        )
    return (
        getattr(change.raw, "result", "") or "",
        sandbox,
        None,
        changed_paths,
    )



async def _read_codeact_manifest(sandbox: object, manifest_path: str) -> str:
    manifest_text, _ = await _read_text_file_via_exec(sandbox, manifest_path)
    return manifest_text


async def _execute_python_codeact(
    context: ToolExecutionContext,
    sandbox: object,
    *,
    code: str,
    cwd: str | None,
    disable_codeact_file_edits: bool,
    build_tool_output: BuildToolOutput,
    format_codeact_error: Callable[..., str],
    extract_exit_code: Callable[..., tuple[str, int]],
    files_written_count: Callable[[list[object], list[str]], int],
) -> ToolResult:
    stdout, sandbox, tool_error, changed_paths = await _execute_python_wrapper(
        context,
        sandbox,
        code=code,
        cwd=cwd,
        disable_codeact_file_edits=disable_codeact_file_edits,
        build_tool_output=build_tool_output,
    )
    if tool_error is not None:
        return tool_error
    assert stdout is not None

    stdout, _ = extract_exit_code(stdout, fallback_exit_code=0)
    stdout_lines = stdout.splitlines()
    script_stdout = "\n".join(stdout_lines[:-1]).strip() if stdout_lines else ""
    try:
        result_line = stdout_lines[-1] if stdout_lines else "{}"
        result = json.loads(result_line)
    except (json.JSONDecodeError, IndexError):
        return build_tool_output(
            context=context,
            status="unknown",
            files_written=0,
            shells=[],
            script_stdout=stdout[:4000],
            warnings=["CodeAct result line was not valid JSON."],
            changed_paths=changed_paths,
        )

    manifest_path = str(result.get("manifest", "") or "")
    if not manifest_path:
        if result.get("status") == "error":
            return ToolResult(
                output=f"CodeAct execution error:\n{stdout[:4000]}",
                is_error=True,
            )
        return build_tool_output(
            context=context,
            status="unknown",
            files_written=0,
            shells=[],
            script_stdout=stdout[:4000],
            warnings=["CodeAct wrapper did not return a manifest path."],
            changed_paths=changed_paths,
        )

    try:
        manifest_text = await _read_codeact_manifest(sandbox, manifest_path)
        manifest = json.loads(manifest_text)
    except Exception:
        if result.get("status") == "error":
            return ToolResult(
                output=format_codeact_error(stdout=stdout),
                is_error=True,
            )
        return build_tool_output(
            context=context,
            status="unknown",
            files_written=0,
            shells=[],
            script_stdout=stdout[:4000],
            warnings=["CodeAct completed but its manifest could not be read."],
            changed_paths=changed_paths,
        )

    shells = list(manifest.get("shells", []) or [])
    if result.get("status") == "error":
        manifest_error = str(manifest.get("error", "") or "")
        return ToolResult(
            output=format_codeact_error(stdout=stdout, manifest_error=manifest_error),
            is_error=True,
            metadata={
                "status": manifest.get("status", "error"),
                "shells_run": len(shells),
                "changed_paths": changed_paths,
            },
        )

    warnings = [str(w) for w in (manifest.get("warnings", []) or [])]
    writes = list(manifest.get("writes", []) or [])
    return build_tool_output(
        context=context,
        status="ok",
        files_written=files_written_count(writes, changed_paths),
        shells=shells,
        script_stdout=script_stdout,
        warnings=warnings,
        error=str(manifest.get("error", "") or ""),
        changed_paths=changed_paths,
    )

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
    attribute_changes: bool,
) -> dict[str, object]:
    if get_ci_service(context) is None:
        raise RuntimeError("Code intelligence service is unavailable")

    wrapped_command = command if not cwd else f"cd {shlex.quote(cwd)} && {command}"
    change = await submit_codeact_cmd(
        context,
        command=_wrap_bash_command(wrapped_command),
        description="daytona_codeact shell",
        timeout=timeout,
        sandbox=sandbox,
        attribute_changes=attribute_changes,
    )
    response = change.raw
    stdout = getattr(response, "result", "") or ""
    fallback_exit_code = getattr(response, "exit_code", None)
    cleaned_stdout, exit_code = _extract_exit_code(
        stdout,
        fallback_exit_code=fallback_exit_code,
    )
    formatted_stdout = _format_shell_stdout(cleaned_stdout, exit_code=exit_code)
    return {
        "command": command,
        "stdout": formatted_stdout,
        "stderr": formatted_stdout if exit_code != 0 else "",
        "exit_code": exit_code,
        "changed_paths": list(change.changed_paths),
        "ambient_changed_paths": list(change.ambient_changed_paths),
        "audit_success": bool(change.success),
        "audit_conflict_reason": change.conflict_reason,
    }


async def _run_shell_with_recovery(
    context: ToolExecutionContext,
    sandbox: object,
    *,
    command: str,
    cwd: str | None,
    timeout: int,
    attribute_changes: bool,
) -> tuple[dict[str, object] | None, object, ToolResult | None]:
    try:
        return (
            await _exec_shell_command(
                context,
                sandbox,
                command=command,
                cwd=cwd,
                timeout=timeout,
                attribute_changes=attribute_changes,
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
                    attribute_changes=attribute_changes,
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
    changed_paths: list[str] | None = None,
    ambient_changed_paths: list[str] | None = None,
) -> ToolResult:
    shell_summaries: list[str] = []
    shell_outputs: list[dict[str, object]] = []
    for shell_result in shells[:3]:
        command = str(shell_result.get("command", "") or "")
        exit_code = shell_result.get("exit_code", "?")
        try:
            exit_code_int = int(exit_code)
        except (TypeError, ValueError):
            exit_code_int = 1
        stdout = _format_shell_stdout(
            str(shell_result.get("stdout", "") or ""),
            exit_code=exit_code_int,
        )
        stderr = _format_shell_stdout(
            str(shell_result.get("stderr", "") or ""),
            exit_code=exit_code_int,
        )
        shell_summaries.append(f"$ {command[:80]} -> exit {exit_code}")
        shell_outputs.append(
            {
                "command": command,
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
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
                "script_stdout": _format_shell_stdout(script_stdout, exit_code=0),
                "warnings": warnings,
                "error": error[:500] if error else "",
            }
        ),
        is_error=is_error,
        metadata={
            "status": status,
            "files_written": files_written,
            "shells_run": len(shells),
            "changed_paths": list(changed_paths or []),
            "ambient_changed_paths": list(ambient_changed_paths or []),
        },
    )



def _ci_required_result() -> ToolResult:
    return ci_required_result(
        "daytona_codeact",
        "Command execution and Python CodeAct are disabled without CI service.",
    )


def _shell_result_error_detail(shell_result: dict[str, object]) -> str:
    return str(shell_result.get("stderr", "") or shell_result.get("stdout", "") or "")


def _changed_paths_from_shell(shell_result: dict[str, object]) -> list[str]:
    raw = shell_result.get("changed_paths")
    if not isinstance(raw, list):
        return []
    return sorted({str(path) for path in raw if str(path or "").strip()})


def _ambient_changed_paths_from_shell(shell_result: dict[str, object]) -> list[str]:
    raw = shell_result.get("ambient_changed_paths")
    if not isinstance(raw, list):
        return []
    return sorted({str(path) for path in raw if str(path or "").strip()})


def _files_written_count(
    manifest_writes: list[object],
    changed_paths: list[str],
) -> int:
    if not manifest_writes:
        return len(changed_paths)

    manifest_paths = {
        str(item.get("path") or "")
        for item in manifest_writes
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    }
    audited_only = [path for path in changed_paths if path not in manifest_paths]
    return len(manifest_writes) + len(audited_only)




@tool(
    name="daytona_codeact",
    description=(
        "Execute either Python code or a direct shell command in the Daytona sandbox. "
        "Use `command` for tests, builds, and verification; use `code` for multi-step "
        "Python with read()/shell() helpers. Do not use CodeAct for file edits; "
        "use daytona_edit_file, daytona_write_file, daytona_rename_symbol, "
        "daytona_delete_file, or daytona_move_file instead. "
        "Never include shell or Python cleanup/mutation tokens such as `rm`, `mv`, "
        "`unlink`, `os.remove`, `Path.unlink`, `shutil.rmtree`, `shutil.move`, "
        "`os.rename`, `git rm`, or `git mv`; repo deletions and path moves must use "
        "daytona_delete_file/daytona_move_file. "
        "stdout and stderr are already "
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
    disable_codeact_file_edits = should_disable_codeact_file_edits(context)

    # Pre-flight policy (shell normalization, destructive-git/shell blocks,
    # file-edit side-channel blocks) is enforced by pre-phase platform hooks.
    # The in-sandbox wrapper applies the
    # same checks in a second line of defense inside the sandbox process.
    if resolved_mode == "shell":
        direct_command = command or ""

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
            attribute_changes=not disable_codeact_file_edits,
        )
        if tool_error is not None:
            return tool_error
        assert shell_result is not None
        exit_code = int(shell_result.get("exit_code", 1))
        audit_success = bool(shell_result.get("audit_success", True))
        audit_conflict = shell_result.get("audit_conflict_reason") or ""
        changed_paths = _changed_paths_from_shell(shell_result)
        ambient_changed_paths = _ambient_changed_paths_from_shell(shell_result)
        is_error = exit_code != 0 or not audit_success
        if not audit_success and exit_code == 0:
            error_detail = (
                f"git workspace commit aborted: {audit_conflict or 'unknown reason'}"
            )
        elif exit_code != 0:
            error_detail = _shell_result_error_detail(shell_result)
        else:
            error_detail = ""
        return _build_tool_output(
            context=context,
            status="ok" if not is_error else "error",
            files_written=len(changed_paths),
            shells=[shell_result],
            script_stdout="",
            warnings=[],
            error=error_detail,
            changed_paths=changed_paths,
            ambient_changed_paths=ambient_changed_paths,
        )

    return await _execute_python_codeact(
        context,
        sandbox,
        code=code or "",
        cwd=repo_cwd,
        disable_codeact_file_edits=disable_codeact_file_edits,
        build_tool_output=_build_tool_output,
        format_codeact_error=_format_codeact_error,
        extract_exit_code=_extract_exit_code,
        files_written_count=_files_written_count,
    )
