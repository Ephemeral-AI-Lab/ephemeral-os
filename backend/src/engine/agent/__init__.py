"""Ephemeral agent factory and lifecycle entrypoints."""

from engine.agent.factory import EphemeralAgent, spawn_agent
from engine.agent.lifecycle import EphemeralRunResult, run_ephemeral_agent

__all__ = [
    "EphemeralAgent",
    "EphemeralRunResult",
    "run_ephemeral_agent",
    "spawn_agent",
]
