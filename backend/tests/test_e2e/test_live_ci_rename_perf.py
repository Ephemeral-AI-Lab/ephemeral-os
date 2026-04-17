"""Live Daytona trace + performance test for Jedi/LSP-backed CI operations.

This test intentionally prints detailed trace logs. It is meant for
debugging live sandbox latency and behavior after changes to the Jedi/LSP
stack and the rename tools.

Run with:
    uv run pytest backend/tests/test_e2e/test_live_ci_rename_perf.py -m live -v -s
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shlex
import statistics
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, TypeVar

import pytest
from dotenv import load_dotenv

from code_intelligence.routing.service import CodeIntelligenceService
from tools.ci_toolkit.rename_tool import ci_rename, ci_rename_symbol
from tools.core.base import ToolExecutionContext
from tools.daytona_toolkit._daytona_utils import _extract_exit_code, _wrap_bash_command

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_PROJECT_ROOT / ".env")

pytestmark = [pytest.mark.e2e, pytest.mark.live]

_T = TypeVar("_T")
_TERM_NOISE = re.compile(r"\x1b\[3J.*$", re.S)
_MAX_COMMAND_CHARS = 260


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


@dataclass
class TraceEvent:
    seq: int
    op: str
    duration_ms: float
    exit_code: int | None
    stdout_chars: int
    command: str
    error: str = ""


class TraceLog:
    """Collects sandbox IO events and prints compact debugging summaries."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def mark(self) -> int:
        return len(self.events)

    def record(
        self,
        *,
        op: str,
        command: Any,
        started_at: float,
        result: Any = None,
        error: BaseException | None = None,
    ) -> None:
        stdout = _clean_stdout(getattr(result, "result", "") or "") if result is not None else ""
        self.events.append(
            TraceEvent(
                seq=len(self.events) + 1,
                op=op,
                duration_ms=round((time.perf_counter() - started_at) * 1000, 3),
                exit_code=getattr(result, "exit_code", None) if result is not None else None,
                stdout_chars=len(stdout),
                command=_compact(str(command)),
                error=f"{type(error).__name__}: {error}" if error is not None else "",
            )
        )

    def summary_since(self, start: int) -> dict[str, Any]:
        events = self.events[start:]
        durations = [event.duration_ms for event in events]
        slowest = sorted(events, key=lambda event: event.duration_ms, reverse=True)[:5]
        return {
            "exec_count": len(events),
            "exec_total_ms": round(sum(durations), 3),
            "exec_max_ms": round(max(durations), 3) if durations else 0.0,
            "exec_median_ms": round(statistics.median(durations), 3) if durations else 0.0,
            "slowest_exec": [asdict(event) for event in slowest],
        }

    def print_all(self) -> None:
        for event in self.events:
            print("[ci-lsp-live-exec] " + json.dumps(asdict(event), sort_keys=True), flush=True)

    def print_summary(self) -> None:
        durations = [event.duration_ms for event in self.events]
        payload = {
            "total_exec_count": len(self.events),
            "total_exec_ms": round(sum(durations), 3),
            "median_exec_ms": round(statistics.median(durations), 3) if durations else 0.0,
            "p95_exec_ms": _percentile(durations, 95),
            "slowest_exec": [
                asdict(event)
                for event in sorted(
                    self.events, key=lambda event: event.duration_ms, reverse=True
                )[:10]
            ],
        }
        print("[ci-lsp-live-summary] " + json.dumps(payload, sort_keys=True), flush=True)


