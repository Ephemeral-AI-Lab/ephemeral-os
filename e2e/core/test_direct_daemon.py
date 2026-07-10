import json
import socket
import subprocess
import traceback

import pytest

from core import direct_daemon as daemon
from core.cli import CliError


class Recorder:
    def __init__(self):
        self.records = []

    def add_command(self, record):
        self.records.append(record)


class Reader:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def readline(self):
        return self.response


class Stream:
    def __init__(self, response):
        self.response = response
        self.sent = b""
        self.timeout = None
        self.shutdown_mode = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendall(self, payload):
        self.sent += payload

    def shutdown(self, mode):
        self.shutdown_mode = mode

    def makefile(self, mode):
        assert mode == "rb"
        return Reader(self.response)


def endpoint(*_args):
    return {"daemon": {"host": "127.0.0.1", "port": 7000}}


def fail_if_called(*_args, **_kwargs):
    raise AssertionError("side effect occurred")


def formatted_exception(error):
    return "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )


def test_allowlist_is_exact_and_rejects_before_side_effects(monkeypatch):
    assert daemon.ALLOWED_OPERATIONS == frozenset(
        {"create_workspace_session", "destroy_workspace_session"}
    )
    monkeypatch.setattr(daemon, "manager", fail_if_called)
    monkeypatch.setattr(daemon.subprocess, "run", fail_if_called)
    monkeypatch.setattr(daemon.socket, "create_connection", fail_if_called)

    with pytest.raises(CliError, match="operation is not allowed"):
        daemon.direct_daemon("sandbox-1", "file_list", {"path": "."})


def test_success_uses_inspect_and_label_without_persisting_token(monkeypatch, caplog):
    secret = "sentinel-daemon-secret"
    calls = {}
    stream = Stream(
        b'{"workspace_session_id":"ws-1","finalize_policy":"no_op"}\n'
    )
    recorder = Recorder()

    def inspect(*args, **kwargs):
        calls["inspect"] = (args, kwargs)
        return endpoint()

    def run(command, **kwargs):
        calls["docker"] = (command, kwargs)
        return subprocess.CompletedProcess(command, 0, secret + "\n", "")

    def connect(address, **kwargs):
        calls["connect"] = (address, kwargs)
        return stream

    monkeypatch.setattr(daemon, "manager", inspect)
    monkeypatch.setattr(daemon.subprocess, "run", run)
    monkeypatch.setattr(daemon.socket, "create_connection", connect)

    result = daemon.direct_daemon_result(
        "sandbox-1",
        "create_workspace_session",
        {"network_profile": "restricted"},
        timeout=12,
        recorder=recorder,
    )

    assert calls["inspect"] == (
        ("inspect_sandbox", "--sandbox-id", "sandbox-1"),
        {},
    )
    assert calls["docker"] == (
        [
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "eos.auth_token"}}',
            "sandbox-1",
        ],
        {"capture_output": True, "text": True, "timeout": 30},
    )
    assert calls["connect"] == (("127.0.0.1", 7000), {"timeout": 12})
    assert stream.timeout == 12
    assert stream.shutdown_mode == socket.SHUT_WR
    request = json.loads(stream.sent)
    assert request[daemon.DAEMON_AUTH_FIELD] == secret
    assert request["op"] == "create_workspace_session"
    assert request["scope"] == {"kind": "sandbox", "sandbox_id": "sandbox-1"}
    assert request["args"] == {"network_profile": "restricted"}
    assert "_sandbox_gateway_auth_token" not in request
    assert "_stream_logs" not in request
    assert result.ok
    assert result.json["workspace_session_id"] == "ws-1"

    persisted = json.dumps(
        {
            "args": result.args,
            "json": result.json,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "records": recorder.records,
            "logs": caplog.text,
        },
        sort_keys=True,
    )
    assert secret not in persisted


@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
def test_docker_failures_do_not_leak_secret(
    monkeypatch,
    caplog,
    failure,
):
    secret = "sentinel-docker-failure-secret"
    recorder = Recorder()

    def run(command, **_kwargs):
        if failure == "nonzero":
            return subprocess.CompletedProcess(command, 1, secret, secret)
        raise subprocess.TimeoutExpired(
            command,
            timeout=30,
            output=secret,
            stderr=secret,
        )

    monkeypatch.setattr(daemon, "manager", endpoint)
    monkeypatch.setattr(daemon.subprocess, "run", run)
    monkeypatch.setattr(daemon.socket, "create_connection", fail_if_called)

    with pytest.raises(CliError) as raised:
        daemon.direct_daemon_result(
            "sandbox-1",
            "create_workspace_session",
            recorder=recorder,
        )

    exposed = "\n".join(
        [str(raised.value), formatted_exception(raised.value), caplog.text]
    )
    assert secret not in exposed
    assert recorder.records == []


@pytest.mark.parametrize("label_output", ["", "<no value>\n"])
def test_missing_docker_label_fails_before_socket(monkeypatch, label_output):
    monkeypatch.setattr(daemon, "manager", endpoint)
    monkeypatch.setattr(
        daemon.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, label_output, ""
        ),
    )
    monkeypatch.setattr(daemon.socket, "create_connection", fail_if_called)

    with pytest.raises(CliError, match="credentials are unavailable"):
        daemon.direct_daemon("sandbox-1", "destroy_workspace_session")


def test_non_json_daemon_output_is_not_echoed(monkeypatch, caplog):
    secret = "sentinel-malformed-daemon-output"
    stream = Stream((secret + "\n").encode())
    recorder = Recorder()

    monkeypatch.setattr(daemon, "manager", endpoint)
    monkeypatch.setattr(
        daemon.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, "daemon-token\n", ""
        ),
    )
    monkeypatch.setattr(
        daemon.socket,
        "create_connection",
        lambda *_args, **_kwargs: stream,
    )

    with pytest.raises(CliError) as raised:
        daemon.direct_daemon_result(
            "sandbox-1",
            "create_workspace_session",
            recorder=recorder,
        )

    exposed = "\n".join(
        [str(raised.value), formatted_exception(raised.value), caplog.text]
    )
    assert secret not in exposed
    assert recorder.records == []


def test_daemon_response_cannot_persist_auth_token(monkeypatch, caplog):
    secret = "sentinel-echoed-daemon-token"
    stream = Stream(json.dumps({"message": secret}).encode() + b"\n")
    recorder = Recorder()

    monkeypatch.setattr(daemon, "manager", endpoint)
    monkeypatch.setattr(
        daemon.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, secret + "\n", ""
        ),
    )
    monkeypatch.setattr(
        daemon.socket,
        "create_connection",
        lambda *_args, **_kwargs: stream,
    )

    with pytest.raises(CliError, match="response contained sandbox credentials"):
        daemon.direct_daemon_result(
            "sandbox-1",
            "create_workspace_session",
            recorder=recorder,
        )

    assert recorder.records == []
    assert secret not in caplog.text
