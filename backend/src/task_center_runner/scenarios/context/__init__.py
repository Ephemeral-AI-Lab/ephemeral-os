"""Context-engine recipe scenarios.

Build specific (goal, iteration, attempt) configurations and assert on the
rendered ``LaunchBundle`` shape (block count, headings, priority order)
captured via ``squad/prompt_inspector.py``. The model API is bypassed
entirely — these are pure recipe-output assertions.

Reference scenarios for this subpackage land alongside the prompt-inspector
extensions tracked in the design doc.
"""

from __future__ import annotations

__all__: list[str] = []
