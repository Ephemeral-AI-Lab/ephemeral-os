"""Arbiter — Optimistic Concurrency Control for file edits.

Provides per-file write coordination to prevent conflicts when multiple
agents edit the same file. Uses edit tokens with TTL for staleness detection.

Edit history is persisted via FileChangeStore (PostgreSQL). The Arbiter
no longer maintains an in-memory edit buffer.

Lock ordering (Group A):
    Arbiter locks < Cache locks < Counter locks
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from code_intelligence.constants import (
    ARBITER_LOCK_TIMEOUT,
    ARBITER_MAX_CONCURRENT_EDITS,
)

logger = logging.getLogger(__name__)

_EDIT_TOKEN_TTL = 300.0  # 5 minutes
_EDIT_INTENT_TTL = 300.0  # 5 minutes


@dataclass
class EditToken:
    """Token issued when a file is read-for-edit."""

    token_id: str
    file_path: str
    content_hash: str
    issued_at: float
    agent_id: str = ""
    ttl: float = _EDIT_TOKEN_TTL


@dataclass
class EditIntent:
    """Published edit intent for coordination and observability."""

    intent_id: str
    file_path: str
    issued_at: float
    heartbeat_at: float
    agent_id: str = ""
    coordination_plan_id: str = ""
    task_id: str = ""
    symbols: tuple[str, ...] = ()
    scope: str = "file"
    ttl: float = _EDIT_INTENT_TTL


@dataclass
class ArbiterMetrics:
    """Edit coordination metrics."""

    total_edits: int = 0
    conflicts_detected: int = 0
    tokens_issued: int = 0
    tokens_expired: int = 0
    active_locks: int = 0


class Arbiter:
    """Per-file edit arbitration with OCC.

    Thread-safe. Uses per-file locks to serialize edits to the same file
    while allowing concurrent edits to different files.

    Edit history is delegated to FileChangeStore (PG). The Arbiter only
    manages OCC primitives: tokens, intents, and file locks.

    Parameters
    ----------
    workspace_root:
        Root directory for path validation.
    on_edit:
        Optional callback ``(file_path, agent_id, generation)`` after successful edit.
    file_change_store:
        Durable store for edit history. When None, edits are not persisted.
    team_run_id:
        Team run ID for scoping edits in the store.
    max_concurrent:
        Maximum concurrent file edits.
    """

    def __init__(
        self,
        workspace_root: str = "",
        on_edit: Callable[[str, str, int], None] | None = None,
        file_change_store: Any | None = None,
        team_run_id: str = "",
        max_concurrent: int = ARBITER_MAX_CONCURRENT_EDITS,
    ) -> None:
        self._workspace_root = workspace_root
        self._on_edit = on_edit
        self._max_concurrent = max_concurrent
        self.file_change_store: Any = file_change_store
        self.team_run_id: str = team_run_id

        self._lock = threading.Lock()
        self._file_locks: dict[str, threading.Lock] = {}
        self._active_tokens: dict[str, EditToken] = {}  # token_id -> token
        self._active_intents: dict[str, EditIntent] = {}  # intent_id -> intent
        self._metrics = ArbiterMetrics()
        self._generation = 0

    # -- Token management -----------------------------------------------------

    def issue_token(
        self, file_path: str, content_hash: str, agent_id: str = "",
    ) -> EditToken:
        """Issue an edit token for a file."""
        with self._lock:
            self._prune_expired_tokens_locked()
        token = EditToken(
            token_id=uuid.uuid4().hex[:12],
            file_path=file_path,
            content_hash=content_hash,
            issued_at=time.time(),
            agent_id=agent_id,
        )
        with self._lock:
            self._active_tokens[token.token_id] = token
            self._metrics.tokens_issued += 1
        return token

    def validate_token(
        self,
        token_id: str,
        *,
        file_path: str,
        content_hash: str = "",
    ) -> tuple[bool, str]:
        """Validate that *token_id* still reserves *file_path* at *content_hash*."""
        with self._lock:
            self._prune_expired_tokens_locked()
            token = self._active_tokens.get(token_id)
            if token is None:
                return False, "missing or expired write reservation"
            if token.file_path != file_path:
                return False, "write reservation does not belong to this file"
            if content_hash and token.content_hash != content_hash:
                return False, "write reservation content hash no longer matches"
            return True, ""

    def release_token(self, token_id: str) -> None:
        """Drop an active edit token."""
        with self._lock:
            self._active_tokens.pop(token_id, None)

    def publish_edit_intent(
        self,
        file_path: str,
        agent_id: str = "",
        *,
        coordination_plan_id: str | None = None,
        task_id: str | None = None,
        symbols: list[str] | tuple[str, ...] | None = None,
        scope: str = "file",
    ) -> str:
        """Publish an edit intent for coordination-aware consumers."""
        now = time.time()
        intent = EditIntent(
            intent_id=uuid.uuid4().hex[:12],
            file_path=file_path,
            issued_at=now,
            heartbeat_at=now,
            agent_id=agent_id,
            coordination_plan_id=str(coordination_plan_id or ""),
            task_id=str(task_id or ""),
            symbols=tuple(
                str(symbol).strip()
                for symbol in (symbols or [])
                if isinstance(symbol, str) and str(symbol).strip()
            ),
            scope=str(scope or "file"),
        )
        with self._lock:
            self._prune_expired_tokens_locked()
            self._prune_expired_intents_locked(now=now)
            self._active_intents[intent.intent_id] = intent
        return intent.intent_id

    def heartbeat_edit_intent(self, intent_id: str) -> bool:
        """Refresh an existing edit intent heartbeat."""
        with self._lock:
            self._prune_expired_intents_locked()
            intent = self._active_intents.get(intent_id)
            if intent is None:
                return False
            intent.heartbeat_at = time.time()
            return True

    def release_edit_intent(self, intent_id: str) -> None:
        """Drop an active edit intent."""
        with self._lock:
            self._active_intents.pop(intent_id, None)

    # -- Edit coordination ----------------------------------------------------

    def acquire_file_lock(
        self, file_path: str, timeout: float = ARBITER_LOCK_TIMEOUT,
    ) -> bool:
        """Acquire the per-file edit lock. Returns True if acquired."""
        lock = self._get_file_lock(file_path)
        return lock.acquire(timeout=timeout)

    def release_file_lock(self, file_path: str) -> None:
        """Release the per-file edit lock."""
        lock = self._get_file_lock(file_path)
        try:
            lock.release()
        except RuntimeError:
            pass  # Already released

    def record_edit(
        self,
        file_path: str,
        agent_id: str = "",
        edit_type: str = "edit",
        old_hash: str = "",
        new_hash: str = "",
        description: str = "",
    ) -> int:
        """Record a successful edit. Returns the new generation.

        Writes directly to FileChangeStore (PG) when available.
        """
        with self._lock:
            self._prune_expired_tokens_locked()
            self._generation += 1
            gen = self._generation
            self._metrics.total_edits += 1

        store = self.file_change_store
        if store is not None and getattr(store, "initialized", False):
            try:
                store.record(
                    team_run_id=self.team_run_id or "",
                    file_path=file_path,
                    agent_id=agent_id,
                    edit_type=edit_type,
                    old_hash=old_hash,
                    new_hash=new_hash,
                    description=description,
                )
            except Exception:
                logger.debug("FileChangeStore.record failed for %s", file_path)

        if self._on_edit:
            try:
                self._on_edit(file_path, agent_id, gen)
            except Exception:
                logger.debug("on_edit callback failed for %s", file_path)

        return gen

    # -- Queries --------------------------------------------------------------

    @property
    def metrics(self) -> ArbiterMetrics:
        with self._lock:
            return ArbiterMetrics(
                total_edits=self._metrics.total_edits,
                conflicts_detected=self._metrics.conflicts_detected,
                tokens_issued=self._metrics.tokens_issued,
                tokens_expired=self._metrics.tokens_expired,
                active_locks=len(self._file_locks),
            )

    @property
    def active_edit_count(self) -> int:
        with self._lock:
            self._prune_expired_tokens_locked()
            return len(self._active_tokens)

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def active_reservations(
        self,
        scope_paths: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return active edit reservations, optionally filtered to *scope_paths*."""
        scope_paths = [p.strip() for p in (scope_paths or []) if isinstance(p, str) and p.strip()]
        now = time.time()
        with self._lock:
            self._prune_expired_tokens_locked(now=now)
            out: list[dict[str, Any]] = []
            for token in self._active_tokens.values():
                if scope_paths and not any(_paths_overlap(token.file_path, scope) for scope in scope_paths):
                    continue
                out.append(
                    {
                        "token_id": token.token_id,
                        "file_path": token.file_path,
                        "agent_id": token.agent_id,
                        "issued_at": token.issued_at,
                        "expires_at": token.issued_at + token.ttl,
                    }
                )
            out.sort(key=lambda item: (str(item["file_path"]), str(item["token_id"])))
            return out

    def active_edit_intents(
        self,
        scope_paths: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return active edit intents, optionally filtered to *scope_paths*."""
        scope_paths = [p.strip() for p in (scope_paths or []) if isinstance(p, str) and p.strip()]
        now = time.time()
        with self._lock:
            self._prune_expired_intents_locked(now=now)
            out: list[dict[str, Any]] = []
            for intent in self._active_intents.values():
                if scope_paths and not any(_paths_overlap(intent.file_path, scope) for scope in scope_paths):
                    continue
                out.append(
                    {
                        "intent_id": intent.intent_id,
                        "file_path": intent.file_path,
                        "agent_id": intent.agent_id,
                        "coordination_plan_id": intent.coordination_plan_id,
                        "task_id": intent.task_id,
                        "scope": intent.scope,
                        "symbols": list(intent.symbols),
                        "issued_at": intent.issued_at,
                        "heartbeat_at": intent.heartbeat_at,
                        "expires_at": intent.heartbeat_at + intent.ttl,
                    }
                )
            out.sort(key=lambda item: (str(item["file_path"]), str(item["intent_id"])))
            return out

    def status(self) -> dict[str, Any]:
        """Return arbiter status summary."""
        m = self.metrics
        return {
            "total_edits": m.total_edits,
            "conflicts_detected": m.conflicts_detected,
            "tokens_issued": m.tokens_issued,
            "tokens_expired": m.tokens_expired,
            "active_tokens": self.active_edit_count,
            "active_intents": len(self._active_intents),
            "active_locks": m.active_locks,
        }

    def cleanup_locks(self) -> int:
        """Remove file locks that are not held. Returns count cleaned."""
        with self._lock:
            self._prune_expired_tokens_locked()
            to_remove = [
                fp for fp, lock in self._file_locks.items()
                if not lock.locked()
            ]
            for fp in to_remove:
                del self._file_locks[fp]
            return len(to_remove)

    # -- Internal -------------------------------------------------------------

    def _get_file_lock(self, file_path: str) -> threading.Lock:
        with self._lock:
            if file_path not in self._file_locks:
                self._file_locks[file_path] = threading.Lock()
            return self._file_locks[file_path]

    def _prune_expired_tokens_locked(self, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        expired = [
            token_id
            for token_id, token in self._active_tokens.items()
            if token.issued_at + token.ttl <= now
        ]
        for token_id in expired:
            self._active_tokens.pop(token_id, None)
            self._metrics.tokens_expired += 1

    def _prune_expired_intents_locked(self, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        expired = [
            intent_id
            for intent_id, intent in self._active_intents.items()
            if intent.heartbeat_at + intent.ttl <= now
        ]
        for intent_id in expired:
            self._active_intents.pop(intent_id, None)


def _paths_overlap(path_a: str, path_b: str) -> bool:
    left = (path_a or "").strip().rstrip("/")
    right = (path_b or "").strip().rstrip("/")
    if not left or not right:
        return False
    if left == right:
        return True
    if left.startswith(right + "/") or right.startswith(left + "/"):
        return True
    return (
        left.endswith("/" + right)
        or right.endswith("/" + left)
        or ("/" + right + "/") in (left + "/")
        or ("/" + left + "/") in (right + "/")
    )
