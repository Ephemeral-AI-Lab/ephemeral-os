"""Live Pyright probes against layer-stack snapshots.

This module intentionally tests ``pyright-langserver --stdio`` directly so the
language-server behavior can be measured apart from plugin wrapper overhead:

1. Mutations enter through public ``sandbox.api.write_file`` / ``edit_file``.
2. Each checkpoint prepares a layer-stack snapshot lowerdir.
3. Pyright is initialized with that lowerdir as ``rootUri``.
4. LSP hover / definition / diagnostics see the base repo plus new layers.
"""

from __future__ import annotations

import json
import shlex
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import sandbox.api as sandbox_api
from sandbox.api import (
    EditFileRequest,
    SandboxCaller,
    SearchReplaceEdit,
    WriteFileRequest,
)
from sandbox.host.daemon_client import DEFAULT_LAYER_STACK_ROOT, call_daemon_api
from sandbox.provider.daytona.bootstrap import bootstrap_daytona_provider
from sandbox.provider.registry import get_adapter

__all__ = [
    "PyrightLayerStackMutationReport",
    "PyrightLayerStackReport",
    "PyrightLayerStackStageReport",
    "ensure_node_pyright",
    "run_pyright_layerstack_complex_scenario",
]


_NODE_HOME = "/tmp/eos-node22"
_NODE_VERSION = "22.13.1"
_SCENARIO_DIR = "pyright_layerstack_complex"
_ROOT_CONFIG_PATH = "pyrightconfig.json"
_PACKAGE_CONFIG_PATH = f"{_SCENARIO_DIR}/pyrightconfig.json"
_INIT_PATH = f"{_SCENARIO_DIR}/__init__.py"
_MODEL_PATH = f"{_SCENARIO_DIR}/model.py"
_SERVICE_PATH = f"{_SCENARIO_DIR}/service.py"
_CONSUMER_PATH = f"{_SCENARIO_DIR}/consumer.py"


@dataclass(frozen=True)
class PyrightLayerStackMutationReport:
    name: str
    operation: str
    path: str
    duration_s: float
    timings: dict[str, float] = field(default_factory=dict)
    changed_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class PyrightLayerStackStageReport:
    name: str
    manifest_version: int
    lowerdir: str
    duration_s: float
    timings: dict[str, float] = field(default_factory=dict)
    lsp: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PyrightLayerStackReport:
    passed: bool
    duration_s: float
    install_duration_s: float
    mutations: list[PyrightLayerStackMutationReport] = field(default_factory=list)
    stages: list[PyrightLayerStackStageReport] = field(default_factory=list)
    failure: str | None = None


async def ensure_node_pyright(
    sandbox_id: str,
    *,
    node_version: str = _NODE_VERSION,
) -> float:
    """Install Node 22 + npm Pyright into ``/tmp`` when absent."""
    bootstrap_daytona_provider()
    adapter = get_adapter(sandbox_id)
    started = time.monotonic()
    result = await adapter.exec(
        sandbox_id,
        _node_pyright_install_script(node_version),
        timeout=360,
    )
    if result.exit_code != 0:
        raise RuntimeError(
            "pyright setup failed "
            f"(exit_code={result.exit_code}): {result.stderr or result.stdout}"
        )
    return time.monotonic() - started


