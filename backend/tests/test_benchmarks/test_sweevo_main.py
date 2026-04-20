from __future__ import annotations

from message.messages import ConversationMessage, TextBlock
from message.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    SystemNotification,
)
from providers.types import UsageSnapshot
import logging
import os
import sys

import benchmarks.sweevo as sweevo_pkg
from benchmarks.sweevo import __main__ as sweevo_main
import asyncio


def test_build_run_log_path_uses_time_and_team_run_id(tmp_path, monkeypatch):
    monkeypatch.setattr(sweevo_main, "_PROJECT_ROOT", tmp_path)
    log_path = sweevo_main._build_run_log_path("2026-04-20-10-30_sweevo_benchmark", "2026-04-20-10-30")
    assert log_path == tmp_path / ".ephemeralos" / "team-runs" / "2026-04-20-10-30_sweevo_benchmark" / "benchmark" / "2026-04-20-10-30_run.log"


def test_build_structured_log_path(tmp_path, monkeypatch):
    monkeypatch.setattr(sweevo_main, "_PROJECT_ROOT", tmp_path)
    path = sweevo_main._build_structured_log_path("2026-04-20-10-30_sweevo_benchmark", "2026-04-20-10-30")
    assert path == tmp_path / ".ephemeralos" / "team-runs" / "2026-04-20-10-30_sweevo_benchmark" / "benchmark" / "2026-04-20-10-30_run.events.jsonl"


def test_build_code_intelligence_log_path(tmp_path, monkeypatch):
    monkeypatch.setattr(sweevo_main, "_PROJECT_ROOT", tmp_path)
    path = sweevo_main._build_code_intelligence_log_path("2026-04-20-10-30_sweevo_benchmark", "2026-04-20-10-30")
    assert path == tmp_path / ".ephemeralos" / "team-runs" / "2026-04-20-10-30_sweevo_benchmark" / "benchmark" / "2026-04-20-10-30_run.code-intelligence.log"


def test_default_no_proxy_appends_bigmodel_without_overwriting(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "example.test")
    monkeypatch.delenv("no_proxy", raising=False)

    sweevo_pkg.ensure_default_no_proxy()

    expected = "example.test,open.bigmodel.cn,localhost,127.0.0.1,::1"
    assert sweevo_pkg._merge_no_proxy("example.test") == expected
    assert os.environ["NO_PROXY"] == expected
    assert os.environ["no_proxy"] == expected


