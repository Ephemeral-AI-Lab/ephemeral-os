"""Plugin runtime types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ephemeralos.mcp.types import McpServerConfig
from ephemeralos.plugins.schemas import PluginManifest
from ephemeralos.skills.types import SkillDefinition


@dataclass(frozen=True)
class LoadedPlugin:
    """A loaded plugin and its contributed artifacts."""

    manifest: PluginManifest
    path: Path
    enabled: bool
    skills: list[SkillDefinition] = field(default_factory=list)
    hooks: dict[str, list] = field(default_factory=dict)
    mcp_servers: dict[str, McpServerConfig] = field(default_factory=dict)
    commands: list[SkillDefinition] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def description(self) -> str:
        return self.manifest.description
