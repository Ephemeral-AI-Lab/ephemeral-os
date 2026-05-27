"""Docker SDK client wrapper and tuned ``docker run`` flag constants.

The SDK import is lazy: callers obtain a client via :func:`get_docker_client`
so importing this module does not require ``docker`` to be installed.
"""

from __future__ import annotations

import os
from typing import Any

# Default capability set for the Docker run invocation.
#
# Sized to unblock two daemon-internal kernel surfaces:
#   * ``CAP_SYS_ADMIN`` — ``unshare -Urm`` + the mount-syscall overlay mount
#     the EphemeralOS runtime constructs (``overlay/kernel_mount.py``)
#     plus the isolated-workspace ``setns(CLONE_NEWUSER|CLONE_NEWNS)`` flow.
#   * ``CAP_NET_ADMIN`` — the isolated-workspace network module (``eos-shared0``
#     bridge, MASQUERADE/IMDS nftables rules, per-workspace veth wiring) makes
#     ``ip link`` / ``nft`` / rtnetlink calls in the daemon's own netns.
#     ``CAP_SYS_ADMIN`` is NOT a superset of ``CAP_NET_ADMIN`` for these
#     operations, so the cap must be granted explicitly.
# Sufficiency is verified by ``backend/scripts/preflight_docker_a2_caps.sh``
# on a Linux CI host.
DEFAULT_RUN_FLAGS: tuple[str, ...] = (
    "--cap-add=SYS_ADMIN",
    "--cap-add=NET_ADMIN",
    "--security-opt",
    "seccomp=unconfined",
    "--security-opt",
    "apparmor=unconfined",
)

# Privileged escape hatch (oversized blast radius — escape only).
PRIVILEGED_RUN_FLAGS: tuple[str, ...] = ("--privileged",)

# Capability-stripped escape hatch for negative precondition tests.
NO_PRIVILEGE_RUN_FLAGS: tuple[str, ...] = ()

OVERLAY_WRITABLE_TMPFS_TARGET = "/eos-mount-scratch"
DEFAULT_OVERLAY_WRITABLE_TMPFS_OPTIONS = "rw,size=2g,mode=1777"


def resolve_run_flags() -> tuple[str, ...]:
    """Pick the ``docker run`` flag set from env-var escape hatches.

    Precedence:
    1. ``EOS_DOCKER_PRIVILEGED=1`` → :data:`PRIVILEGED_RUN_FLAGS`
    2. ``EOS_DOCKER_NO_PRIVILEGE=1`` → :data:`NO_PRIVILEGE_RUN_FLAGS`
    3. otherwise → :data:`DEFAULT_RUN_FLAGS`
    """
    if os.environ.get("EOS_DOCKER_PRIVILEGED") == "1":
        base = PRIVILEGED_RUN_FLAGS
    elif os.environ.get("EOS_DOCKER_NO_PRIVILEGE") == "1":
        base = NO_PRIVILEGE_RUN_FLAGS
    else:
        base = DEFAULT_RUN_FLAGS
    return (*base, *_overlay_writable_tmpfs_flags())


def _overlay_writable_tmpfs_flags() -> tuple[str, ...]:
    if os.environ.get("EOS_DOCKER_DISABLE_OVERLAY_WRITABLE_TMPFS") == "1":
        return ()
    options = (
        os.environ.get("EOS_DOCKER_OVERLAY_WRITABLE_TMPFS_OPTIONS", "").strip()
        or DEFAULT_OVERLAY_WRITABLE_TMPFS_OPTIONS
    )
    return ("--tmpfs", f"{OVERLAY_WRITABLE_TMPFS_TARGET}:{options}")


def host_config_kwargs() -> dict[str, Any]:
    """Translate :func:`resolve_run_flags` into docker-py host_config kwargs.

    The Docker Python SDK ``containers.create(...)`` does not take the CLI
    string flags directly; it takes keyword arguments. This helper centralizes
    the translation so the CLI smoke script and the adapter share a single
    source of truth.
    """
    flags = resolve_run_flags()
    kwargs: dict[str, Any] = {}

    cap_add: list[str] = []
    security_opt: list[str] = []
    tmpfs: dict[str, str] = {}
    i = 0
    while i < len(flags):
        token = flags[i]
        if token == "--privileged":
            kwargs["privileged"] = True
            i += 1
        elif token.startswith("--cap-add="):
            cap_add.append(token.split("=", 1)[1])
            i += 1
        elif token == "--security-opt":
            security_opt.append(flags[i + 1])
            i += 2
        elif token == "--tmpfs":
            target, _, options = flags[i + 1].partition(":")
            if target:
                tmpfs[target] = options
            i += 2
        else:
            i += 1

    if cap_add and not kwargs.get("privileged"):
        kwargs["cap_add"] = cap_add
    if security_opt and not kwargs.get("privileged"):
        kwargs["security_opt"] = security_opt
    if tmpfs:
        kwargs["tmpfs"] = tmpfs
    return kwargs


def get_docker_client() -> Any:
    """Return a connected ``docker.DockerClient`` from the local daemon.

    Raises ``RuntimeError`` if the ``docker`` SDK is not installed; the import
    is intentionally lazy so environments without the SDK can still import the
    provider package.
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
    "DEFAULT_OVERLAY_WRITABLE_TMPFS_OPTIONS",
    "OVERLAY_WRITABLE_TMPFS_TARGET",
    "get_async_docker_client",
    "get_docker_client",
    "host_config_kwargs",
    "resolve_run_flags",
]