def test_default_no_proxy_preserves_wildcard(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")

    sweevo_pkg.ensure_default_no_proxy()

    assert os.environ["NO_PROXY"] == "*"
    assert os.environ["no_proxy"] == "*"


def test_main_writes_plaintext_run_log(monkeypatch, tmp_path):
    monkeypatch.setattr(sweevo_main, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sweevo_main, "_utc_run_time", lambda: "2026-04-20-10-30")

    async def _fake_cmd_run(args, *, team_run_id):
        print("=" * 72, flush=True)
        print("  SWE-EVO run  instance=pydantic__pydantic_v2.7.0_v2.7.1", flush=True)
        print("\033[32m[pass]\033[0m recorded", flush=True)
        sys.stderr.write("warning on stderr\n")
        sys.stderr.flush()
        return 0

    monkeypatch.setattr(sweevo_main, "_cmd_run", _fake_cmd_run)

    exit_code = sweevo_main.main(
        [
            "--instance-id",
            "pydantic__pydantic_v2.7.0_v2.7.1",
        ]
    )

    assert exit_code == 0

    benchmark_dir = tmp_path / ".ephemeralos" / "team-runs" / "2026-04-20-10-30_sweevo_benchmark" / "benchmark"
    run_logs = sorted(
        path for path in benchmark_dir.glob("*.log") if ".code-intelligence." not in path.name
    )
    assert len(run_logs) == 1

    ci_logs = sorted(benchmark_dir.glob("*.code-intelligence.log"))
    assert len(ci_logs) == 1

    contents = run_logs[0].read_text(encoding="utf-8")
    assert "SWE-EVO run  instance=pydantic__pydantic_v2.7.0_v2.7.1" in contents
    assert "[pass] recorded" in contents
    assert "warning on stderr" in contents
    assert "\x1b[" not in contents


def test_main_writes_code_intelligence_log_in_parallel(monkeypatch, tmp_path):
    monkeypatch.setattr(sweevo_main, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sweevo_main, "_utc_run_time", lambda: "2026-04-20-10-30")

    async def _fake_cmd_run(_args, *, team_run_id):
        logging.getLogger("code_intelligence.routing.service").info("indexed workspace")
        logging.getLogger("server.routers.code_intelligence").info("router request")
        logging.getLogger("benchmarks.sweevo.runner").warning("benchmark warning")
        return 0

    monkeypatch.setattr(sweevo_main, "_cmd_run", _fake_cmd_run)

    exit_code = sweevo_main.main(
        [
            "--instance-id",
            "pydantic__pydantic_v2.7.0_v2.7.1",
        ]
    )

    assert exit_code == 0

    benchmark_dir = tmp_path / ".ephemeralos" / "team-runs" / "2026-04-20-10-30_sweevo_benchmark" / "benchmark"
    ci_logs = sorted(benchmark_dir.glob("*.code-intelligence.log"))
    assert len(ci_logs) == 1

    contents = ci_logs[0].read_text(encoding="utf-8")
    assert "code_intelligence.routing.service: indexed workspace" in contents
    assert "server.routers.code_intelligence: router request" in contents
    assert "benchmarks.sweevo.runner" not in contents


def test_main_run_log_records_info_level_python_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(sweevo_main, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sweevo_main, "_utc_run_time", lambda: "2026-04-20-10-30")

    async def _fake_cmd_run(_args, *, team_run_id):
        logging.getLogger("benchmarks.sweevo.runner").info("benchmark info message")
        return 0

    monkeypatch.setattr(sweevo_main, "_cmd_run", _fake_cmd_run)

    exit_code = sweevo_main.main(
        [
            "--instance-id",
            "pydantic__pydantic_v2.7.0_v2.7.1",
        ]
    )

    assert exit_code == 0

    benchmark_dir = tmp_path / ".ephemeralos" / "team-runs" / "2026-04-20-10-30_sweevo_benchmark" / "benchmark"
    run_logs = sorted(
        path for path in benchmark_dir.glob("*.log") if ".code-intelligence." not in path.name
    )
    assert len(run_logs) == 1
    contents = run_logs[0].read_text(encoding="utf-8")
    assert "INFO benchmarks.sweevo.runner: benchmark info message" in contents


def test_main_does_not_print_log_paths_into_run_log(monkeypatch, tmp_path):
    monkeypatch.setattr(sweevo_main, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sweevo_main, "_utc_run_time", lambda: "2026-04-20-10-30")

    async def _fake_cmd_run(_args, *, team_run_id):
        print("benchmark body", flush=True)
        return 0

    monkeypatch.setattr(sweevo_main, "_cmd_run", _fake_cmd_run)

    exit_code = sweevo_main.main(
        [
            "--instance-id",
            "pydantic__pydantic_v2.7.0_v2.7.1",
        ]
    )

    assert exit_code == 0

    benchmark_dir = tmp_path / ".ephemeralos" / "team-runs" / "2026-04-20-10-30_sweevo_benchmark" / "benchmark"
    run_logs = sorted(
        path for path in benchmark_dir.glob("*.log") if ".code-intelligence." not in path.name
    )
    assert len(run_logs) == 1
    contents = run_logs[0].read_text(encoding="utf-8")
    assert "benchmark body" in contents
    assert "Log file:" not in contents
    assert "Code intelligence log file:" not in contents


def test_main_list_does_not_write_run_log(monkeypatch, tmp_path):
    monkeypatch.setattr(sweevo_main, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sweevo_main, "_cmd_list", lambda _source: 0)

    exit_code = sweevo_main.main(["--list"])

    assert exit_code == 0
    assert list(tmp_path.iterdir()) == []


def test_main_run_log_keeps_full_conversation_messages(monkeypatch, tmp_path):
    from benchmarks.sweevo import runner as sweevo_runner

    monkeypatch.setattr(sweevo_main, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sweevo_main, "_utc_run_time", lambda: "2026-04-20-10-30")

    long_text = "conversation-" * 60
    long_system = "system-note-" * 50

    async def _fake_run_sweevo_with_agent(*, printer, **kwargs):
        printer.emit(
            AssistantTextDelta(
                text=long_text,
                agent_name="developer",
                work_id="run-1234567890",
            )
        )
        printer.emit(
            AssistantTurnComplete(
                message=ConversationMessage(
                    role="assistant",
                    content=[TextBlock(text=long_text)],
                ),
                usage=UsageSnapshot(),
                agent_name="developer",
                work_id="run-1234567890",
            )
        )
        printer.emit(
            SystemNotification(
                text=long_system,
                category="runtime_note",
                agent_name="developer",
                work_id="run-1234567890",
            )
        )
        return {
            "test": {"exit_code": 0},
            "grading": {},
            "team": {},
            "team_status": "succeeded",
            "agent_events": 1,
        }

    monkeypatch.setattr(sweevo_runner, "run_sweevo_with_agent", _fake_run_sweevo_with_agent)

    exit_code = sweevo_main.main(
        [
            "--instance-id",
            "pydantic__pydantic_v2.7.0_v2.7.1",
        ]
    )

    assert exit_code == 0

    benchmark_dir = tmp_path / ".ephemeralos" / "team-runs" / "2026-04-20-10-30_sweevo_benchmark" / "benchmark"
    run_logs = sorted(
        path for path in benchmark_dir.glob("*.log") if ".code-intelligence." not in path.name
    )
    assert len(run_logs) == 1
    contents = run_logs[0].read_text(encoding="utf-8")
    assert f"[text] {long_text}" in contents
    assert f"[system:runtime_note] {long_system}" in contents


def test_main_uses_resume_team_run_id_as_folder(monkeypatch, tmp_path):
    """When --resume-team-run-id is given, benchmark files land in that folder."""
    monkeypatch.setattr(sweevo_main, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sweevo_main, "_utc_run_time", lambda: "2026-04-20-10-30")

    captured: dict[str, object] = {}

    async def _fake_cmd_run(_args, *, team_run_id):
        captured["team_run_id"] = team_run_id
        return 0

    monkeypatch.setattr(sweevo_main, "_cmd_run", _fake_cmd_run)

    exit_code = sweevo_main.main(
        [
            "--instance-id",
            "pydantic__pydantic_v2.7.0_v2.7.1",
            "--resume-team-run-id",
            "2026-04-19-08-00_sweevo_benchmark",
        ]
    )

    assert exit_code == 0
    assert captured["team_run_id"] == "2026-04-19-08-00_sweevo_benchmark"
    benchmark_dir = tmp_path / ".ephemeralos" / "team-runs" / "2026-04-19-08-00_sweevo_benchmark" / "benchmark"
    assert benchmark_dir.exists()


def test_main_uses_selected_team_name_for_fresh_run_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(sweevo_main, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sweevo_main, "_utc_run_time", lambda: "2026-04-20-10-30")

    captured: dict[str, object] = {}

    async def _fake_cmd_run(args, *, team_run_id):
        captured["team_run_id"] = team_run_id
        captured["team_name"] = args.team_name
        return 0

    monkeypatch.setattr(sweevo_main, "_cmd_run", _fake_cmd_run)

    exit_code = sweevo_main.main(
        [
            "--instance-id",
            "pydantic__pydantic_v2.7.0_v2.7.1",
            "--team",
            "sweevo-team-glm5.1",
        ]
    )

    assert exit_code == 0
    assert captured == {
        "team_run_id": "2026-04-20-10-30_sweevo-team-glm5.1",
        "team_name": "sweevo-team-glm5.1",
    }
    benchmark_dir = (
        tmp_path
        / ".ephemeralos"
        / "team-runs"
        / "2026-04-20-10-30_sweevo-team-glm5.1"
        / "benchmark"
    )
    assert benchmark_dir.exists()


def test_ansi_stripping_tee_flush_tolerates_closed_mirror(tmp_path):
    mirror = (tmp_path / "run.log").open("w", encoding="utf-8")
    tee = sweevo_main._AnsiStrippingTee(sys.stdout, mirror)

    mirror.close()

    tee.flush()


def test_ansi_stripping_tee_write_tolerates_broken_primary(tmp_path):
    class _BrokenPrimary:
        def __init__(self) -> None:
            self.encoding = "utf-8"
            self.errors = "strict"
            self.flush_calls = 0

        def write(self, _data: str) -> int:
            raise BrokenPipeError()

        def flush(self) -> None:
            self.flush_calls += 1
            raise BrokenPipeError()

    mirror_path = tmp_path / "run.log"
    with mirror_path.open("w", encoding="utf-8") as mirror:
        primary = _BrokenPrimary()
        tee = sweevo_main._AnsiStrippingTee(primary, mirror)

        written = tee.write("\033[32m[pass]\033[0m recorded\n")
        tee.flush()

    assert written == len("\033[32m[pass]\033[0m recorded\n")
    assert primary.flush_calls == 0
    assert mirror_path.read_text(encoding="utf-8") == "[pass] recorded\n"


def test_cmd_run_forces_color_even_when_stdout_is_not_tty(monkeypatch):
    created: dict[str, object] = {}
    captured: dict[str, object] = {}

    class _FakePrinter:
        def __init__(self, *, color, truncate, timestamps, sink):
            created["color"] = color
            created["truncate"] = truncate
            created["timestamps"] = timestamps
            created["sink"] = sink

        def summary(self):
            return {"totals": {}}

        def raw_line(self, agent, body):
            return None

    async def _fake_run_sweevo_with_agent(**kwargs):
        captured.update(kwargs)
        return {
            "test": {"exit_code": 0},
            "grading": {},
            "team": {},
            "team_status": "succeeded",
            "agent_events": 0,
        }

    monkeypatch.setattr("message.event_printer.MultiAgentEventPrinter", _FakePrinter)
    monkeypatch.setattr(
        "benchmarks.sweevo.runner.run_sweevo_with_agent", _fake_run_sweevo_with_agent
    )

    class _FakeStdout:
        def write(self, data):
            return len(data)

        def flush(self):
            return None

        def isatty(self):
            return False

    monkeypatch.setattr(sys, "stdout", _FakeStdout())

    args = sweevo_main._build_parser().parse_args(
        [
            "--instance-id",
            "pydantic__pydantic_v2.7.0_v2.7.1",
            "--team-name",
            "sweevo-team-glm5.1",
        ]
    )
    exit_code = asyncio.run(sweevo_main._cmd_run(args, team_run_id="2026-04-20-10-30_sweevo_benchmark"))

    assert exit_code == 0
    assert created["color"] is True
    assert captured["team_name"] == "sweevo-team-glm5.1"
