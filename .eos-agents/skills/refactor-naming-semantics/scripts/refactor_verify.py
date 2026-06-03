#!/usr/bin/env python3
"""Run narrow refactor verification commands and write a concise report."""

from __future__ import annotations

import argparse
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VerificationCommandReport:
    command: str
    returncode: int
    elapsed_seconds: float
    stdout: str
    stderr: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", default=".", help="Working directory for verification commands")
    parser.add_argument("--command", action="append", required=True, help="Command to run. Repeat for multiple checks")
    parser.add_argument("--summary", help="Write markdown summary to this path")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout per command in seconds")
    parser.add_argument("--skip-git-diff-check", action="store_true", help="Do not run git diff --check before commands")
    return parser.parse_args()


def run_command(command: str, cwd: Path, timeout: int) -> VerificationCommandReport:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        return VerificationCommandReport(
            command=command,
            returncode=completed.returncode,
            elapsed_seconds=time.monotonic() - started,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    except subprocess.TimeoutExpired as exc:
        return VerificationCommandReport(
            command=command,
            returncode=124,
            elapsed_seconds=time.monotonic() - started,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + f"\nTimed out after {timeout} seconds.",
        )


def tail(text: str, limit: int = 120) -> str:
    lines = text.splitlines()
    if len(lines) <= limit:
        return text.strip()
    return "\n".join(lines[-limit:]).strip()


def is_git_repo(cwd: Path) -> bool:
    if not (cwd / ".git").exists():
        return False
    result = subprocess.run(
        "git rev-parse --is-inside-work-tree",
        cwd=cwd,
        shell=True,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def format_command_report(report: VerificationCommandReport) -> str:
    status = "PASS" if report.returncode == 0 else "FAIL"
    lines = [
        f"### {status}: `{report.command}`",
        "",
        f"- Exit code: {report.returncode}",
        f"- Elapsed: {report.elapsed_seconds:.2f}s",
    ]
    stdout = tail(report.stdout)
    stderr = tail(report.stderr)
    if stdout:
        lines.extend(["", "stdout:", "", "```", stdout, "```"])
    if stderr:
        lines.extend(["", "stderr:", "", "```", stderr, "```"])
    lines.append("")
    return "\n".join(lines)


def format_summary(cwd: Path, reports: list[VerificationCommandReport]) -> str:
    failed = [report for report in reports if report.returncode != 0]
    lines = [
        "# Refactor Verification",
        "",
        f"Working directory: `{cwd}`",
        f"Commands run: {len(reports)}",
        f"Failures: {len(failed)}",
        "",
    ]
    for report in reports:
        lines.append(format_command_report(report))
    if failed:
        lines.append("## Next Step")
        lines.append("")
        lines.append("Inspect the first failing command, fix only refactor-caused breakage, and rerun the same command before broadening verification.")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    cwd = Path(args.cwd).resolve()
    commands = list(args.command)
    if not args.skip_git_diff_check and is_git_repo(cwd):
        commands.insert(0, "git diff --check")

    reports = [run_command(command, cwd, args.timeout) for command in commands]
    summary = format_summary(cwd, reports)
    if args.summary:
        Path(args.summary).write_text(summary, encoding="utf-8")
    else:
        print(summary, end="")
    return 1 if any(report.returncode != 0 for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
