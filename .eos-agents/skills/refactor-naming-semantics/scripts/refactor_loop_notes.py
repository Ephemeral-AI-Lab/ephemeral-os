#!/usr/bin/env python3
"""Create and append refactor loop notes for autonomous cleanup passes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes", required=True, help="Path to the loop note markdown file")
    subcommands = parser.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser("init", help="Initialize a loop note file")
    init.add_argument("--target", required=True, help="Refactor target boundary")
    init.add_argument("--checks", default="", help="Planned verification checks")
    init.add_argument("--invariants", default="", help="Behavior and public compatibility invariants")
    init.add_argument("--exit", default="", help="Explicit stop condition")

    append = subcommands.add_parser("append", help="Append one pass result")
    append.add_argument("--pass-name", required=True, help="Short pass name")
    append.add_argument("--summary", required=True, help="What changed or was learned")
    append.add_argument("--checks", default="", help="Checks run and result")
    append.add_argument("--next", default="", help="Next action or stop reason")
    return parser.parse_args()


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_multiline(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return "Not recorded."
    return stripped


def initialize_notes(path: Path, args: argparse.Namespace) -> None:
    content = "\n".join(
        [
            "# Refactor Loop Notes",
            "",
            f"Initialized: {timestamp()}",
            "",
            "## Contract",
            "",
            f"Target: {normalize_multiline(args.target)}",
            "",
            "Invariants:",
            "",
            normalize_multiline(args.invariants),
            "",
            "Checks:",
            "",
            normalize_multiline(args.checks),
            "",
            "Exit condition:",
            "",
            normalize_multiline(args.exit),
            "",
            "## Passes",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def append_pass(path: Path, args: argparse.Namespace) -> None:
    entry = "\n".join(
        [
            f"### {args.pass_name}",
            "",
            f"Time: {timestamp()}",
            "",
            "Summary:",
            "",
            normalize_multiline(args.summary),
            "",
            "Checks:",
            "",
            normalize_multiline(args.checks),
            "",
            "Next:",
            "",
            normalize_multiline(args.next),
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(entry)


def main() -> int:
    args = parse_args()
    notes_path = Path(args.notes)
    if args.command == "init":
        initialize_notes(notes_path, args)
    elif args.command == "append":
        append_pass(notes_path, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
