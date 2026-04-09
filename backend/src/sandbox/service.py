"""Sandbox service — Daytona sandbox lifecycle management.

Wraps the Daytona SDK to provide create/start/stop/delete/list operations
with error handling, git bootstrapping, and optional CI warmup hooks.

Modeled after the synthetic-os sandbox_service for API compatibility.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from sandbox.credentials import load_credentials
from sandbox.exc import DaytonaUnavailableError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Labels & constants
# ---------------------------------------------------------------------------

_APP_MANAGED_BY = "ephemeralos"
_APP_CREATED_VIA = "api"
_SNAPSHOT_LABEL = "ephemeralos_snapshot"
_IMAGE_LABEL = "ephemeralos_image"
_LIST_PAGE_LIMIT = 100
_SNAPSHOT_PAGE_LIMIT = 100
_SANDBOX_TIMEOUT_SECONDS = 180.0

# ---------------------------------------------------------------------------
# Git bootstrap script — installs git if missing
# ---------------------------------------------------------------------------

_GIT_BOOTSTRAP = r"""
set -e
if command -v git >/dev/null 2>&1; then exit 0; fi
echo "[sandbox] Installing git..."
if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq git
elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache git
elif command -v microdnf >/dev/null 2>&1; then
    microdnf install -y git
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y git
elif command -v yum >/dev/null 2>&1; then
    yum install -y git
else
    echo "[sandbox] No package manager found — git not installed" >&2
    exit 1