async def run_pyright_layerstack_complex_scenario(
    sandbox_id: str,
    *,
    repo_root: str = "/testbed",
    layer_stack_root: str = DEFAULT_LAYER_STACK_ROOT,
) -> PyrightLayerStackReport:
    """Run a multi-write/edit Pyright LSP scenario against layer-stack views."""
    bootstrap_daytona_provider()
    started = time.monotonic()
    mutations: list[PyrightLayerStackMutationReport] = []
    stages: list[PyrightLayerStackStageReport] = []
    caller = SandboxCaller(
        agent_id="pyright-layerstack-live-test",
        run_id="pyright-layerstack-live-test",
        agent_run_id="pyright-layerstack-live-test",
        task_id="pyright-layerstack-live-test",
    )
    try:
        install_duration_s = await ensure_node_pyright(sandbox_id)
        await _ensure_workspace_base(sandbox_id, repo_root, layer_stack_root)

        mutations.extend(
            await _write_files(
                sandbox_id,
                repo_root,
                caller,
                {
                    _ROOT_CONFIG_PATH: _root_pyright_config_body(),
                    _PACKAGE_CONFIG_PATH: _package_pyright_config_body(),
                    _INIT_PATH: "",
                    _MODEL_PATH: _model_body_v1(),
                    _SERVICE_PATH: _service_body_v1(),
                },
            )
        )
        stages.append(
            await _snapshot_and_probe(
                sandbox_id,
                repo_root=repo_root,
                layer_stack_root=layer_stack_root,
                stage_name="initial_writes_clean",
                open_files=[_MODEL_PATH, _SERVICE_PATH],
                queries=_core_queries(),
                expected_hover_tokens=("display_name", "str"),
                expected_definition=("model.py", 7),
                expected_diagnostic_tokens=(),
                clean_diagnostic_files=["model.py", "service.py"],
            )
        )

        mutations.append(
            await _edit_file(
                sandbox_id,
                repo_root,
                caller,
                _MODEL_PATH,
                old_text=(
                    "def display_name(profile: UserProfile) -> str:\n"
                    "    return f\"{profile.first_name} {profile.last_name}\"\n"
                ),
                new_text=(
                    "def display_name(profile: UserProfile) -> int:\n"
                    "    return len(profile.first_name) + len(profile.last_name)\n"
                ),
            ),
        )
        stages.append(
            await _snapshot_and_probe(
                sandbox_id,
                repo_root=repo_root,
                layer_stack_root=layer_stack_root,
                stage_name="edit_model_type_error",
                open_files=[_MODEL_PATH, _SERVICE_PATH],
                queries=_hover_queries(),
                expected_hover_tokens=("display_name", "int"),
                expected_definition=None,
                expected_diagnostic_tokens=("str", "int"),
                clean_diagnostic_files=["model.py"],
            )
        )

        mutations.append(
            await _edit_file(
                sandbox_id,
                repo_root,
                caller,
                _SERVICE_PATH,
                old_text="name: str = display_name(profile)\n",
                new_text="name: int = display_name(profile)\n",
            )
        )
        stages.append(
            await _snapshot_and_probe(
                sandbox_id,
                repo_root=repo_root,
                layer_stack_root=layer_stack_root,
                stage_name="edit_service_clean_again",
                open_files=[_MODEL_PATH, _SERVICE_PATH],
                queries=_core_queries(),
                expected_hover_tokens=("display_name", "int"),
                expected_definition=("model.py", 7),
                expected_diagnostic_tokens=(),
                clean_diagnostic_files=["model.py", "service.py"],
            )
        )

        mutations.extend(
            await _write_files(
                sandbox_id,
                repo_root,
                caller,
                {_CONSUMER_PATH: "from .service import name\n\nfinal: str = name\n"},
            )
        )
        stages.append(
            await _snapshot_and_probe(
                sandbox_id,
                repo_root=repo_root,
                layer_stack_root=layer_stack_root,
                stage_name="new_consumer_type_error",
                open_files=[_MODEL_PATH, _SERVICE_PATH, _CONSUMER_PATH],
                queries=_hover_queries(),
                expected_hover_tokens=("display_name", "int"),
                expected_definition=None,
                expected_diagnostic_tokens=("str", "int"),
                clean_diagnostic_files=["model.py", "service.py"],
            )
        )

        mutations.append(
            await _edit_file(
                sandbox_id,
                repo_root,
                caller,
                _CONSUMER_PATH,
                old_text="final: str = name\n",
                new_text="final: int = name\n",
            )
        )
        stages.append(
            await _snapshot_and_probe(
                sandbox_id,
                repo_root=repo_root,
                layer_stack_root=layer_stack_root,
                stage_name="edit_consumer_clean_final",
                open_files=[_MODEL_PATH, _SERVICE_PATH, _CONSUMER_PATH],
                queries=_core_queries(),
                expected_hover_tokens=("display_name", "int"),
                expected_definition=("model.py", 7),
                expected_diagnostic_tokens=(),
                clean_diagnostic_files=[
                    "model.py",
                    "service.py",
                    "consumer.py",
                ],
            )
        )
    except Exception as exc:
        return PyrightLayerStackReport(
            passed=False,
            duration_s=time.monotonic() - started,
            install_duration_s=locals().get("install_duration_s", 0.0),
            mutations=mutations,
            stages=stages,
            failure=f"{type(exc).__name__}: {exc}",
        )
    return PyrightLayerStackReport(
        passed=True,
        duration_s=time.monotonic() - started,
        install_duration_s=install_duration_s,
        mutations=mutations,
        stages=stages,
    )


