"""Sandbox service — Daytona sandbox lifecycle management.

Wraps the Daytona SDK to provide create/start/stop/delete/list operations
with error handling, git bootstrapping, and optional CI warmup hooks.

Modeled after the synthetic-os sandbox_service for API compatibility.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

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
# Helpers
# ---------------------------------------------------------------------------

_client_lock = threading.Lock()
_cached_client: Any | None = None
_cached_client_key: tuple[str, str, str] | None = None


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_dict(payload: dict[str, str] | None) -> dict[str, str]:
    if not payload:
        return {}
    return {str(k).strip(): str(v).strip() for k, v in payload.items() if str(k).strip()}


def _require_settings() -> tuple[str, str, str]:
    """Return (api_key, api_url, target) from settings or env."""
    try:
        from config import load_settings
        settings = load_settings()
        api_key = (settings.daytona_api_key or "").strip()
        api_url = (settings.daytona_api_url or "").strip()
        target = (settings.daytona_target or "").strip()
    except Exception:
        api_key = api_url = target = ""

    if not api_key:
        api_key = os.environ.get("DAYTONA_API_KEY", "").strip()
    if not api_url:
        api_url = os.environ.get("DAYTONA_API_URL", "").strip()
    if not target:
        target = os.environ.get("DAYTONA_TARGET", "").strip()

    return api_key, api_url, target


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
            raise RuntimeError(
                "Daytona SDK not installed. Run: pip install daytona-sdk"
            ) from exc

    return Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams, CreateSandboxFromImageParams


def _get_daytona_client() -> Any:
    """Return a cached Daytona client, creating one if config changed."""
    global _cached_client, _cached_client_key

    api_key, api_url, target = _require_settings()
    if not api_key or not api_url:
        raise RuntimeError(
            "Daytona is not configured. Set DAYTONA_API_KEY and DAYTONA_API_URL."
        )
    current_key = (api_key, api_url, target)

    with _client_lock:
        if _cached_client is not None and _cached_client_key == current_key:
            return _cached_client

        Daytona, DaytonaConfig, *_ = _daytona_classes()
        cfg_kwargs: dict[str, str] = {"api_key": api_key, "api_url": api_url}
        if target:
            cfg_kwargs["target"] = target
        cfg = DaytonaConfig(**cfg_kwargs)
        _cached_client = Daytona(cfg)
        _cached_client_key = current_key
        logger.info("Daytona client created (api_url=%s)", api_url)
        return _cached_client


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


def _sandbox_state(sandbox: Any) -> str:
    """Normalize sandbox state to lowercase string."""
    raw_state = getattr(sandbox, "state", None)
    if raw_state is None:
        return "unknown"
    normalized = getattr(raw_state, "value", raw_state)
    state = str(normalized).strip()
    if not state:
        return "unknown"
    if state.lower().startswith("sandboxstate."):
        state = state.split(".", 1)[1]
    return state.lower()


def _sandbox_image(sandbox: Any) -> str | None:
    """Extract image name from sandbox labels/attributes."""
    labels = getattr(sandbox, "labels", None) or {}
    if isinstance(labels, dict):
        snapshot_label = labels.get(_SNAPSHOT_LABEL)
        if snapshot_label:
            return str(snapshot_label)
        image_label = labels.get(_IMAGE_LABEL)
        if image_label:
            return str(image_label)
    direct_image = _normalize_optional_text(getattr(sandbox, "image", None))
    if direct_image:
        return direct_image
    image_name = _normalize_optional_text(getattr(sandbox, "image_name", None))
    if image_name:
        return image_name
    snapshot = _normalize_optional_text(getattr(sandbox, "snapshot", None))
    return snapshot


def _serialize_sandbox(sandbox: Any, *, assigned_agents: list[str] | None = None) -> dict[str, Any]:
    """Serialize a Daytona SDK sandbox to the shape the frontend expects."""
    labels = getattr(sandbox, "labels", None) or {}
    if not isinstance(labels, dict):
        labels = {}
    return {
        "id": getattr(sandbox, "id", ""),
        "name": getattr(sandbox, "name", ""),
        "state": _sandbox_state(sandbox),
        "image": _sandbox_image(sandbox),
        "labels": {str(k): str(v) for k, v in labels.items()},
        "created_at": getattr(sandbox, "created_at", None),
        "managed_by_app": labels.get("managed_by") == _APP_MANAGED_BY,
        "assigned_agents": list(assigned_agents or []),
    }


def _refresh_sandbox_data(sandbox: Any) -> None:
    refresh = getattr(sandbox, "refresh_data", None)
    if callable(refresh):
        refresh()


def _ensure_git(sandbox: Any) -> None:
    """Install git in the sandbox if missing."""
    try:
        response = sandbox.process.exec(
            "command -v git >/dev/null 2>&1 && echo ok || echo missing",
            timeout=10,
        )
        if "ok" in (response.result or ""):
            return
        sandbox.process.exec(_GIT_BOOTSTRAP, timeout=120)
    except Exception:
        logger.warning("Git bootstrap failed for sandbox %s", getattr(sandbox, "id", "?"))


# ---------------------------------------------------------------------------
# SandboxService — synchronous methods returning dicts (matching synthetic-os)
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
        api_key, api_url, target = _require_settings()
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
            client = _get_daytona_client()
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
        client = _get_daytona_client()
        sandboxes = [
            _serialize_sandbox(sb)
            for sb in _paginate_all(client.list, _LIST_PAGE_LIMIT)
        ]
        sandboxes.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return sandboxes

    def get_sandbox(self, sandbox_id: str) -> dict[str, Any]:
        """Get a single sandbox by ID."""
        client = _get_daytona_client()
        sb = client.get(sandbox_id)
        if sb is None:
            raise ValueError(f"Sandbox '{sandbox_id}' not found")
        return _serialize_sandbox(sb)

    def get_sandbox_object(self, sandbox_id: str) -> Any:
        """Return the raw Daytona SDK sandbox object."""
        client = _get_daytona_client()
        sb = client.get(sandbox_id)
        if sb is None:
            raise ValueError(f"Sandbox '{sandbox_id}' not found")
        return sb

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

        client = _get_daytona_client()
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

        sb = client.create(params, timeout=_SANDBOX_TIMEOUT_SECONDS)
        _refresh_sandbox_data(sb)
        _ensure_git(sb)

        return _serialize_sandbox(sb, assigned_agents=[])

    def start_sandbox(self, sandbox_id: str) -> dict[str, Any]:
        """Start a stopped sandbox."""
        client = _get_daytona_client()
        sb = client.get(sandbox_id)
        if sb is None:
            raise ValueError(f"Sandbox '{sandbox_id}' not found")

        if _sandbox_state(sb) == "started":
            return _serialize_sandbox(sb)

        sb.start(timeout=_SANDBOX_TIMEOUT_SECONDS)
        _refresh_sandbox_data(sb)
        _ensure_git(sb)
        _refresh_sandbox_data(sb)

        return _serialize_sandbox(sb)

    def stop_sandbox(self, sandbox_id: str) -> dict[str, Any]:
        """Stop a running sandbox."""
        client = _get_daytona_client()
        sb = client.get(sandbox_id)
        if sb is None:
            raise ValueError(f"Sandbox '{sandbox_id}' not found")
        sb.stop(timeout=60)
        _refresh_sandbox_data(sb)
        return _serialize_sandbox(sb)

    def delete_sandbox(self, sandbox_id: str) -> None:
        """Delete a sandbox."""
        client = _get_daytona_client()
        sb = client.get(sandbox_id)
        if sb is None:
            raise ValueError(f"Sandbox '{sandbox_id}' not found")
        sb.delete(timeout=_SANDBOX_TIMEOUT_SECONDS)
        logger.info("Sandbox deleted: %s", sandbox_id)

    # -- Snapshots ------------------------------------------------------------

    def list_snapshots(self) -> list[dict[str, Any]]:
        """List available Daytona snapshots."""
        client = _get_daytona_client()
        # Try client.snapshot.list (newer SDK) then client.list_snapshots (older)
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
            # Fallback for older SDK
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
