#!/usr/bin/env python3
"""Print and save representative user prompts for all members of a team."""

from __future__ import annotations

import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_SRC = _ROOT / "backend" / "src"


def main() -> int:
    if str(_BACKEND_SRC) not in sys.path:
        sys.path.insert(0, str(_BACKEND_SRC))
    from prompts.prompt_cli import dump_team_user_prompts_main

    return dump_team_user_prompts_main()


if __name__ == "__main__":
    raise SystemExit(main())
