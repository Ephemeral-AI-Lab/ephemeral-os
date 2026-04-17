#!/usr/bin/env python3
"""Build and print the system prompt for a given agent name.

Usage:
    python scripts/build_system_prompt.py <agent_name> [--cwd <dir>]

Examples:
    python scripts/build_system_prompt.py coder
    python scripts/build_system_prompt.py planner --cwd /tmp/project
"""

from __future__ import annotations

import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SRC = _ROOT / "backend" / "src"


def main() -> int:
    if str(_BACKEND_SRC) not in sys.path:
        sys.path.insert(0, str(_BACKEND_SRC))
    from prompts.prompt_cli import build_system_prompt_main

    return build_system_prompt_main()


if __name__ == "__main__":
    raise SystemExit(main())