class _TracingProcess:
    def __init__(self, real_process: Any, trace: TraceLog):
        self._real = real_process
        self._trace = trace

    def exec(self, command: str, *args: Any, **kwargs: Any) -> Any:
        started_at = time.perf_counter()
        try:
            result = self._real.exec(command, *args, **kwargs)
        except Exception as exc:
            self._trace.record(
                op="process.exec",
                command=command,
                started_at=started_at,
                error=exc,
            )
            raise
        self._trace.record(
            op="process.exec",
            command=command,
            started_at=started_at,
            result=result,
        )
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _TracingFs:
    def __init__(self, real_fs: Any, trace: TraceLog):
        self._real = real_fs
        self._trace = trace

    def download_file(self, *args: Any, **kwargs: Any) -> Any:
        started_at = time.perf_counter()
        try:
            result = self._real.download_file(*args, **kwargs)
        except Exception as exc:
            self._trace.record(
                op="fs.download_file",
                command=args[0] if args else "",
                started_at=started_at,
                error=exc,
            )
            raise
        self._trace.record(
            op="fs.download_file",
            command=args[0] if args else "",
            started_at=started_at,
            result=SimpleNamespace(result=result, exit_code=0),
        )
        return result

    def upload_file(self, *args: Any, **kwargs: Any) -> Any:
        started_at = time.perf_counter()
        try:
            result = self._real.upload_file(*args, **kwargs)
        except Exception as exc:
            self._trace.record(
                op="fs.upload_file",
                command=args[-1] if args else "",
                started_at=started_at,
                error=exc,
            )
            raise
        self._trace.record(
            op="fs.upload_file",
            command=args[-1] if args else "",
            started_at=started_at,
            result=SimpleNamespace(result=result, exit_code=0),
        )
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _TracingSandboxWrapper:
    def __init__(self, raw_sandbox: Any, trace: TraceLog):
        self._raw = raw_sandbox
        self.process = _TracingProcess(raw_sandbox.process, trace)
        self.fs = _TracingFs(raw_sandbox.fs, trace)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


