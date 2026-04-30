"""Back-compat shim — prefer ``sandbox.lifecycle`` / ``sandbox.client`` for new code.

The original kitchen-sink ``sandbox.service`` module has been carved into:

* :mod:`sandbox.lifecycle.service` — :class:`SandboxService`
* :mod:`sandbox.lifecycle.proxy`   — :class:`SandboxProxy`
* :mod:`sandbox.client.sync`       — ``acquire_client``, ``fetch_sandbox`` and
                                     normalize/paginate helpers

This module re-exports the prior public surface so existing importers keep
working unchanged. New code should import from the focused sub-modules
directly.
"""

from __future__ import annotations

from sandbox.client.sync import (
    _APP_CREATED_VIA,
    _APP_MANAGED_BY,
    _IMAGE_LABEL,
    _LIST_PAGE_LIMIT,
    _SANDBOX_TIMEOUT_SECONDS,
    _SNAPSHOT_LABEL,
    _SNAPSHOT_PAGE_LIMIT,
    _daytona_classes,
    _normalize_dict,
    _normalize_optional_text,
    _paginate_all,
    _timeout_seconds_from_env,
    acquire_client,
    fetch_sandbox,
)
from sandbox.lifecycle.proxy import SandboxProxy
from sandbox.lifecycle.service import SandboxService

__all__ = [
    "SandboxProxy",
    "SandboxService",
    "_APP_CREATED_VIA",
    "_APP_MANAGED_BY",
    "_IMAGE_LABEL",
    "_LIST_PAGE_LIMIT",
    "_SANDBOX_TIMEOUT_SECONDS",
    "_SNAPSHOT_LABEL",
    "_SNAPSHOT_PAGE_LIMIT",
    "_daytona_classes",
    "_normalize_dict",
    "_normalize_optional_text",
    "_paginate_all",
    "_timeout_seconds_from_env",
    "acquire_client",
    "fetch_sandbox",
]
