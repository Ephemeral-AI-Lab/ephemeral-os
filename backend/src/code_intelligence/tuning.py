"""Central tuning knobs for code-intelligence runtime behavior."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CodeIntelligenceTuning:
    rename_preview_cache_max: int = 32
    scope_recent_seconds: float = 300.0
    grep_match_cap: int = 500
    codeact_default_timeout: int = 900
    codeact_write_timeout: int = 5


CODE_INTELLIGENCE_TUNING = CodeIntelligenceTuning()


__all__ = [
    "CODE_INTELLIGENCE_TUNING",
    "CodeIntelligenceTuning",
]