fi
echo "[sandbox] git installed"
"""

# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------

_client_lock = threading.Lock()
_cached_client: Any | None = None
_cached_client_key: tuple[str, str, str] | None = None


def acquire_client() -> Any:
    """Return a cached Daytona client, creating one if config changed."""
    global _cached_client, _cached_client_key

    api_key, api_url, target = load_credentials()
    if not api_key or not api_url:
        raise DaytonaUnavailableError(
            "Daytona is not configured. Set DAYTONA_API_KEY and DAYTONA_API_URL."
        )
    current_key = (api_key, api_url, target)

    with _client_lock:
        if _cached_client is not None and _cached_client_key == current_key:
            return _cached_client

        try:
            from daytona_sdk import Daytona, DaytonaConfig
        except ImportError as exc:
            raise DaytonaUnavailableError(
                "Daytona SDK not installed. Run: pip install daytona-sdk"
            ) from exc

        cfg_kwargs: dict[str, str] = {"api_key": api_key, "api_url": api_url}
        if target:
            cfg_kwargs["target"] = target
        cfg = DaytonaConfig(**cfg_kwargs)
        _cached_client = Daytona(cfg)
        _cached_client_key = current_key
        logger.info("Daytona client created (api_url=%s)", api_url)
        return _cached_client


def fetch_sandbox(sandbox_id: str) -> Any:
    """Fetch a pre-created sandbox by ID."""
    client = acquire_client()
    sandbox = client.get(sandbox_id)
    if sandbox is None:
        raise ValueError(f"Sandbox '{sandbox_id}' not found")
    return sandbox


# ---------------------------------------------------------------------------
# Helpers
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


def _daytona_classes():
    """Import and return Daytona SDK classes."""
    try:
        from daytona_sdk import (
            CreateSandboxFromImageParams,
            CreateSandboxFromSnapshotParams,
            Daytona,
            DaytonaConfig,
        )
    except ImportError:
        try:
            from daytona import (
                CreateSandboxFromImageParams,
                CreateSandboxFromSnapshotParams,
                Daytona,
                DaytonaConfig,
            )
        except ImportError as exc:
            raise DaytonaUnavailableError(
                "Daytona SDK not installed. Run: pip install daytona-sdk"
            ) from exc

    return Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams, CreateSandboxFromImageParams


def _paginate_all(list_fn: Any, limit: int) -> list[Any]:
    """Exhaust a paginated Daytona SDK list method and return all items."""
    first_page = list_fn(limit=limit)
    items = list(getattr(first_page, "items", []) or [])
    current_page = int(getattr(first_page, "page", 1) or 1)
    total_pages = int(getattr(first_page, "total_pages", 1) or 1)
    for page in range(current_page + 1, total_pages + 1):
        response = list_fn(page=page, limit=limit)
        items.extend(list(getattr(response, "items", []) or []))
    return items


# ---------------------------------------------------------------------------
# SandboxProxy
# ---------------------------------------------------------------------------


class SandboxProxy:
    """Typed view over a Daytona SDK sandbox object."""

    __slots__ = ("_raw",)

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    @property
    def id(self) -> str:
        return getattr(self._raw, "id", "")

    @property
    def name(self) -> str:
        return getattr(self._raw, "name", "")

    @property
    def created_at(self) -> Any:
        return getattr(self._raw, "created_at", None)

    @property
    def labels(self) -> dict[str, str]:
        raw = getattr(self._raw, "labels", None) or {}
        return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}

    @property
    def state(self) -> str:
        raw_state = getattr(self._raw, "state", None)
        if raw_state is None:
            return "unknown"
        s = str(getattr(raw_state, "value", raw_state)).strip()
        if not s:
            return "unknown"
        if s.lower().startswith("sandboxstate."):
            s = s.split(".", 1)[1]
        return s.lower()

    @property
    def image(self) -> str | None:
        labels = self.labels
        for key in (_SNAPSHOT_LABEL, _IMAGE_LABEL):
            if labels.get(key):
                return labels[key]
        for attr in ("image", "image_name", "snapshot"):
            val = _normalize_optional_text(getattr(self._raw, attr, None))
            if val:
                return val
        return None

    @property
    def managed_by_app(self) -> bool:
        return self.labels.get("managed_by") == _APP_MANAGED_BY

    def refresh(self) -> None:
        fn = getattr(self._raw, "refresh_data", None)
        if callable(fn):
            fn()

    def ensure_git(self) -> None:
        """Install git in the sandbox if missing."""
        try:
            resp = self._raw.process.exec(
                "command -v git >/dev/null 2>&1 && echo ok || echo missing",
                timeout=10,
            )
            if "ok" in (resp.result or ""):
                return
            self._raw.process.exec(_GIT_BOOTSTRAP, timeout=120)
        except Exception:
            logger.warning("Git bootstrap failed for sandbox %s", self.id)

    def serialize(self, *, assigned_agents: list[str] | None = None) -> dict[str, Any]:
        """Serialize to the shape the frontend expects."""
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "image": self.image,
            "labels": self.labels,
            "created_at": self.created_at,
            "managed_by_app": self.managed_by_app,
            "assigned_agents": list(assigned_agents or []),
        }


# ---------------------------------------------------------------------------
# SandboxService
# ---------------------------------------------------------------------------


class SandboxService:
    """Manages Daytona sandbox lifecycle.

    All public methods are synchronous and return plain dicts matching
    the API response shapes. The router wraps them with asyncio.to_thread
    when needed.
    """

    # -- Health ---------------------------------------------------------------

    def get_health(self) -> dict[str, Any]:
        """Check Daytona availability and configuration."""
        api_key, api_url, target = load_credentials()
        if not api_key or not api_url:
            return {
                "configured": False,
                "available": False,
                "api_url": api_url or None,
                "target": target or None,
                "detail": "Set DAYTONA_API_KEY and DAYTONA_API_URL to connect.",
                "default_image": None,
            }
        try:
            client = acquire_client()
            client.list(limit=1)
            return {
                "configured": True,
                "available": True,
                "api_url": api_url,
                "target": target or None,
                "detail": None,
                "default_image": None,
            }
        except Exception as exc:
            return {
                "configured": True,
                "available": False,
                "api_url": api_url,
                "target": target or None,
                "detail": str(exc),
                "default_image": None,
            }

    # -- List -----------------------------------------------------------------

    def list_sandboxes(self) -> list[dict[str, Any]]:
        """List all sandboxes (both managed and external)."""
        client = acquire_client()
        sandboxes = [
            SandboxProxy(sb).serialize() for sb in _paginate_all(client.list, _LIST_PAGE_LIMIT)
        ]
        sandboxes.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return sandboxes

    def _get_proxy(self, sandbox_id: str) -> SandboxProxy:
        """Fetch a sandbox by ID and return a typed proxy."""
        raw = fetch_sandbox(sandbox_id)
        return SandboxProxy(raw)

    def get_sandbox(self, sandbox_id: str) -> dict[str, Any]:
        """Get a single sandbox by ID."""
        return self._get_proxy(sandbox_id).serialize()

    def get_sandbox_object(self, sandbox_id: str) -> Any:
        """Return the raw Daytona SDK sandbox object."""
        return self._get_proxy(sandbox_id)._raw

    def get_build_logs_url(self, sandbox_id: str) -> str | None:
        """Return the Daytona build-logs URL for a sandbox when available."""
        raw = self.get_sandbox_object(sandbox_id)
        sandbox_api = getattr(raw, "_sandbox_api", None)
        if sandbox_api is None or not hasattr(sandbox_api, "get_build_logs_url"):
            return None
        try:
            result = sandbox_api.get_build_logs_url(sandbox_id)
        except Exception:
            logger.debug("Failed to fetch build logs URL for sandbox %s", sandbox_id, exc_info=True)
            return None
        url = getattr(result, "url", None)
        return str(url).strip() or None

    # -- Lifecycle ------------------------------------------------------------

    def create_sandbox(
        self,
        *,
        name: str,
        snapshot: str | None = None,
        image: str | None = None,
        language: str = "python",
        env_vars: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a new sandbox."""
        normalized_name = _normalize_optional_text(name)
        normalized_snapshot = _normalize_optional_text(snapshot)
        normalized_image = _normalize_optional_text(image)
        if not normalized_name:
            raise ValueError("Sandbox name is required")
        if normalized_snapshot and normalized_image:
            raise ValueError("Pass either snapshot or image, not both.")

        clean_env = _normalize_dict(env_vars)
        clean_labels = _normalize_dict(labels)
        clean_labels["managed_by"] = _APP_MANAGED_BY
        clean_labels["created_via"] = _APP_CREATED_VIA
        if normalized_snapshot:
            clean_labels[_SNAPSHOT_LABEL] = normalized_snapshot
        if normalized_image:
            clean_labels[_IMAGE_LABEL] = normalized_image

        client = acquire_client()
        _, _, CreateSandboxFromSnapshotParams, CreateSandboxFromImageParams = _daytona_classes()

        if normalized_image:
            params = CreateSandboxFromImageParams(
                name=normalized_name,
                image=normalized_image,
                language=language,
                auto_stop_interval=0,
                env_vars=clean_env or None,
                labels=clean_labels,
                ephemeral=False,
            )
        else:
            params = CreateSandboxFromSnapshotParams(
                name=normalized_name,
                snapshot=normalized_snapshot,
                language=language,
                auto_stop_interval=0,
                env_vars=clean_env or None,
                labels=clean_labels,
                ephemeral=False,
            )

        raw = client.create(params, timeout=_SANDBOX_TIMEOUT_SECONDS)
        sb = SandboxProxy(raw)
        sb.refresh()
        sb.ensure_git()

        return sb.serialize(assigned_agents=[])

    def start_sandbox(self, sandbox_id: str) -> dict[str, Any]:
        """Start a stopped sandbox."""
        sb = self._get_proxy(sandbox_id)
        if sb.state == "started":
            return sb.serialize()

        sb._raw.start(timeout=_SANDBOX_TIMEOUT_SECONDS)
        sb.refresh()
        sb.ensure_git()
        sb.refresh()

        return sb.serialize()

    def stop_sandbox(self, sandbox_id: str) -> dict[str, Any]:
        """Stop a running sandbox."""
        sb = self._get_proxy(sandbox_id)
        sb._raw.stop(timeout=60)
        sb.refresh()
        return sb.serialize()

    def delete_sandbox(self, sandbox_id: str) -> None:
        """Delete a sandbox."""
        sb = self._get_proxy(sandbox_id)
        sb._raw.delete(timeout=_SANDBOX_TIMEOUT_SECONDS)
        logger.info("Sandbox deleted: %s", sandbox_id)

    # -- Snapshots ------------------------------------------------------------

    def list_snapshots(self) -> list[dict[str, Any]]:
        """List available Daytona snapshots."""
        client = acquire_client()
        snapshot_api = getattr(client, "snapshot", None)
        if snapshot_api and hasattr(snapshot_api, "list"):
            items = _paginate_all(snapshot_api.list, _SNAPSHOT_PAGE_LIMIT)
        elif hasattr(client, "list_snapshots"):
            items = _paginate_all(client.list_snapshots, _SNAPSHOT_PAGE_LIMIT)
        else:
            logger.warning("Daytona client has no snapshot listing API")
            return []
        return [
            {
                "name": getattr(s, "name", ""),
                "state": str(getattr(s, "state", "unknown")),
                "image_name": getattr(s, "image_name", None),
            }
            for s in items
        ]

    # -- Preview URLs ---------------------------------------------------------

    def get_signed_preview_url(self, sandbox_id: str, port: int) -> dict[str, Any]:
        """Get a signed preview URL for a sandbox port."""
        sb = self.get_sandbox_object(sandbox_id)
        try:
            result = sb.create_signed_preview_url(port)
            return {
                "url": result.url,
                "token": result.token,
                "port": result.port,
            }
        except AttributeError:
            url = sb.get_preview_url(port)
            return {"url": url, "token": "", "port": port}

    # -- File operations ------------------------------------------------------

    def list_files_recursive(
        self,
        sandbox_id: str,
        root: str = "/workspace",
        max_depth: int = 10,
        max_items: int = 10_000,
    ) -> list[dict[str, Any]]:
        """List files recursively in a sandbox."""
        sb = self.get_sandbox_object(sandbox_id)
        fs = getattr(sb, "fs", None)
        list_files_fn = getattr(fs, "list_files", None)
        if not callable(list_files_fn):
            raise RuntimeError("Sandbox filesystem API is not available")

        import posixpath

        results: list[dict[str, Any]] = []
        pending: list[tuple[str, int]] = [(root, 0)]

        while pending:
            if len(results) >= max_items:
                break
            current, depth = pending.pop()
            entries = list_files_fn(current) or []
            for entry in entries:
                if len(results) >= max_items:
                    break
                name = getattr(entry, "name", None)
                if not isinstance(name, str) or not name or name in {".", ".."}:
                    continue
                child = posixpath.join(current, name)
                is_dir = bool(getattr(entry, "is_dir", False))
                results.append({"path": child, "name": name, "is_dir": is_dir})
                if is_dir and depth < max_depth:
                    pending.append((child, depth + 1))

        results.sort(key=lambda item: item["path"])
        return results
