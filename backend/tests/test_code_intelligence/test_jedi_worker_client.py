"""Tests for the persistent Jedi worker client.

These tests exercise the client against a *stub* worker script rather
than real Jedi — worker-script correctness is an integration concern
covered by the live e2e suite. What we verify here:

* the client spawns exactly one process and reuses it,
* crashes trigger one automatic respawn,
* ``shutdown`` is forwarded,
* the env-var kill-switch is honoured,
* logical errors (ok=False) do not tear down a healthy process.
"""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

import pytest
from code_intelligence.lsp._jedi_worker_client import (
    ENV_FLAG,
    JediWorkerClient,
    WorkerUnavailable,
    is_enabled,
)

STUB_WORKER = dedent(
    """
    import json, sys, os
    calls = 0
    crash_after = int(os.environ.get("STUB_CRASH_AFTER", "0"))
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        req = json.loads(line)
        op = req.get("op")
        rid = req.get("id", "")
        if op == "shutdown":
            sys.stdout.write(json.dumps({"id": rid, "ok": True, "result": {"bye": True}}) + "\\n")
            sys.stdout.flush()
            break
        if op == "ping":
            # Ping is a liveness probe; don't count it toward crash budget.
            sys.stdout.write(json.dumps({"id": rid, "ok": True, "result": {"pong": True}}) + "\\n")
            sys.stdout.flush()
            continue
        calls += 1
        if crash_after and calls > crash_after:
            sys.exit(1)
        payload = {"id": rid, "ok": True, "result": {"op": op, "calls": calls, "args": req.get("args")}}
        sys.stdout.write(json.dumps(payload) + "\\n")
        sys.stdout.flush()
    """
).lstrip()


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    yield


def _write_stub(tmp_path: Path) -> str:
    p = tmp_path / "stub_worker.py"
    p.write_text(STUB_WORKER, encoding="utf-8")
    return str(p)


def _client(tmp_path: Path) -> JediWorkerClient:
    return JediWorkerClient(
        workspace_root=str(tmp_path),
        worker_script=_write_stub(tmp_path),
        python_executable=sys.executable,
    )


def test_env_flag_respected(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_FLAG, raising=False)
    assert is_enabled() is False
    cli = _client(tmp_path)
    with pytest.raises(WorkerUnavailable):
        cli.request("ping")


def test_spawn_lazy_and_reuse(tmp_path):
    cli = _client(tmp_path)
    assert cli._proc is None
    r1 = cli.request("definitions", {"x": 1})
    assert r1["calls"] == 1
    proc_after_first = cli._proc
    assert proc_after_first is not None
    r2 = cli.request("definitions", {"x": 2})
    assert r2["calls"] == 2
    assert cli._proc is proc_after_first  # reused, not respawned
    cli.shutdown()


def test_auto_respawn_once_on_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_CRASH_AFTER", "1")
    cli = _client(tmp_path)
    r1 = cli.request("definitions", {"x": 1})
    assert r1["calls"] == 1
    # Next call observes EOF → respawn → fresh stub counter = 1.
    r2 = cli.request("definitions", {"x": 2})
    assert r2["calls"] == 1
    cli.shutdown()


def test_shutdown_closes_process(tmp_path):
    cli = _client(tmp_path)
    cli.request("ping")
    proc = cli._proc
    assert proc is not None
    cli.shutdown()
    assert cli._proc is None
    assert proc.wait(timeout=2.0) == 0


def test_logical_error_does_not_tear_down_process(tmp_path):
    cli = _client(tmp_path)
    cli.request("ping")
    proc_before = cli._proc

    orig_read = cli._read_raw
    calls = {"n": 0}

    def fake_read(proc):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"id": "x", "ok": False, "error": "bad_args"}
        return orig_read(proc)

    cli._read_raw = fake_read  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="bad_args"):
        cli.request("ping")
    assert cli._proc is proc_before
    cli.shutdown()
