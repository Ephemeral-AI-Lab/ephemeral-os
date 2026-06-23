#!/usr/bin/env python3
"""Measure wall-clock latency for sandbox-cli subprocess invocations."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import shlex
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_NAME = "sandbox-cli-latency"
SENSITIVE_ENV_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL")


@dataclass(frozen=True)
class Case:
    name: str
    args: list[str]
    description: str = ""
    env: dict[str, str] | None = None


DEFAULT_CASES = [
    Case(
        name="cli_help",
        args=["--help"],
        description="Top-level CLI parser/help path; no gateway required.",
    ),
    Case(
        name="manager_help",
        args=["manager", "help"],
        description="Manager catalog help render; no gateway required.",
    ),
    Case(
        name="manager_help_create_sandbox",
        args=["manager", "help", "create_sandbox"],
        description="Manager operation help render; no gateway required.",
    ),
    Case(
        name="runtime_help_exec_command",
        args=[
            "--default-sandbox-id",
            "latency-probe",
            "runtime",
            "help",
            "exec_command",
        ],
        description="Runtime operation help render with explicit default sandbox; no gateway required.",
    ),
]


def main() -> int:
    args = parse_args()
    cases = resolve_cases(args)

    if args.list_cases:
        print_cases(cases)
        return 0

    if args.build:
        build_sandbox_cli(args.cargo_profile)

    cli_path = resolve_cli_path(args)
    output_dir = resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    for case in cases:
        records.extend(run_case(cli_path, case, args.warmups, "warmup", args.timeout))
        records.extend(run_case(cli_path, case, args.iterations, "measure", args.timeout))

    write_samples_csv(output_dir / "samples.csv", records)
    write_samples_jsonl(output_dir / "samples.jsonl", records)
    summary = build_summary(args, cli_path, cases, records, output_dir)
    write_json(output_dir / "summary.json", summary)

    print_summary(summary, output_dir)

    measurement_failures = [
        record
        for record in records
        if record["phase"] == "measure" and not record["ok"]
    ]
    if measurement_failures and not args.allow_failures:
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure end-to-end wall-clock latency for sandbox-cli commands."
    )
    parser.add_argument(
        "--cli",
        type=Path,
        help="sandbox-cli executable to measure. Defaults to target/debug/sandbox-cli when present, else bin/sandbox-cli.",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Build sandbox-cli before measuring. When --cli is omitted, the built binary is measured.",
    )
    parser.add_argument(
        "--cargo-profile",
        default="debug",
        help="Cargo profile used with --build: debug, release, or a custom --profile value.",
    )
    parser.add_argument(
        "--iterations",
        type=positive_int,
        default=30,
        help="Measured invocations per case.",
    )
    parser.add_argument(
        "--warmups",
        type=non_negative_int,
        default=5,
        help="Warmup invocations per case. Warmups are recorded but excluded from summary stats.",
    )
    parser.add_argument(
        "--timeout",
        type=positive_float,
        default=30.0,
        help="Per-invocation timeout in seconds.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory. Defaults to target/experiments/sandbox-cli-latency/<timestamp>.",
    )
    parser.add_argument(
        "--commands-file",
        type=Path,
        help="JSON file containing custom cases. The file can be a list or an object with a 'cases' list.",
    )
    parser.add_argument(
        "--case",
        dest="case_specs",
        action="append",
        default=[],
        metavar="NAME::ARGS",
        help="Add a custom case. ARGS is parsed with shell-like quoting, for example 'manager_help::manager help'.",
    )
    parser.add_argument(
        "--include-default-cases",
        action="store_true",
        help="Include default safe cases in addition to --commands-file or --case cases.",
    )
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="Write results and exit 0 even when measured invocations fail.",
    )
    parser.add_argument(
        "--list-cases",
        action="store_true",
        help="Print resolved cases and exit without running commands.",
    )
    return parser.parse_args()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def resolve_cases(args: argparse.Namespace) -> list[Case]:
    custom_cases: list[Case] = []
    if args.commands_file:
        custom_cases.extend(load_cases_file(args.commands_file))
    custom_cases.extend(parse_case_specs(args.case_specs))

    cases: list[Case] = []
    if args.include_default_cases or not custom_cases:
        cases.extend(DEFAULT_CASES)
    cases.extend(custom_cases)
    ensure_unique_case_names(cases)
    return cases


def load_cases_file(path: Path) -> list[Case]:
    case_path = resolve_repo_path(path)
    with case_path.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    raw_cases = document.get("cases") if isinstance(document, dict) else document
    if not isinstance(raw_cases, list):
        raise SystemExit(f"{case_path} must contain a case list")

    cases = []
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            raise SystemExit(f"{case_path} case {index} must be an object")
        name = require_string(raw_case, "name", case_path, index)
        raw_args = raw_case.get("args", [])
        if not isinstance(raw_args, list) or not all(
            isinstance(item, str) for item in raw_args
        ):
            raise SystemExit(f"{case_path} case {index} args must be a string list")
        description = raw_case.get("description", "")
        if not isinstance(description, str):
            raise SystemExit(f"{case_path} case {index} description must be a string")
        raw_env = raw_case.get("env")
        env = None
        if raw_env is not None:
            if not isinstance(raw_env, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in raw_env.items()
            ):
                raise SystemExit(f"{case_path} case {index} env must map strings to strings")
            env = dict(raw_env)
        cases.append(Case(name=name, args=list(raw_args), description=description, env=env))
    return cases


def require_string(raw_case: dict[str, Any], field: str, path: Path, index: int) -> str:
    value = raw_case.get(field)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"{path} case {index} {field} must be a non-empty string")
    return value


def parse_case_specs(case_specs: list[str]) -> list[Case]:
    cases = []
    for spec in case_specs:
        name, separator, command = spec.partition("::")
        if not separator or not name:
            raise SystemExit(f"--case must use NAME::ARGS, got {spec!r}")
        cases.append(Case(name=name, args=shlex.split(command)))
    return cases


def ensure_unique_case_names(cases: list[Case]) -> None:
    seen: set[str] = set()
    for case in cases:
        if case.name in seen:
            raise SystemExit(f"duplicate case name: {case.name}")
        seen.add(case.name)


def build_sandbox_cli(profile: str) -> None:
    command = [
        "cargo",
        "build",
        "--quiet",
        "--manifest-path",
        str(REPO_ROOT / "Cargo.toml"),
        "-p",
        "sandbox-gateway",
        "--bin",
        "sandbox-cli",
    ]
    if profile == "release":
        command.append("--release")
    elif profile != "debug":
        command.extend(["--profile", profile])
    subprocess.run(command, cwd=REPO_ROOT, check=True)


def resolve_cli_path(args: argparse.Namespace) -> Path:
    if args.cli:
        return resolve_repo_path(args.cli)
    if args.build:
        return cargo_binary_path(args.cargo_profile)
    debug_binary = cargo_binary_path("debug")
    if debug_binary.is_file() and os.access(debug_binary, os.X_OK):
        return debug_binary
    return REPO_ROOT / "bin" / "sandbox-cli"


def cargo_binary_path(profile: str) -> Path:
    profile_dir = "release" if profile == "release" else profile
    return cargo_target_dir() / profile_dir / "sandbox-cli"


def cargo_target_dir() -> Path:
    target_dir = os.environ.get("CARGO_TARGET_DIR")
    if not target_dir:
        return REPO_ROOT / "target"
    path = Path(target_dir)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def resolve_repo_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def resolve_output_dir(path: Path | None) -> Path:
    if path:
        return resolve_repo_path(path)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return REPO_ROOT / "target" / "experiments" / EXPERIMENT_NAME / timestamp


def run_case(
    cli_path: Path,
    case: Case,
    iterations: int,
    phase: str,
    timeout: float,
) -> list[dict[str, Any]]:
    return [
        run_once(cli_path=cli_path, case=case, iteration=index, phase=phase, timeout=timeout)
        for index in range(iterations)
    ]


def run_once(
    cli_path: Path,
    case: Case,
    iteration: int,
    phase: str,
    timeout: float,
) -> dict[str, Any]:
    env = os.environ.copy()
    if case.env:
        env.update(case.env)

    command = [str(cli_path), *case.args]
    started_at = datetime.now(timezone.utc).isoformat()
    started_ns = time.perf_counter_ns()
    timed_out = False
    returncode: int | None
    stdout = b""
    stderr = b""

    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout or b""
        stderr = completed.stderr or b""
    except subprocess.TimeoutExpired as error:
        timed_out = True
        returncode = None
        stdout = bytes_or_empty(error.stdout)
        stderr = bytes_or_empty(error.stderr)

    duration_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
    return {
        "case": case.name,
        "phase": phase,
        "iteration": iteration,
        "started_at": started_at,
        "command": command,
        "ok": returncode == 0 and not timed_out,
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "stdout_sample": decode_sample(stdout),
        "stderr_sample": decode_sample(stderr),
    }


def bytes_or_empty(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8", errors="replace")


def decode_sample(value: bytes, limit: int = 400) -> str:
    return value[:limit].decode("utf-8", errors="replace")


def write_samples_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "case",
        "phase",
        "iteration",
        "ok",
        "returncode",
        "timed_out",
        "duration_ms",
        "stdout_bytes",
        "stderr_bytes",
        "stdout_sha256",
        "stderr_sha256",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record[field] for field in fields})


def write_samples_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            json.dump(record, handle, sort_keys=True)
            handle.write("\n")


def write_json(path: Path, value: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def build_summary(
    args: argparse.Namespace,
    cli_path: Path,
    cases: list[Case],
    records: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    return {
        "metadata": {
            "experiment": EXPERIMENT_NAME,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "repo_root": str(REPO_ROOT),
            "output_dir": str(output_dir),
            "cli": str(cli_path),
            "iterations": args.iterations,
            "warmups": args.warmups,
            "timeout_seconds": args.timeout,
            "build": args.build,
            "cargo_profile": args.cargo_profile,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "argv": sys.argv,
            "environment": selected_environment(),
        },
        "cases": [summarize_case(case, records) for case in cases],
    }


def summarize_case(case: Case, records: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [
        record
        for record in records
        if record["case"] == case.name and record["phase"] == "measure"
    ]
    successful_durations = [
        float(record["duration_ms"]) for record in measured if record["ok"]
    ]
    failures = [record for record in measured if not record["ok"]]
    return {
        "name": case.name,
        "args": case.args,
        "description": case.description,
        "env": redact_env(case.env or {}),
        "measurements": len(measured),
        "successes": len(successful_durations),
        "failures": len(failures),
        "stats_ms": stats(successful_durations),
    }


def stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {
            "min": None,
            "mean": None,
            "stdev": None,
            "p50": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "max": None,
        }
    return {
        "min": min(values),
        "mean": statistics.mean(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
        "p50": percentile(values, 50),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values),
    }


def percentile(values: list[float], percentile_value: int) -> float:
    ordered = sorted(values)
    rank = math.ceil((percentile_value / 100) * len(ordered)) - 1
    rank = min(max(rank, 0), len(ordered) - 1)
    return ordered[rank]


def selected_environment() -> dict[str, str]:
    keys = ("CARGO_TARGET_DIR", "SANDBOX_GATEWAY_SOCKET", "SANDBOX_DEFAULT_ID")
    return redact_env({key: os.environ[key] for key in keys if key in os.environ})


def redact_env(env: dict[str, str]) -> dict[str, str]:
    redacted = {}
    for key, value in env.items():
        if any(marker in key.upper() for marker in SENSITIVE_ENV_MARKERS):
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


def print_summary(summary: dict[str, Any], output_dir: Path) -> None:
    rows = []
    for case in summary["cases"]:
        stats_ms = case["stats_ms"]
        rows.append(
            [
                case["name"],
                f"{case['successes']}/{case['measurements']}",
                format_ms(stats_ms["mean"]),
                format_ms(stats_ms["p50"]),
                format_ms(stats_ms["p95"]),
                format_ms(stats_ms["max"]),
            ]
        )

    headers = ["case", "ok", "mean_ms", "p50_ms", "p95_ms", "max_ms"]
    widths = [
        max(len(str(row[index])) for row in [headers, *rows])
        for index in range(len(headers))
    ]
    print(" ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print(" ".join("-" * width for width in widths))
    for row in rows:
        print(" ".join(str(cell).ljust(widths[index]) for index, cell in enumerate(row)))
    print(f"\nresults: {output_dir}")


def format_ms(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def print_cases(cases: list[Case]) -> None:
    for case in cases:
        command = " ".join(shlex.quote(part) for part in case.args)
        print(f"{case.name}: {command}")
        if case.description:
            print(f"  {case.description}")


if __name__ == "__main__":
    raise SystemExit(main())
