"""Docker implementation of the sandbox provider adapter.

This package mirrors the public shape of :mod:`sandbox.provider.daytona`:
``DockerProviderAdapter`` implements every method on
:class:`sandbox.provider.protocol.ProviderAdapter`, and
``bootstrap_docker_provider`` registers it as the process-wide default.

Environment variables
---------------------

``EOS_SANDBOX_PROVIDER`` (authoritative)
    ``docker`` | ``daytona``. When unset the dispatcher in
    :mod:`sandbox.provider.bootstrap` defaults to Docker.

``EOS_DOCKER_PRIVILEGED`` = ``1``
    Run containers with ``--privileged`` instead of the minimum-cap default
    (CAP_SYS_ADMIN + unconfined seccomp/apparmor). Escape hatch only — grants
    every cap and all device access, oversized blast radius.

``EOS_DOCKER_NO_PRIVILEGE`` = ``1``
    Run containers with zero added caps. This is expected to fail the sandbox
    startup precondition for normal command execution; keep it only for
    explicit capability-negative tests.

``EOS_DOCKER_DISABLE_OVERLAY_WRITABLE_TMPFS`` = ``1``
    Do not mount the default overlay writable tmpfs. Useful only when a
    runtime forbids tmpfs mounts; Docker Desktop hosts normally need the
    tmpfs writable path to keep ``PRIVATE_NAMESPACE`` viable.

``EOS_DOCKER_OVERLAY_WRITABLE_TMPFS_OPTIONS``
    Override the default ``/eos-mount-scratch`` tmpfs options
    (``rw,size=2g,mode=1777``).

Env-var precedence
------------------

``EOS_SANDBOX_PROVIDER`` is the **single source of truth** for provider
selection. Presence of ``DAYTONA_API_KEY`` does NOT auto-select Daytona; if
``EOS_SANDBOX_PROVIDER=docker`` and ``DAYTONA_API_KEY`` is set, the dispatcher
logs once at startup: ``INFO: Daytona credentials detected but provider=docker;
ignoring DAYTONA_*``.

macOS caveat
------------

Docker is the default sandbox provider, including on macOS. Docker Desktop's
Linux VM UID-mapping and overlay-on-overlay2 storage driver may prevent a
kernel overlay mount on the container root filesystem from succeeding even
with CAP_SYS_ADMIN + unconfined seccomp. The Docker provider therefore mounts
``/eos-mount-scratch`` as tmpfs by default, and command exec allocates
per-run ``upper/`` plus ``work/`` dirs under the canonical overlay writable
root. Normal Docker Desktop runs should report ``mount_mode=private_namespace``.
Disabling the overlay writable tmpfs can make those runs fail the hard overlay
precondition.
"""

from __future__ import annotations

from sandbox.provider.docker.adapter import DockerProviderAdapter
from sandbox.provider.docker.bootstrap import bootstrap_docker_provider

__all__ = [
    "DockerProviderAdapter",
    "bootstrap_docker_provider",
]
