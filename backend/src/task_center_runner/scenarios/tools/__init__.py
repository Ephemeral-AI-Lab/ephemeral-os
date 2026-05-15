"""Tool execution, gate-hook guardrail, and notification scenarios.

Verify that submission gate hooks reject the wrong calls, pre/post hook
pipelines compose correctly, ``tool_call_limit`` triggers ``RESOURCE_LIMIT``,
dispatch validation enforces terminal-tool exclusivity, and notification
rules fire at the expected turn.

Reference scenarios for this subpackage will land alongside the executor
actions they require (e.g. ``submit_execution_handoff`` after edit). See
``docs/wiki/live-e2e-scenario-suite-design.md``.
"""

from __future__ import annotations

__all__: list[str] = []
