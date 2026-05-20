"""Sandbox provider config.

Environment bindings use ``EOS__SANDBOX__...``. Compatibility bindings include
``EOS_SANDBOX_PROVIDER``, ``DAYTONA_API_KEY``, ``DAYTONA_API_URL``,
``DAYTONA_TARGET``, and the legacy ``EPHEMERALOS_SANDBOX_*`` defaults.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from config.base import ModuleConfigBase


class DockerConfig(ModuleConfigBase):
    """Docker-provider settings."""

    daemon_tcp: bool = True
    privileged: bool = False
    no_privilege: bool = False
    default_snapshot: str = ""


class DaytonaConfig(ModuleConfigBase):
    """Daytona-provider settings and env-sourced credentials."""

    api_key: str = ""
    api_url: str = ""
    target: str = ""
    tcp_host: str = ""
    tcp_port: int | None = Field(default=None, ge=1, le=65535)
    default_image: str = ""
    default_snapshot: str = ""


class SandboxConfig(ModuleConfigBase):
    """Sandbox provider defaults and provider-specific config."""

    default_provider: Literal["docker", "daytona"] = "docker"
    timeout_s: float = Field(default=300.0, gt=0)
    runtime_client_timeout_s: float = Field(default=600.0, gt=0)
    docker: DockerConfig = Field(default_factory=DockerConfig)
    daytona: DaytonaConfig = Field(default_factory=DaytonaConfig)
