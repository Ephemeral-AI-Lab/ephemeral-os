"""Typed view over a Daytona SDK sandbox object."""

from __future__ import annotations

import logging
from typing import Any

from sandbox.providers.daytona.client.sync import (
    _APP_MANAGED_BY,
    _IMAGE_LABEL,
    _SNAPSHOT_LABEL,
    _normalize_optional_text,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Git bootstrap script — installs git if missing
# ---------------------------------------------------------------------------

_GIT_BOOTSTRAP = r"""
set -e
if command -v git >/dev/null 2>&1; then exit 0; fi
echo "[sandbox] Installing git..."
as_root() {
    if [ "$(id -u)" = "0" ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo -n "$@"
    else
        return 1
    fi
}
if command -v apt-get >/dev/null 2>&1; then
    as_root mkdir -p /var/lib/apt/lists/partial
    as_root apt-get update -qq && as_root apt-get install -y -qq git
elif command -v apk >/dev/null 2>&1; then
    as_root apk add --no-cache git
elif command -v microdnf >/dev/null 2>&1; then
    as_root microdnf install -y git
elif command -v dnf >/dev/null 2>&1; then
    as_root dnf install -y git
elif command -v yum >/dev/null 2>&1; then
    as_root yum install -y git
else
    echo "[sandbox] No package manager found — git not installed" >&2
    exit 1
fi
echo "[sandbox] git installed"
"""


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
            from sandbox.api.raw_exec import raw_exec
            from sandbox.providers.daytona.client.async_bridge import run_sync

            logger.info("ensure_git(%s): probe starting", self.id)
            resp = run_sync(
                raw_exec(
                    self.id,
                    "command -v git >/dev/null 2>&1 && echo ok || echo missing",
                    timeout=10,
                )
            )
            if "ok" in (resp.stdout or ""):
                logger.info("ensure_git(%s): git already available", self.id)
                return
            logger.info("ensure_git(%s): installing git", self.id)
            install = run_sync(
                raw_exec(self.id, _GIT_BOOTSTRAP, timeout=120)
            )
            if getattr(install, "exit_code", 1) not in (0, None):
                raise RuntimeError(
                    getattr(install, "stderr", "")
                    or getattr(install, "stdout", "")
                    or "git install failed"
                )
            logger.info("ensure_git(%s): install completed", self.id)
        except Exception as exc:
            logger.warning("Git bootstrap failed for sandbox %s: %s", self.id, exc)

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


__all__ = ["SandboxProxy"]