async def _ensure_workspace_base(
    sandbox_id: str,
    repo_root: str,
    layer_stack_root: str,
) -> None:
    response = await call_daemon_api(
        sandbox_id,
        "api.ensure_workspace_base",
        {"workspace_root": repo_root},
        timeout=120,
        layer_stack_root=layer_stack_root,
    )
    binding = response.get("binding")
    if not isinstance(binding, dict) or binding.get("workspace_root") != repo_root:
        raise AssertionError(f"workspace base not bound to {repo_root}: {response}")


async def _write_files(
    sandbox_id: str,
    repo_root: str,
    caller: SandboxCaller,
    files: dict[str, str],
) -> list[PyrightLayerStackMutationReport]:
    mutations: list[PyrightLayerStackMutationReport] = []
    for rel_path, content in files.items():
        started = time.monotonic()
        result = await sandbox_api.write_file(
            sandbox_id,
            WriteFileRequest(
                path=_abs(repo_root, rel_path),
                content=content,
                caller=caller,
                description=f"pyright layer-stack test write {rel_path}",
            ),
        )
        if not result.success:
            raise AssertionError(f"write failed for {rel_path}: {result}")
        mutations.append(
            PyrightLayerStackMutationReport(
                name=f"write:{rel_path}",
                operation="write",
                path=rel_path,
                duration_s=time.monotonic() - started,
                timings=result.timings,
                changed_paths=tuple(result.changed_paths),
            )
        )
    return mutations


async def _edit_file(
    sandbox_id: str,
    repo_root: str,
    caller: SandboxCaller,
    rel_path: str,
    *,
    old_text: str,
    new_text: str,
) -> PyrightLayerStackMutationReport:
    started = time.monotonic()
    result = await sandbox_api.edit_file(
        sandbox_id,
        EditFileRequest(
            path=_abs(repo_root, rel_path),
            edits=(SearchReplaceEdit(old_text=old_text, new_text=new_text),),
            caller=caller,
            description=f"pyright layer-stack test edit {rel_path}",
        ),
    )
    if not result.success or result.applied_edits != 1:
        raise AssertionError(f"edit failed for {rel_path}: {result}")
    return PyrightLayerStackMutationReport(
        name=f"edit:{rel_path}",
        operation="edit",
        path=rel_path,
        duration_s=time.monotonic() - started,
        timings=result.timings,
        changed_paths=tuple(result.changed_paths),
    )


