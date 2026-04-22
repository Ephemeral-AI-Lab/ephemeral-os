"""Console-script shim for prompt inspection utilities."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_SRC = Path(__file__).resolve().parent / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from prompt.prompt_cli import (  # noqa: E402
    build_system_prompt_main,
    dump_team_system_prompts_main,
    dump_team_user_prompts_main,
)

__all__ = [
    "build_system_prompt_main",
    "dump_team_system_prompts_main",
    "dump_team_user_prompts_main",
]
