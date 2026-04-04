"""Bridge configuration types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


DEFAULT_SESSION_TIMEOUT_MS = 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class WorkData:
    """Work item metadata."""

    type: Literal["session", "healthcheck"]
    id: str


@dataclass(frozen=True)
class WorkSecret:
    """Decoded work secret."""

    version: int
    session_ingress_token: str
    api_base_url: str


@dataclass(frozen=True)
class BridgeConfig:
    """Minimal bridge configuration."""

    dir: str
    machine_name: str
    max_sessions: int = 1
    verbose: bool = False
    session_timeout_ms: int = DEFAULT_SESSION_TIMEOUT_MS
