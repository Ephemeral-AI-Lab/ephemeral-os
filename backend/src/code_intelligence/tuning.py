"""Central tuning knobs for code-intelligence runtime behavior."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

SANDBOX_PYTHON_CONCURRENCY_ENV = "CI_LSP_SANDBOX_PYTHON_CONCURRENCY"


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


@dataclass(frozen=True)
class CodeIntelligenceTuning:
    rename_preview_cache_max: int = 32
    sandbox_python_concurrency: int = field(
        default_factory=lambda: _env_int(SANDBOX_PYTHON_CONCURRENCY_ENV, 8),
    )
    scope_recent_seconds: float = 300.0
    grep_match_cap: int = 500
    codeact_default_timeout: int = 900
    codeact_write_timeout: int = 5


CODE_INTELLIGENCE_TUNING = CodeIntelligenceTuning()


__all__ = [
    "CODE_INTELLIGENCE_TUNING",
    "CodeIntelligenceTuning",
    "SANDBOX_PYTHON_CONCURRENCY_ENV",
]