async def _snapshot_and_probe(
    sandbox_id: str,
    *,
    repo_root: str,
    layer_stack_root: str,
    stage_name: str,
    open_files: list[str],
    queries: list[dict[str, Any]],
    expected_hover_tokens: tuple[str, ...],
    expected_definition: tuple[str, int],
    expected_diagnostic_tokens: tuple[str, ...],
    clean_diagnostic_files: list[str],
) -> PyrightLayerStackStageReport:
    stage_started = time.monotonic()
    snapshot = await call_daemon_api(
        sandbox_id,
        "api.prepare_workspace_snapshot",
        {"request_id": f"pyright-{stage_name}"},
        timeout=120,
        layer_stack_root=layer_stack_root,
    )
    lowerdir = str(snapshot["lowerdir"])
    lease_id = str(snapshot["lease_id"])
    try:
        await _assert_snapshot_contains_base_and_files(
            sandbox_id,
            lowerdir,
            open_files,
        )
        lsp = await _run_pyright_lsp_probe(
            sandbox_id,
            lowerdir=str(PurePosixPath(lowerdir) / _SCENARIO_DIR),
            open_files=[_scenario_rel(path) for path in open_files],
            queries=queries,
        )
        _assert_probe(
            lsp,
            expected_hover_tokens=expected_hover_tokens,
            expected_definition=expected_definition,
            expected_diagnostic_tokens=expected_diagnostic_tokens,
            clean_diagnostic_files=clean_diagnostic_files,
        )
    finally:
        await call_daemon_api(
            sandbox_id,
            "api.release_workspace_snapshot",
            {"lease_id": lease_id},
            timeout=60,
            layer_stack_root=layer_stack_root,
        )
    return PyrightLayerStackStageReport(
        name=stage_name,
        manifest_version=int(snapshot["manifest_version"]),
        lowerdir=lowerdir,
        duration_s=time.monotonic() - stage_started,
        timings=_float_timings(snapshot.get("timings")),
        lsp=lsp,
    )


async def _assert_snapshot_contains_base_and_files(
    sandbox_id: str,
    lowerdir: str,
    open_files: list[str],
) -> None:
    base_candidates = [
        f"test -d {shlex.quote(str(PurePosixPath(lowerdir) / '.git'))}",
        f"test -f {shlex.quote(str(PurePosixPath(lowerdir) / 'pyproject.toml'))}",
        f"test -f {shlex.quote(str(PurePosixPath(lowerdir) / 'setup.py'))}",
    ]
    checks = [f"( {' || '.join(base_candidates)} )"]
    checks.extend(
        f"test -f {shlex.quote(str(PurePosixPath(lowerdir) / rel_path))}"
        for rel_path in open_files
    )
    result = await get_adapter(sandbox_id).exec(
        sandbox_id,
        " && ".join(checks),
        timeout=30,
    )
    if result.exit_code != 0:
        raise AssertionError(
            "layer-stack snapshot does not contain expected base/files: "
            f"{result.stderr or result.stdout}"
        )


