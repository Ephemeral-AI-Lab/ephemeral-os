"""Daytona sync client cache and helpers."""

from __future__ import annotations

import logging
import os
import threading
from inspect import Parameter, signature
from typing import Any

from sandbox.provider.daytona.client.credentials import (
    build_sdk_client,
    load_required_credentials,
)
from sandbox.provider.daytona.errors import DaytonaUnavailableError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Labels & constants shared by the Daytona adapter.
# ---------------------------------------------------------------------------

_APP_MANAGED_BY = "ephemeralos"
_APP_CREATED_VIA = "api"
_SNAPSHOT_LABEL = "ephemeralos_snapshot"
_IMAGE_LABEL = "ephemeralos_image"
_LIST_PAGE_LIMIT = 100
_SNAPSHOT_PAGE_LIMIT = 100


def _timeout_seconds_from_env() -> float:
    raw = os.getenv("EPHEMERALOS_SANDBOX_TIMEOUT_SECONDS")
    if not raw:
        return 300.0
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid EPHEMERALOS_SANDBOX_TIMEOUT_SECONDS=%r; using default", raw)
        return 300.0
    return max(value, 1.0)


_SANDBOX_TIMEOUT_SECONDS = _timeout_seconds_from_env()
# Health probes and unauthenticated lookups should not pay the cold-start budget;
# scheduler-degraded states must surface within seconds, not minutes.
_HEALTH_TIMEOUT_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------

_client_lock = threading.Lock()
_cached_client: Any | None = None
_cached_client_key: tuple[str, str, str] | None = None


def acquire_client() -> Any:
    """Return a cached Daytona client, creating one if config changed."""
    global _cached_client, _cached_client_key

    api_key, api_url, target = load_required_credentials(
        unavailable_cls=DaytonaUnavailableError,
        not_configured_message=(
            "Daytona is not configured. Set DAYTONA_API_KEY and DAYTONA_API_URL."
        ),
    )
    current_key = (api_key, api_url, target)

    stale_client: Any = None
    with _client_lock:
        if _cached_client is not None and _cached_client_key == current_key:
            return _cached_client

        if _cached_client is not None:
            stale_client = _cached_client

        _cached_client = build_sdk_client(
            "Daytona",
            api_key=api_key,
            api_url=api_url,
            target=target,
            unavailable_cls=DaytonaUnavailableError,
            not_installed_message="Daytona SDK not installed. Run: pip install daytona-sdk",
        )
        _cached_client_key = current_key
        new_client = _cached_client
        logger.info("Daytona client created (api_url=%s)", api_url)

    if stale_client is not None:
        try:
            close_fn = getattr(stale_client, "close", None)
            if callable(close_fn):
                close_fn()
        except Exception:
            logger.debug("Failed to close superseded Daytona client", exc_info=True)
    return new_client


def fetch_sandbox(sandbox_id: str) -> Any:
    """Fetch a pre-created sandbox by ID."""
    client = acquire_client()
    sandbox = _call_with_optional_timeout(
        client.get,
        sandbox_id,
        timeout=_SANDBOX_TIMEOUT_SECONDS,
    )
    if sandbox is None:
        raise ValueError(f"Sandbox '{sandbox_id}' not found")
    return sandbox


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_dict(payload: dict[str, str] | None) -> dict[str, str]:
    if not payload:
        return {}
    return {str(k).strip(): str(v).strip() for k, v in payload.items() if str(k).strip()}


def _creation_param_classes() -> tuple[Any, Any]:
    """Import and return Daytona SDK sandbox creation parameter classes."""
    try:
        from daytona_sdk import (
            CreateSandboxFromImageParams,
            CreateSandboxFromSnapshotParams,
        )
    except ImportError as exc:
        raise DaytonaUnavailableError(
            "Daytona SDK not installed. Run: pip install daytona-sdk"
        ) from exc

    return CreateSandboxFromSnapshotParams, CreateSandboxFromImageParams


_MAX_PAGINATION_PAGES = 1000  # WR-06: defense-in-depth cap


def _paginate_all(list_fn: Any, limit: int) -> list[Any]:
    """Exhaust a paginated Daytona SDK list method and return all items."""
    first_page = _call_with_optional_timeout(
        list_fn,
        limit=limit,
        timeout=_SANDBOX_TIMEOUT_SECONDS,
    )
    items = list(getattr(first_page, "items", []) or [])
    current_page = int(getattr(first_page, "page", 1) or 1)
    total_pages = int(getattr(first_page, "total_pages", 1) or 1)
    # WR-06: cap iteration so a corrupt SDK response (very large
    # total_pages) cannot loop until OOM or rate-limit.
    capped_total = min(total_pages, _MAX_PAGINATION_PAGES)
    if total_pages > _MAX_PAGINATION_PAGES:
        logger.warning(
            "Truncating Daytona pagination at %d pages (SDK reported %d)",
            _MAX_PAGINATION_PAGES,
            total_pages,
        )
    for page in range(current_page + 1, capped_total + 1):
        response = _call_with_optional_timeout(
            list_fn,
            page=page,
            limit=limit,
            timeout=_SANDBOX_TIMEOUT_SECONDS,
        )
        items.extend(list(getattr(response, "items", []) or []))
    return items


def _call_with_optional_timeout(
    fn: Any,
    *args: Any,
    timeout: float,
    **kwargs: Any,
) -> Any:
    """Call a Daytona SDK method, passing timeout only when supported.

    Daytona SDK versions differ here: create/start/stop/delete accept timeout,
    while current get/list methods do not. Keep the timeout on methods that
    expose it without breaking newer get/list signatures.
    """
    if _accepts_timeout(fn):
        kwargs["timeout"] = timeout
    return fn(*args, **kwargs)


def _accepts_timeout(fn: Any) -> bool:
    try:
        params = signature(fn).parameters.values()
    except (TypeError, ValueError):
        return True
    for param in params:
        if param.kind is Parameter.VAR_KEYWORD:
            return True
        if param.name == "timeout":
            return True
    return False


__all__ = [
    "acquire_client",
    "fetch_sandbox",
    "_call_with_optional_timeout",
    "_HEALTH_TIMEOUT_SECONDS",
    "_SANDBOX_TIMEOUT_SECONDS",
]
