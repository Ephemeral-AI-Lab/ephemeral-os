"""One-call provider bootstrap for the Docker adapter.

Symmetric with :mod:`sandbox.provider.daytona.bootstrap`. The
:mod:`sandbox.provider.bootstrap` dispatcher routes to this function when
``EOS_SANDBOX_PROVIDER=docker`` or when running on Linux without an override.
"""

from __future__ import annotations

from sandbox.provider.docker.adapter import DockerProviderAdapter
from sandbox.provider.registry import set_default_provider


def bootstrap_docker_provider() -> None:
    """Register the Docker adapter as the process-wide default provider."""
    set_default_provider(DockerProviderAdapter())


__all__ = ["bootstrap_docker_provider"]