class _AsyncFs:
    def __init__(self, real_fs: Any):
        self._real = real_fs

    async def upload_file(self, *args: Any, **kwargs: Any) -> Any:
        return self._real.upload_file(*args, **kwargs)

    async def download_file(self, *args: Any, **kwargs: Any) -> Any:
        return self._real.download_file(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _AsyncProcess:
    def __init__(self, real_process: Any):
        self._real = real_process

    async def exec(self, *args: Any, **kwargs: Any) -> Any:
        response = self._real.exec(*args, **kwargs)
        stdout = _clean_stdout(getattr(response, "result", "") or "")
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
    traced_sandbox: Any
    async_sandbox: Any
    trace: TraceLog
    home: str
    root_dir: str

    def exec(self, command: str, *, timeout: int = 180) -> tuple[int, str]:
        response = self.raw_sandbox.process.exec(_wrap_bash_command(command), timeout=timeout)
        raw = _clean_stdout(getattr(response, "result", "") or "")
        cleaned, exit_code = _extract_exit_code(
            raw,
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        return exit_code, cleaned

    def exec_checked(self, command: str, *, timeout: int = 180) -> str:
        exit_code, stdout = self.exec(command, timeout=timeout)
        if exit_code != 0:
            detail = stdout.strip() or f"exit {exit_code}"
            raise AssertionError(f"Sandbox command failed: {detail}")
        return stdout

    def write_file(self, path: str, content: str) -> None:
        payload = base64.b64encode(content.encode("utf-8")).decode("ascii")
        self.exec_checked(
            f"mkdir -p {shlex.quote(os.path.dirname(path) or '.')} && "
            f"echo {shlex.quote(payload)} | base64 -d > {shlex.quote(path)}"
        )

    def read_file(self, path: str) -> str:
        return self.exec_checked(f"cat {shlex.quote(path)}")


@pytest.fixture
def live_rename_env() -> LiveRenameEnv:
    if not HAS_DAYTONA:
        pytest.skip("Daytona credentials not configured")

    from sandbox.testing import create_test_sandbox, delete_test_sandbox, get_sandbox_service

    info = create_test_sandbox(name="ci-lsp-perf-live")
    sandbox_id = info["id"]
    try:
        sandbox_svc = get_sandbox_service()
        raw_sandbox = sandbox_svc.get_sandbox_object(sandbox_id)
        home_resp = raw_sandbox.process.exec("pwd", timeout=10)
        home = (getattr(home_resp, "result", "") or "").strip() or "/home/daytona"
        trace = TraceLog()
        traced = _TracingSandboxWrapper(raw_sandbox, trace)
        env = LiveRenameEnv(
            sandbox_id=sandbox_id,
            raw_sandbox=raw_sandbox,
            traced_sandbox=traced,
            async_sandbox=_AsyncSandboxWrapper(traced),
            trace=trace,
            home=home,
            root_dir=f"{home}/ci_lsp_perf_{uuid.uuid4().hex[:8]}",
        )
        env.exec_checked(f"mkdir -p {shlex.quote(env.root_dir)}")
        yield env
    finally:
        delete_test_sandbox(sandbox_id)


def test_live_ci_lsp_jedi_tool_traces_and_perf(live_rename_env: LiveRenameEnv) -> None:
    """Trace direct LSP calls and rename tool calls inside a real Daytona sandbox."""
    env = live_rename_env
    pkg = f"{env.root_dir}/pkg"
    core_path = f"{pkg}/core.py"
    uses_path = f"{pkg}/uses.py"
    more_path = f"{pkg}/more.py"

    env.write_file(f"{pkg}/__init__.py", "")
    env.write_file(
        core_path,
        "\n".join(
            [
                "def alpha(value):",
                '    """Primary function used for LSP definition/reference probes."""',
                "    return value + 1",
                "",
                "def beta(value):",
                "    return alpha(value) * 2",
                "",
                "def delta(value):",
                "    return beta(value) + 5",
                "",
                "class Runner:",
                "    def run(self, value):",
                "        return delta(value)",
                "",
            ]
        ),
    )
    env.write_file(
        uses_path,
        "\n".join(
            [
                "from pkg.core import alpha, beta, delta, Runner",
                "",
                "def call_alpha():",
                "    return alpha(1)",
                "",
                "def call_beta():",
                "    return beta(2)",
                "",
                "def call_delta():",
                "    return delta(3)",
                "",
                "def call_runner():",
                "    return Runner().run(4)",
                "",
            ]
        ),
    )
    env.write_file(
        more_path,
        "\n".join(
            [
                "from pkg.core import alpha, beta, delta",
                "",
                "def combine():",
                "    return alpha(10) + beta(20) + delta(30)",
                "",
            ]
        ),
    )

    svc = CodeIntelligenceService(
        sandbox_id=env.sandbox_id,
        workspace_root=env.root_dir,
        sandbox=env.traced_sandbox,
    )
    ctx = ToolExecutionContext(
        cwd=Path(env.root_dir),
        metadata={
            "daytona_sandbox": env.async_sandbox,
            "daytona_cwd": env.root_dir,
            "repo_root": env.root_dir,
            "ci_service": svc,
            "agent_run_id": f"ci-lsp-live-{uuid.uuid4().hex[:8]}",
        },
    )

    try:
        init_ok = _measure(
            "service.ensure_initialized",
            env.trace,
            svc,
            lambda: (
                svc.ensure_initialized(wait=True)
                and svc.lsp_client.ensure_ready(install_missing=True)["python"]
            ),
        )
        assert init_ok is True
        assert svc.symbol_index.ensure_built(wait=True, timeout=60.0) is True

        definitions = _measure(
            "lsp.goto_definition(alpha usage)",
            env.trace,
            svc,
            lambda: svc.lsp_client.goto_definition(uses_path, 4, 11),
        )
        assert any(item.file_path == core_path and item.name == "alpha" for item in definitions)

        refs = _measure(
            "lsp.find_references(alpha cold)",
            env.trace,
            svc,
            lambda: svc.lsp_client.find_references(core_path, 1, 0),
        )
        assert len(refs) >= 3

        cache_before = svc.lsp_client.telemetry.cache_hits
        refs_cached = _measure(
            "lsp.find_references(alpha cached)",
            env.trace,
            svc,
            lambda: svc.lsp_client.find_references(core_path, 1, 0),
        )
        assert len(refs_cached) == len(refs)
        assert svc.lsp_client.telemetry.cache_hits > cache_before

        hover = _measure(
            "lsp.hover(alpha)",
            env.trace,
            svc,
            lambda: svc.lsp_client.hover(core_path, 1, 0),
        )
        assert hover is not None
        assert "Primary function" in hover.content

        dry_symbol = _measure(
            "tool.ci_rename_symbol(beta dry_run)",
            env.trace,
            svc,
            lambda: asyncio.run(
                ci_rename_symbol.execute(
                    ci_rename_symbol.input_model(
                        file_path=core_path,
                        line=5,
                        character=0,
                        new_name="beta_v2",
                        dry_run=True,
                    ),
                    ctx,
                )
            ),
        )
        assert not dry_symbol.is_error, dry_symbol.output
        dry_symbol_data = json.loads(dry_symbol.output)
        assert dry_symbol_data["status"] == "dry_run"
        assert len(dry_symbol_data["files"]) >= 3

        commit_symbol = _measure(
            "tool.ci_rename_symbol(beta commit)",
            env.trace,
            svc,
            lambda: asyncio.run(
                ci_rename_symbol.execute(
                    ci_rename_symbol.input_model(
                        file_path=core_path,
                        line=5,
                        character=0,
                        new_name="beta_v2",
                    ),
                    ctx,
                )
            ),
        )
        assert not commit_symbol.is_error, commit_symbol.output
        commit_symbol_data = json.loads(commit_symbol.output)
        assert commit_symbol_data["status"] == "renamed"
        assert len(commit_symbol_data["files"]) >= 3

        dry_facade = _measure(
            "tool.ci_rename(delta dry_run)",
            env.trace,
            svc,
            lambda: asyncio.run(
                ci_rename.execute(
                    ci_rename.input_model(symbol="delta", new_name="delta_v2", dry_run=True),
                    ctx,
                )
            ),
        )
        assert not dry_facade.is_error, dry_facade.output
        dry_facade_data = json.loads(dry_facade.output)
        assert dry_facade_data["status"] == "dry_run"
        assert len(dry_facade_data["files"]) >= 3

        commit_facade = _measure(
            "tool.ci_rename(delta commit)",
            env.trace,
            svc,
            lambda: asyncio.run(
                ci_rename.execute(
                    ci_rename.input_model(symbol="delta", new_name="delta_v2"),
                    ctx,
                )
            ),
        )
        assert not commit_facade.is_error, commit_facade.output
        commit_facade_data = json.loads(commit_facade.output)
        assert commit_facade_data["status"] == "renamed"
        assert len(commit_facade_data["files"]) >= 3

        core_new = env.read_file(core_path)
        uses_new = env.read_file(uses_path)
        more_new = env.read_file(more_path)
        assert "def beta_v2" in core_new and "def beta(" not in core_new
        assert "def delta_v2" in core_new and "def delta(" not in core_new
        assert "beta_v2" in uses_new and "delta_v2" in uses_new
        assert "beta_v2" in more_new and "delta_v2" in more_new
        assert "import alpha, beta," not in uses_new
        assert "import alpha, beta," not in more_new
        assert not re.search(r"(?<![A-Za-z0-9_])beta\(", uses_new)
        assert not re.search(r"(?<![A-Za-z0-9_])delta\(", uses_new)
        assert not re.search(r"(?<![A-Za-z0-9_])beta\(", more_new)
        assert not re.search(r"(?<![A-Za-z0-9_])delta\(", more_new)

        final_status = svc.status()
        print(
            "[ci-lsp-live-final-status] "
            + json.dumps(
                {
                    "sandbox_id": env.sandbox_id,
                    "workspace_root": env.root_dir,
                    "symbol_index": final_status["symbol_index"],
                    "arbiter": final_status["arbiter"],
                    "lsp": final_status["lsp"],
                    "jedi_worker_env": os.environ.get("CI_JEDI_WORKER_ENABLED", ""),
                    "jedi_worker_used": getattr(svc.lsp_client, "_worker", None) is not None,
                },
                sort_keys=True,
            ),
            flush=True,
        )
    finally:
        env.trace.print_all()
        env.trace.print_summary()
        svc.dispose()


def _measure(
    label: str,
    trace: TraceLog,
    svc: CodeIntelligenceService,
    func: Callable[[], _T],
) -> _T:
    event_start = trace.mark()
    telemetry_before = svc.lsp_client.telemetry
    index_before = svc.symbol_index.generation
    arbiter_before = svc.arbiter.generation
    started_at = time.perf_counter()
    error = ""
    try:
        result = func()
        return result
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
        telemetry_after = svc.lsp_client.telemetry
        payload = {
            "label": label,
            "duration_ms": duration_ms,
            "error": error,
            "lsp_queries_delta": telemetry_after.queries - telemetry_before.queries,
            "lsp_successes_delta": telemetry_after.successes - telemetry_before.successes,
            "lsp_errors_delta": telemetry_after.errors - telemetry_before.errors,
            "lsp_cache_hits_delta": telemetry_after.cache_hits - telemetry_before.cache_hits,
            "symbol_index_generation_delta": svc.symbol_index.generation - index_before,
            "arbiter_generation_delta": svc.arbiter.generation - arbiter_before,
            **trace.summary_since(event_start),
        }
        print("[ci-lsp-live-trace] " + json.dumps(payload, sort_keys=True), flush=True)


def _percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((pct / 100) * (len(ordered) - 1)))
    return round(ordered[index], 3)


def _clean_stdout(stdout: str) -> str:
    return _TERM_NOISE.sub("", stdout)


def _compact(value: str, *, limit: int = _MAX_COMMAND_CHARS) -> str:
    text = value.replace("\n", "\\n")
    if len(text) > limit:
        return text[:limit] + f"... ({len(text)} chars)"
    return text
