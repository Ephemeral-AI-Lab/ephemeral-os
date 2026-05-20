"""Provider dispatcher — selects Docker or Daytona at startup.

Picks the provider from ``EOS_SANDBOX_PROVIDER`` first, then from the central
``sandbox.default_provider`` config. Sentinel-gated so a second call with the
same resolved value is a silent no-op, and a second call with a *different*
value logs a warning and is also a no-op — see PLAN_v4 §6 Step 3 and §8.
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

_PROVIDER_BOOTSTRAPPED = False
_PROVIDER_BOOTSTRAP_LOCK = threading.Lock()
_FIRST_PROVIDER: str | None = None

_VALID_PROVIDERS = frozenset({"docker", "daytona"})


def _resolve_provider_name() -> str:
    raw = os.environ.get("EOS_SANDBOX_PROVIDER")
    if raw is not None:
        return raw.strip().lower()
    from config import get_central_config

    return get_central_config().sandbox.default_provider.strip().lower()


def bootstrap_sandbox_provider() -> None:
    """Select and bootstrap a provider; first call wins.

    Subsequent calls with the same env value are silent no-ops.
    Subsequent calls with a different env value log a warning and are no-ops.
    Rollback after the first call requires a process restart.
    """
    global _PROVIDER_BOOTSTRAPPED, _FIRST_PROVIDER

    with _PROVIDER_BOOTSTRAP_LOCK:
        name = _resolve_provider_name()

        if _PROVIDER_BOOTSTRAPPED:
            if name != _FIRST_PROVIDER:
                logger.warning(
                    "bootstrap_sandbox_provider called twice with different "
                    "sandbox provider config (first=%s, now=%s); ignoring",
                    _FIRST_PROVIDER,
                    name,
                )
            return

        if name not in _VALID_PROVIDERS:
            raise RuntimeError(
                f"Unknown EOS_SANDBOX_PROVIDER={name!r}; "
                f"expected one of {sorted(_VALID_PROVIDERS)}"
            )

        if name == "docker":
            from sandbox.provider.docker.bootstrap import bootstrap_docker_provider

            bootstrap_docker_provider()
        elif name == "daytona":
            from sandbox.provider.daytona.bootstrap import bootstrap_daytona_provider

            bootstrap_daytona_provider()

        if name == "docker" and os.environ.get("DAYTONA_API_KEY"):
            logger.info(
                "Daytona credentials detected but provider=docker; ignoring DAYTONA_*"
            )

        logger.info("sandbox provider = %s", name)
        _FIRST_PROVIDER = name
        _PROVIDER_BOOTSTRAPPED = True


def _reset_for_tests() -> None:
    """Reset sentinel state. Pytest-only helper for parametrized fixtures."""
    global _PROVIDER_BOOTSTRAPPED, _FIRST_PROVIDER
    with _PROVIDER_BOOTSTRAP_LOCK:
        _PROVIDER_BOOTSTRAPPED = False
        _FIRST_PROVIDER = None


__all__ = [
    "bootstrap_sandbox_provider",
]
