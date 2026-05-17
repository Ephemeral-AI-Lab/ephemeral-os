"""SWE-EVO benchmark adapter (dataset, sandbox, evaluation)."""

from __future__ import annotations

import os

_DEFAULT_NO_PROXY_HOSTS: tuple[str, ...] = (
    "open.bigmodel.cn",
    "localhost",
    "127.0.0.1",
    "::1",
)


def _merge_no_proxy(existing: str, hosts: tuple[str, ...] = _DEFAULT_NO_PROXY_HOSTS) -> str:
    parts = [part.strip() for part in existing.split(",") if part.strip()]
    if "*" in parts:
        return "*"
    seen = {part.lower() for part in parts}
    for host in hosts:
        if host.lower() not in seen:
            parts.append(host)
            seen.add(host.lower())
    return ",".join(parts)


def ensure_default_no_proxy() -> None:
    """Bypass local HTTPS proxies for benchmark model calls that break TLS."""
    existing_values = [
        os.environ.get(key, "")
        for key in ("NO_PROXY", "no_proxy")
        if os.environ.get(key, "").strip()
    ]
    merged = _merge_no_proxy(",".join(existing_values))
    os.environ["NO_PROXY"] = merged
    os.environ["no_proxy"] = merged


ensure_default_no_proxy()


from benchmarks.sweevo.sandbox import (  # noqa: E402 — must follow no_proxy setup
    SnapshotNotRegisteredError,
    verify_sweevo_snapshot_exists,
)

__all__ = ["SnapshotNotRegisteredError", "verify_sweevo_snapshot_exists"]
