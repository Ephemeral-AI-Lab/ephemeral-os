"""Data models for sandbox lifecycle management."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SandboxState(str, Enum):
    """Possible sandbox states."""

    UNKNOWN = "unknown"
    CREATING = "creating"
    STARTING = "starting"
    STARTED = "started"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    DELETING = "deleting"


class SandboxInfo(BaseModel):
    """Serialised sandbox representation."""

    id: str
    name: str = ""
    state: SandboxState = SandboxState.UNKNOWN
    image: str = ""
    snapshot: str = ""
    project_dir: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    cpu: int = 0
    memory: int = 0
    disk: int = 0

    @property
    def managed_by_app(self) -> bool:
        return self.labels.get("managed_by") == "ephemeralos"


class SnapshotInfo(BaseModel):
    """Available Daytona snapshot."""

    id: str
    name: str = ""
    created_at: str = ""
    size: str = ""