async def _run_pyright_lsp_probe(
    sandbox_id: str,
    *,
    lowerdir: str,
    open_files: list[str],
    queries: list[dict[str, Any]],
) -> dict[str, Any]:
    started = time.monotonic()
    result = await get_adapter(sandbox_id).exec(
        sandbox_id,
        _pyright_lsp_probe_command(
            lowerdir=lowerdir,
            open_files=open_files,
            queries=queries,
        ),
        timeout=120,
    )
    if result.exit_code != 0:
        raise AssertionError(
            "pyright LSP probe failed "
            f"(exit_code={result.exit_code}): {result.stderr or result.stdout}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"pyright probe returned non-JSON: {result.stdout!r}") from exc
    if isinstance(payload, dict):
        payload["probe_wall_s"] = time.monotonic() - started
    return payload


def _assert_probe(
    lsp: dict[str, Any],
    *,
    expected_hover_tokens: tuple[str, ...],
    expected_definition: tuple[str, int] | None,
    expected_diagnostic_tokens: tuple[str, ...],
    clean_diagnostic_files: list[str],
) -> None:
    hover_text = json.dumps(lsp.get("responses", {}).get("model_hover", {}))
    for token in expected_hover_tokens:
        if token not in hover_text:
            raise AssertionError(f"hover missing {token!r}: {hover_text}")

    if expected_definition is not None:
        expected_file, expected_line = expected_definition
        defs = lsp.get("responses", {}).get("service_definition", {}).get("result")
        if not _definition_matches(defs, expected_file, expected_line):
            raise AssertionError(
                f"definition did not point at {expected_definition}: {defs}"
            )

    diagnostics = lsp.get("diagnostics", {})
    for rel_path in clean_diagnostic_files:
        entries = diagnostics.get(rel_path, [])
        if entries:
            raise AssertionError(f"expected no diagnostics for {rel_path}: {entries}")

    if expected_diagnostic_tokens:
        diag_text = json.dumps(diagnostics, sort_keys=True)
        for token in expected_diagnostic_tokens:
            if token not in diag_text:
                raise AssertionError(
                    f"diagnostics missing {token!r}: {diag_text}"
                )


def _definition_matches(raw: Any, rel_path: str, line: int) -> bool:
    entries = raw if isinstance(raw, list) else [raw]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        uri = str(entry.get("uri") or entry.get("targetUri") or "")
        range_obj = entry.get("range") or entry.get("targetRange") or {}
        start = range_obj.get("start") if isinstance(range_obj, dict) else {}
        if (
            uri.endswith(rel_path)
            and isinstance(start, dict)
            and start.get("line") == line
        ):
            return True
    return False


def _core_queries() -> list[dict[str, Any]]:
    return [
        {
            "name": "model_hover",
            "method": "textDocument/hover",
            "file": "model.py",
            "line": 7,
            "character": 4,
        },
        {
            "name": "service_definition",
            "method": "textDocument/definition",
            "file": "service.py",
            "line": 3,
            "character": 13,
        },
    ]


def _hover_queries() -> list[dict[str, Any]]:
    return [
        {
            "name": "model_hover",
            "method": "textDocument/hover",
            "file": "model.py",
            "line": 7,
            "character": 4,
        }
    ]


def _node_pyright_install_script(node_version: str) -> str:
    return f"""\
set -eu
export PATH={shlex.quote(_NODE_HOME)}/bin:$PATH
if ! command -v node >/dev/null 2>&1; then
    arch="$(uname -m)"
    case "$arch" in
        x86_64) node_arch=x64 ;;
        aarch64|arm64) node_arch=arm64 ;;
        *) echo "unsupported arch: $arch" >&2; exit 2 ;;
    esac
    mkdir -p {shlex.quote(_NODE_HOME)}
    cd {shlex.quote(_NODE_HOME)}
    curl -fL --retry 3 --connect-timeout 20 --max-time 180 \
        "https://nodejs.org/dist/v{node_version}/node-v{node_version}-linux-${{node_arch}}.tar.xz" \
        -o node.tar.xz
    tar -xJf node.tar.xz --strip-components=1
fi
export PATH={shlex.quote(_NODE_HOME)}/bin:$PATH
node -v
npm -v
npm config set prefix {shlex.quote(_NODE_HOME)}
if ! command -v pyright-langserver >/dev/null 2>&1; then
    npm install -g pyright
fi
pyright --version
command -v pyright-langserver >/dev/null
"""


def _pyright_lsp_probe_command(
    *,
    lowerdir: str,
    open_files: list[str],
    queries: list[dict[str, Any]],
) -> str:
    return (
        "set -eu\n"
        f"export PATH={shlex.quote(_NODE_HOME)}/bin:$PATH\n"
        f"export PYRIGHT_ROOT={shlex.quote(lowerdir)}\n"
        f"export PYRIGHT_OPEN_FILES={shlex.quote(json.dumps(open_files))}\n"
        f"export PYRIGHT_QUERIES={shlex.quote(json.dumps(queries))}\n"
        "python3 - <<'PYRIGHT_LSP_PROBE'\n"
        + _PYRIGHT_LSP_PROBE_PY
        + "\nPYRIGHT_LSP_PROBE\n"
    )


def _root_pyright_config_body() -> str:
    return json.dumps(
        {
            "include": [_SCENARIO_DIR],
            "typeCheckingMode": "strict",
            "useLibraryCodeForTypes": True,
        },
        indent=2,
    ) + "\n"


def _package_pyright_config_body() -> str:
    return json.dumps(
        {
            "include": ["."],
            "typeCheckingMode": "strict",
            "useLibraryCodeForTypes": True,
        },
        indent=2,
    ) + "\n"


def _model_body_v1() -> str:
    return (
        "from dataclasses import dataclass\n"
        "\n"
        "@dataclass\n"
        "class UserProfile:\n"
        "    first_name: str\n"
        "    last_name: str\n"
        "\n"
        "def display_name(profile: UserProfile) -> str:\n"
        "    return f\"{profile.first_name} {profile.last_name}\"\n"
    )


def _service_body_v1() -> str:
    return (
        "from .model import UserProfile, display_name\n"
        "\n"
        "profile = UserProfile(first_name=\"Ada\", last_name=\"Lovelace\")\n"
        "name: str = display_name(profile)\n"
    )


def _abs(repo_root: str, rel_path: str) -> str:
    return f"{repo_root.rstrip('/')}/{rel_path.lstrip('/')}"


def _scenario_rel(rel_path: str) -> str:
    prefix = f"{_SCENARIO_DIR}/"
    if not rel_path.startswith(prefix):
        raise ValueError(f"path is not inside {_SCENARIO_DIR}: {rel_path}")
    return rel_path.removeprefix(prefix)


def _float_timings(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): float(value)
        for key, value in raw.items()
        if isinstance(value, (int, float))
    }


