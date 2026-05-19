"""Docker implementation of the sandbox provider adapter.

This package mirrors the public shape of :mod:`sandbox.provider.daytona`:
``DockerProviderAdapter`` implements every method on
:class:`sandbox.provider.protocol.ProviderAdapter`, and
``bootstrap_docker_provider`` registers it as the process-wide default.

Environment variables
---------------------

``EOS_SANDBOX_PROVIDER`` (authoritative)
    ``docker`` | ``daytona``. When unset the dispatcher in
    :mod:`sandbox.provider.bootstrap` picks per-platform defaults:
    Linux→docker, darwin→daytona.

``EOS_DOCKER_PRIVILEGED`` = ``1``
    Run containers with ``--privileged`` instead of the minimum-cap default
    (CAP_SYS_ADMIN + unconfined seccomp/apparmor). Escape hatch only — grants
    every cap and all device access, oversized blast radius.

``EOS_DOCKER_NO_PRIVILEGE`` = ``1``
    Run containers with zero added caps. Forfeits overlay-mount performance
    (every exec falls through to ``COPY_BACKED``); intended for
    hostile-multi-tenant configurations where the layer-stack perf story is
    not required.

Env-var precedence
------------------

``EOS_SANDBOX_PROVIDER`` is the **single source of truth** for provider
selection. Presence of ``DAYTONA_API_KEY`` does NOT auto-select Daytona; if
``EOS_SANDBOX_PROVIDER=docker`` and ``DAYTONA_API_KEY`` is set, the dispatcher
logs once at startup: ``INFO: Daytona credentials detected but provider=docker;
ignoring DAYTONA_*``.

macOS caveat
------------

macOS Docker Desktop is **unsupported as the default** sandbox provider.
The dispatcher defaults darwin to Daytona. Users who opt in via
``EOS_SANDBOX_PROVIDER=docker`` on darwin should expect ``mount_mode=COPY_BACKED``
for some execs — Docker Desktop's Linux VM UID-mapping and overlay-on-overlay2
storage driver may prevent the kernel overlay mount inside ``unshare -Urm``
from succeeding even with CAP_SYS_ADMIN + unconfined seccomp.
"""

from __future__ import annotations

from sandbox.provider.docker.adapter import DockerProviderAdapter
from sandbox.provider.docker.bootstrap import bootstrap_docker_provider

__all__ = [
    "DockerProviderAdapter",
    "bootstrap_docker_provider",
]
