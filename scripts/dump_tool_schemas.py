#!/usr/bin/env python3
"""Dump live tool input/output schemas in a human-readable form."""

from __future__ import annotations

# ruff: noqa: E402

import argparse
import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SRC = _ROOT / "backend" / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from tools.core.schema_summary import collect_schema_toolkits, format_tool_schema_summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print schemas from the live EphemeralOS tool objects.",
    )
    parser.add_argument(
        "--cwd",
        default=str(_ROOT),
        help="Workspace used for runtime tool discovery. Defaults to the repo root.",
    )
    parser.add_argument(
        "--sandbox-id",
        default="schema-dump",
        help="Synthetic sandbox id used when constructing context-aware toolkits.",
    )
    parser.add_argument(
        "--caller-agent",
        default="",
        help="Synthetic caller agent used for caller-aware tool schemas.",
    )
    parser.add_argument(
        "--no-descriptions",
        action="store_true",
        help="Omit tool and field descriptions.",
    )
    parser.add_argument(
        "--include-instructions",
        action="store_true",
        help="Include toolkit instructions.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional path to write instead of printing to stdout.",
    )
    args = parser.parse_args()

    toolkits = collect_schema_toolkits(
        cwd=Path(args.cwd),
        sandbox_id=args.sandbox_id,
        caller_agent=args.caller_agent,
    )
    summary = format_tool_schema_summary(
        toolkits,
        include_descriptions=not args.no_descriptions,
        include_instructions=args.include_instructions,
    )

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(summary + "\n", encoding="utf-8")
        print(f"Wrote tool schema summary to {output_path}")
        return 0

    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
