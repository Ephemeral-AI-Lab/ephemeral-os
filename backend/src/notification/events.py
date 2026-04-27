"""Notification stream event types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SystemNotification:
    """Engine-generated notification visible to the user and the agent."""

    text: str
    agent_name: str = ""
    run_id: str = ""
