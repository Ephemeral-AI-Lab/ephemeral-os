"""Agent-loop tuning knobs.

``tool_call_limit`` is per-agent (declared in profile MDs and
``AgentDefinition``). The hard ceiling is the structural
``ceil(1.5 * tool_call_limit)``; no engine-wide knob remains.
"""

from __future__ import annotations

from config.base import ModuleConfigBase


class EngineConfig(ModuleConfigBase):
    """Engine-wide agent-loop tuning."""