_PYRIGHT_LSP_PROBE_PY = r'''
import json
import os
import select
import subprocess
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

root = os.environ["PYRIGHT_ROOT"]
open_files = json.loads(os.environ["PYRIGHT_OPEN_FILES"])
queries = json.loads(os.environ["PYRIGHT_QUERIES"])
root_uri = "file://" + quote(root)
proc = subprocess.Popen(
    ["pyright-langserver", "--stdio"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=False,
    env={**os.environ, "PATH": "/tmp/eos-node22/bin:" + os.environ.get("PATH", "")},
)
next_id = 1
responses = {}
notifications = []
diagnostics = {}
server_requests = []
phase_timings = {}
read_buffer = bytearray()


def _send(payload):
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    assert proc.stdin is not None
    proc.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data)
    proc.stdin.flush()


def _read_from_stdout(timeout):
    assert proc.stdout is not None
    ready, _, _ = select.select([proc.stdout], [], [], timeout)
    if not ready:
        raise TimeoutError("timed out waiting for pyright stdout")
    chunk = os.read(proc.stdout.fileno(), 8192)
    if not chunk:
        raise TimeoutError("pyright stdout closed")
    read_buffer.extend(chunk)


def _read_msg(timeout=20.0):
    deadline = time.time() + timeout
    while b"\r\n\r\n" not in read_buffer:
        if time.time() >= deadline:
            raise TimeoutError("timed out waiting for pyright headers")
        _read_from_stdout(max(0.1, deadline - time.time()))

    header_raw, _, rest = bytes(read_buffer).partition(b"\r\n\r\n")
    read_buffer[:] = rest
    headers = {}
    for line in header_raw.split(b"\r\n"):
        if not line:
            continue
        key, value = line.decode("ascii").split(":", 1)
        headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return _read_msg(max(0.1, deadline - time.time()))

    while len(read_buffer) < length:
        if time.time() >= deadline:
            raise TimeoutError("timed out waiting for pyright body")
        _read_from_stdout(max(0.1, deadline - time.time()))

    body = bytes(read_buffer[:length])
    del read_buffer[:length]
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        raise


def _rel_from_uri(uri):
    path = unquote(urlparse(uri).path)
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return path


def _record_notification(message):
    notifications.append(message)
    if message.get("method") != "textDocument/publishDiagnostics":
        return
    params = message.get("params") or {}
    uri = str(params.get("uri") or "")
    diagnostics[_rel_from_uri(uri)] = params.get("diagnostics") or []


def _diagnostic_items(response):
    result = response.get("result") if isinstance(response, dict) else None
    if not isinstance(result, dict):
        return []
    items = result.get("items")
    return items if isinstance(items, list) else []


def _server_request_result(message):
    method = message.get("method")
    params = message.get("params") or {}
    if method == "workspace/configuration":
        items = params.get("items") if isinstance(params, dict) else []
        return [{} for _ in items] if isinstance(items, list) else []
    if method == "workspace/workspaceFolders":
        return [{"uri": root_uri, "name": "layerstack"}]
    return None


def _handle_non_target_message(message):
    if "id" in message and isinstance(message.get("method"), str):
        server_requests.append(
            {
                "id": message.get("id"),
                "method": message.get("method"),
            }
        )
        _send(
            {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": _server_request_result(message),
            }
        )
        return
    _record_notification(message)


def _wait_id(message_id, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            message = _read_msg(max(0.1, deadline - time.time()))
        except TimeoutError as exc:
            raise TimeoutError(
                f"timed out waiting for response id={message_id} "
                f"returncode={proc.poll()}"
            ) from exc
        if message.get("id") == message_id:
            return message
        _handle_non_target_message(message)
    raise TimeoutError(
        f"timed out waiting for response id={message_id} "
        f"returncode={proc.poll()}"
    )


def _request(method, params, timeout=20.0):
    global next_id
    message_id = next_id
    next_id += 1
    started = time.time()
    _send(
        {
            "jsonrpc": "2.0",
            "id": message_id,
            "method": method,
            "params": params,
        }
    )
    response = _wait_id(message_id, timeout=timeout)
    phase_timings[f"request.{method}.s"] = time.time() - started
    return response


init = _request(
    "initialize",
    {
        "processId": os.getpid(),
        "rootUri": root_uri,
        "workspaceFolders": [{"uri": root_uri, "name": "layerstack"}],
        "capabilities": {
            "workspace": {"workspaceFolders": True},
            "textDocument": {
                "diagnostic": {
                    "dynamicRegistration": False,
                    "relatedDocumentSupport": True,
                },
                "definition": {"linkSupport": True},
                "hover": {"contentFormat": ["markdown", "plaintext"]},
            }
        },
    },
    timeout=90.0,
)
_send({"jsonrpc": "2.0", "method": "initialized", "params": {}})

did_open_started = time.time()
for rel_path in open_files:
    full_path = Path(root) / rel_path
    text = full_path.read_text(encoding="utf-8")
    _send(
        {
            "jsonrpc": "2.0",
            "method": "textDocument/didOpen",
            "params": {
                "textDocument": {
                    "uri": "file://" + quote(str(full_path)),
                    "languageId": "python",
                    "version": 1,
                    "text": text,
                }
            },
        }
    )
phase_timings["did_open.s"] = time.time() - did_open_started

for query in queries:
    rel_path = query["file"]
    method = query["method"]
    full_path = Path(root) / rel_path
    responses[query["name"]] = _request(
        method,
        {
            "textDocument": {"uri": "file://" + quote(str(full_path))},
            "position": {
                "line": int(query["line"]),
                "character": int(query["character"]),
            },
        },
        timeout=60.0,
    )

diagnostics_started = time.time()
for rel_path in open_files:
    full_path = Path(root) / rel_path
    diagnostics[rel_path] = _diagnostic_items(
        _request(
            "textDocument/diagnostic",
            {"textDocument": {"uri": "file://" + quote(str(full_path))}},
            timeout=60.0,
        )
    )
phase_timings["diagnostics_pull.s"] = time.time() - diagnostics_started

shutdown = {"error": None}
try:
    shutdown = _request("shutdown", None, timeout=2.0)
except TimeoutError as exc:
    shutdown = {"error": str(exc)}
_send({"jsonrpc": "2.0", "method": "exit", "params": {}})
try:
    proc.wait(timeout=2)
except subprocess.TimeoutExpired:
    proc.kill()
    proc.wait(timeout=2)

print(
    json.dumps(
        {
            "diagnostics": diagnostics,
            "init_ok": "result" in init,
            "notifications_tail": notifications[-8:],
            "phase_timings": phase_timings,
            "responses": responses,
            "returncode": proc.returncode,
            "root_uri": root_uri,
            "server_requests": server_requests,
            "shutdown": shutdown,
            "shutdown_ok": "result" in shutdown,
        },
        sort_keys=True,
    )
)
'''
