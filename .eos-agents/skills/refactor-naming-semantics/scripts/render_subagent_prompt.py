#!/usr/bin/env python3
"""Render a refactor subagent prompt template with JSON values."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


PLACEHOLDER_PATTERN = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", help="Prompt template path")
    parser.add_argument("--values", help="JSON object or path to a JSON file")
    parser.add_argument("--set", action="append", default=[], help="Set one key=value placeholder. Repeat as needed")
    parser.add_argument("--out", help="Write rendered prompt to this path")
    parser.add_argument("--strict", action="store_true", help="Fail if any placeholders remain unresolved")
    return parser.parse_args()


def load_values(raw_values: str | None) -> dict[str, Any]:
    if not raw_values:
        return {}
    candidate = Path(raw_values)
    if candidate.exists():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return json.loads(raw_values)


def merge_set_values(values: dict[str, Any], assignments: list[str]) -> dict[str, Any]:
    merged = dict(values)
    for assignment in assignments:
        if "=" not in assignment:
            raise SystemExit(f"--set value must be key=value, got: {assignment}")
        key, value = assignment.split("=", 1)
        merged[key.strip()] = value
    return merged


def stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(f"- {item}" for item in value)
    if isinstance(value, dict):
        return json.dumps(value, indent=2, sort_keys=True)
    if value is None:
        return ""
    return str(value)


def render_template(template: str, values: dict[str, Any], strict: bool) -> str:
    missing: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            missing.add(key)
            return match.group(0)
        return stringify(values[key])

    rendered = PLACEHOLDER_PATTERN.sub(replace, template)
    if strict and missing:
        missing_list = ", ".join(sorted(missing))
        raise SystemExit(f"Unresolved placeholders: {missing_list}")
    return rendered


def main() -> int:
    args = parse_args()
    values = merge_set_values(load_values(args.values), args.set)
    template = Path(args.template).read_text(encoding="utf-8")
    rendered = render_template(template, values, args.strict)
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="" if rendered.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
