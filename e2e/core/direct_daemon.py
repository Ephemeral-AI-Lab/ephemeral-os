"""Trusted direct-daemon transport for workspace-session lifecycle operations."""

import json
import socket
import subprocess
import time
import uuid

from .cli import CliError, is_error, manager


ALLOWED_OPERATIONS = frozenset(
    {"create_workspace_session", "destroy_workspace_session"}
)
DAEMON_AUTH_FIELD = "_sandbox_daemon_auth_token"
DAEMON_AUTH_LABEL = "eos.auth_token"
DOCKER_INSPECT_TIMEOUT = 30


class DirectDaemonResult:
    def __init__(self, operation, args, response, elapsed_ms):
        self.args = [operation, args]
        self.json = response
        self.elapsed_ms = elapsed_ms
        self.returncode = 1 if is_error(response) else 0
        encoded = json.dumps(response, sort_keys=True)
        self.stdout = encoded if self.returncode == 0 else ""
        self.stderr = encoded if self.returncode != 0 else ""

    @property
    def ok(self):
        return self.returncode == 0


def direct_daemon(sandbox_id, operation, args=None, *, timeout=180):
    return direct_daemon_result(
        sandbox_id,
        operation,
        args,
        timeout=timeout,
    ).json


def direct_daemon_result(
    sandbox_id,
    operation,
    args=None,
    *,
    timeout=180,
    recorder=None,
):
    if operation not in ALLOWED_OPERATIONS:
        raise CliError(f"direct daemon operation is not allowed: {operation}")

    request_args = dict(args or {})
    host, port = _daemon_endpoint(sandbox_id)
    auth_token = _daemon_auth_token(sandbox_id)
    request = {
        "op": operation,
        "request_id": f"e2e-direct-daemon-{uuid.uuid4()}",
        "scope": {"kind": "sandbox", "sandbox_id": sandbox_id},
        "args": request_args,
        DAEMON_AUTH_FIELD: auth_token,
    }

    started = time.monotonic()
    with socket.create_connection((host, port), timeout=timeout) as stream:
        stream.settimeout(timeout)
        stream.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
        stream.shutdown(socket.SHUT_WR)
        with stream.makefile("rb") as reader:
            response_line = reader.readline()
    elapsed_ms = round((time.monotonic() - started) * 1000.0, 3)

    if not response_line.endswith(b"\n"):
        raise CliError("direct daemon response was not newline terminated")
    try:
        response = json.loads(response_line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise CliError("non-JSON direct daemon output") from None
    if auth_token in json.dumps(response, sort_keys=True):
        raise CliError("direct daemon response contained sandbox credentials")

    result = DirectDaemonResult(operation, request_args, response, elapsed_ms)
    if recorder is not None:
        recorder.add_command(
            {
                "cmd": [
                    "direct-daemon",
                    operation,
                    json.dumps(request_args, sort_keys=True),
                ],
                "exit_code": result.returncode,
                "elapsed_ms": elapsed_ms,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "parsed_json": response,
            }
        )
    return result


def _daemon_endpoint(sandbox_id):
    inspected = manager("inspect_sandbox", "--sandbox-id", sandbox_id)
    endpoint = inspected.get("daemon") if isinstance(inspected, dict) else None
    if not isinstance(endpoint, dict):
        raise CliError("inspect_sandbox returned no usable daemon endpoint")
    host = endpoint.get("host")
    port = endpoint.get("port")
    if (
        not isinstance(host, str)
        or not host
        or isinstance(port, bool)
        or not isinstance(port, int)
        or not 0 < port <= 65535
    ):
        raise CliError("inspect_sandbox returned no usable daemon endpoint")
    return host, port


def _daemon_auth_token(sandbox_id):
    command = [
        "docker",
        "inspect",
        "--format",
        f'{{{{index .Config.Labels "{DAEMON_AUTH_LABEL}"}}}}',
        sandbox_id,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=DOCKER_INSPECT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        raise CliError("failed to read sandbox daemon credentials") from None
    if result.returncode != 0:
        raise CliError("failed to read sandbox daemon credentials")
    token = result.stdout.strip()
    if not token or token == "<no value>":
        raise CliError("sandbox daemon credentials are unavailable")
    return token
