"""Docker SDK client wrapper and tuned ``docker run`` flag constants.

The SDK import is lazy: callers obtain a client via :func:`get_docker_client`
so importing this module does not require ``docker`` to be installed (e.g. on
darwin development hosts that default to Daytona).
"""

from __future__ import annotations

import os
from typing import Any

# Default capability set for the Docker run invocation.
#
# Sized to unblock ``unshare -Urm`` + the single-lowerdir overlay mount the
# EphemeralOS runtime constructs (``execution/overlay/kernel_mount.py``).
# Sufficiency is verified by ``backend/scripts/preflight_docker_a2_caps.sh``
# on a Linux CI host.
DEFAULT_RUN_FLAGS: tuple[str, ...] = (
    "--cap-add=SYS_ADMIN",
    "--security-opt",
    "seccomp=unconfined",
    "--security-opt",
    "apparmor=unconfined",
)

# Privileged escape hatch (oversized blast radius — escape only).
PRIVILEGED_RUN_FLAGS: tuple[str, ...] = ("--privileged",)

# Capability-stripped escape hatch (forfeits overlay perf; COPY_BACKED only).
NO_PRIVILEGE_RUN_FLAGS: tuple[str, ...] = ()


def resolve_run_flags() -> tuple[str, ...]:
    """Pick the ``docker run`` flag set from env-var escape hatches.

    Precedence:
    1. ``EOS_DOCKER_PRIVILEGED=1`` → :data:`PRIVILEGED_RUN_FLAGS`
    2. ``EOS_DOCKER_NO_PRIVILEGE=1`` → :data:`NO_PRIVILEGE_RUN_FLAGS`
    3. otherwise → :data:`DEFAULT_RUN_FLAGS`
    """
    if os.environ.get("EOS_DOCKER_PRIVILEGED") == "1":
        return PRIVILEGED_RUN_FLAGS
    if os.environ.get("EOS_DOCKER_NO_PRIVILEGE") == "1":
        return NO_PRIVILEGE_RUN_FLAGS
    return DEFAULT_RUN_FLAGS


def host_config_kwargs() -> dict[str, Any]:
    """Translate :func:`resolve_run_flags` into docker-py host_config kwargs.

    The Docker Python SDK ``containers.create(...)`` does not take the CLI
    string flags directly; it takes keyword arguments. This helper centralizes
    the translation so the CLI smoke script and the adapter share a single
    source of truth.
    """
    flags = resolve_run_flags()
    kwargs: dict[str, Any] = {}

    if "--privileged" in flags:
        kwargs["privileged"] = True
        return kwargs

    cap_add: list[str] = []
    security_opt: list[str] = []
    i = 0
    while i < len(flags):
        token = flags[i]
        if token.startswith("--cap-add="):
            cap_add.append(token.split("=", 1)[1])
            i += 1
        elif token == "--security-opt":
            security_opt.append(flags[i + 1])
            i += 2
        else:
            i += 1

    if cap_add:
        kwargs["cap_add"] = cap_add
    if security_opt:
        kwargs["security_opt"] = security_opt
    return kwargs


def get_docker_client() -> Any:
    """Return a connected ``docker.DockerClient`` from the local daemon.

    Raises ``RuntimeError`` if the ``docker`` SDK is not installed; the import
    is intentionally lazy so darwin hosts can import the provider package
    without the dependency.
    """
    try:
        import docker  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "docker SDK not installed. Install with `pip install docker` "
            "or use the [docker] optional dependency group."
        ) from exc
    return docker.from_env()


def get_async_docker_client() -> Any:
    """Return the same sync client; docker-py has no first-class async API.

    The adapter wraps blocking SDK calls in ``asyncio.to_thread`` rather than
    pulling in a second client library. Returning the sync client keeps the
    contract symmetric with :func:`get_docker_client`.
    """
    return get_docker_client()


__all__ = [
    "DEFAULT_RUN_FLAGS",
    "get_async_docker_client",
    "get_docker_client",
    "host_config_kwargs",
    "resolve_run_flags",
]
