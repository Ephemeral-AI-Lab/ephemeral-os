"""Exploration cache ownership for the merged memory toolkit."""

from __future__ import annotations

import hashlib
import os
from typing import Any


class ExplorationMemory:
    """Cross-run note cache. Content-addressed by file hashes."""

    _MAX_FILES_TO_HASH = 500

    def __init__(self) -> None:
        self._store: dict[str, list[dict[str, Any]]] = {}
        self._persistent_store: Any = None

    def attach_store(self, store: Any) -> None:
        """Attach a durable store for persistent cache entries."""
        self._persistent_store = store

    def attach_pg(self, pg_store: Any) -> None:
        """Backward-compatible alias for the old PG-specific name."""
        self.attach_store(pg_store)

    def check(self, scope_paths: list[str], workspace_root: str = "") -> list[dict[str, Any]] | None:
        """Return cached notes from L1 if files have not changed."""
        content_hash = self._hash_scope(scope_paths, workspace_root)
        key = self._cache_key(scope_paths, content_hash)
        return self._store.get(key)

    async def check_async(
        self,
        scope_paths: list[str],
        workspace_root: str = "",
    ) -> list[dict[str, Any]] | None:
        """Check L1 cache, then fall through to the durable store on miss."""
        content_hash = self._hash_scope(scope_paths, workspace_root)
        key = self._cache_key(scope_paths, content_hash)
        cached = self._store.get(key)
        if cached is not None:
            return cached
        if self._persistent_store is not None and getattr(self._persistent_store, "initialized", False):
            stored_notes = await self._persistent_store.get(key)
            if stored_notes is not None:
                self._store[key] = stored_notes
                return stored_notes
        return None

    def save(self, scope_paths: list[str], notes: list[dict[str, Any]], workspace_root: str = "") -> None:
        """Cache notes in L1."""
        content_hash = self._hash_scope(scope_paths, workspace_root)
        key = self._cache_key(scope_paths, content_hash)
        self._store[key] = notes

    async def save_async(
        self,
        scope_paths: list[str],
        notes: list[dict[str, Any]],
        workspace_root: str = "",
    ) -> None:
        """Cache notes in L1 and write through to the durable store."""
        content_hash = self._hash_scope(scope_paths, workspace_root)
        key = self._cache_key(scope_paths, content_hash)
        self._store[key] = notes
        if self._persistent_store is not None and getattr(self._persistent_store, "initialized", False):
            await self._persistent_store.put(
                cache_key=key,
                scope_paths=sorted(scope_paths),
                content_hash=content_hash,
                notes=notes,
            )

    def _cache_key(self, scope_paths: list[str], content_hash: str) -> str:
        scope_str = "|".join(sorted(scope_paths))
        return hashlib.sha256(f"{scope_str}:{content_hash}".encode()).hexdigest()[:24]

    def _hash_scope(self, scope_paths: list[str], workspace_root: str) -> str:
        """Hash files under scope_paths to invalidate stale cache entries."""
        digest = hashlib.sha256()
        file_count = 0
        for scope in sorted(scope_paths):
            full_path = os.path.join(workspace_root, scope) if workspace_root else scope
            if os.path.isfile(full_path):
                digest.update(self._hash_file(full_path).encode())
                file_count += 1
            elif os.path.isdir(full_path):
                for root, _dirs, files in sorted(os.walk(full_path)):
                    for fname in sorted(files):
                        if file_count >= self._MAX_FILES_TO_HASH:
                            digest.update(f"capped:{file_count}".encode())
                            return digest.hexdigest()[:16]
                        file_path = os.path.join(root, fname)
                        digest.update(self._hash_file(file_path).encode())
                        file_count += 1
            else:
                digest.update(f"missing:{scope}".encode())
        return digest.hexdigest()[:16]

    @staticmethod
    def _hash_file(path: str) -> str:
        try:
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(8192), b""):
                    digest.update(chunk)
            return digest.hexdigest()[:16]
        except (OSError, PermissionError):
            return ""


_exploration_memory = ExplorationMemory()


def get_exploration_memory() -> ExplorationMemory:
    """Return the process-wide exploration cache singleton."""
    return _exploration_memory
