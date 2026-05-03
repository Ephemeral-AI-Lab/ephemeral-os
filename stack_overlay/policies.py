"""Experimental policy helpers for shell modes, direct merges, and leases."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from time import monotonic

from stack_overlay.models import CommitResult, DeleteChange, Manifest, WriteChange
from stack_overlay.occ import OccCommitter


class ShellMode(str, Enum):
    READ_ONLY = "read_only"
    GATED = "gated"
    STRICT_STALE = "strict_stale"
    EXCLUSIVE = "exclusive"


@dataclass(frozen=True)
class StalenessPolicy:
    max_lag: int = 5
    max_age_s: float = 60.0
    warn_lag: int = 2
    warn_age_s: float = 30.0


@dataclass(frozen=True)
class ShellCommitOutcome:
    mode: ShellMode
    status: str
    manifest_lag: int
    shell_age_s: float
    warnings: tuple[str, ...]
    commit: CommitResult | None = None


class ShellCommitGate:
    """Apply shell mode policy around the OCC committer."""

    def __init__(
        self,
        occ: OccCommitter,
        *,
        policy: StalenessPolicy | None = None,
    ) -> None:
        self._occ = occ
        self._policy = policy or StalenessPolicy()
        self._exclusive_lock = threading.Lock()

    def apply(
        self,
        *,
        mode: ShellMode,
        changes: list[WriteChange | DeleteChange],
        snapshot: Manifest,
        active: Manifest,
        shell_started_at: float,
        now: float | None = None,
    ) -> ShellCommitOutcome:
        observed_now = monotonic() if now is None else now
        lag = active.version - snapshot.version
        age = max(0.0, observed_now - shell_started_at)
        warnings = self._warnings(lag, age)

        if mode is ShellMode.READ_ONLY:
            return ShellCommitOutcome(mode, "discarded_read_only", lag, age, warnings)
        if mode is ShellMode.STRICT_STALE and (
            lag > self._policy.max_lag or age > self._policy.max_age_s
        ):
            return ShellCommitOutcome(mode, "rejected_stale_snapshot", lag, age, warnings)
        if mode is ShellMode.EXCLUSIVE:
            with self._exclusive_lock:
                commit = self._occ.apply(changes)
            return ShellCommitOutcome(mode, "committed", lag, age, warnings, commit)

        commit = self._occ.apply(changes)
        return ShellCommitOutcome(mode, "committed", lag, age, warnings, commit)

    def _warnings(self, lag: int, age: float) -> tuple[str, ...]:
        warnings = []
        if lag > self._policy.warn_lag:
            warnings.append("manifest_lag")
        if age > self._policy.warn_age_s:
            warnings.append("shell_age")
        return tuple(warnings)


@dataclass(frozen=True)
class DirectMergeDecision:
    path: str
    change_type: str
    allowed: bool
    reason: str


class DirectMergePolicy:
    """Bound direct-merge exceptions to explicit cache/output prefixes."""

    def __init__(
        self,
        allowed_prefixes: tuple[str, ...] = (
            ".cache/",
            ".pytest_cache/",
            "node_modules/.cache/",
            "tmp/",
            "build/",
            "dist/",
        ),
    ) -> None:
        self._allowed_prefixes = allowed_prefixes

    def decide(self, path: str, change_type: str) -> DirectMergeDecision:
        normalized = path.strip("/")
        if any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in self._allowed_prefixes):
            return DirectMergeDecision(
                normalized,
                change_type,
                True,
                "allowed_direct_merge_prefix",
            )
        return DirectMergeDecision(
            normalized,
            change_type,
            False,
            "direct_merge_requires_allowed_prefix",
        )


@dataclass(frozen=True)
class LeaseSnapshot:
    lease_id: str
    age_s: float
    pinned_bytes: int
    manifest_version: int
    read_only: bool = False


@dataclass(frozen=True)
class LeaseBudgetDecision:
    action: str
    lease_id: str | None
    reason: str


class LeaseBudget:
    def __init__(
        self,
        *,
        max_age_s: float = 600.0,
        max_pinned_bytes_per_session: int = 512 * 1024 * 1024,
        max_old_manifests: int = 16,
        max_total_pinned_bytes_global: int = 4 * 1024 * 1024 * 1024,
    ) -> None:
        self.max_age_s = max_age_s
        self.max_pinned_bytes_per_session = max_pinned_bytes_per_session
        self.max_old_manifests = max_old_manifests
        self.max_total_pinned_bytes_global = max_total_pinned_bytes_global

    def evaluate(
        self,
        leases: list[LeaseSnapshot],
        *,
        active_manifest_version: int,
        global_pinned_bytes: int,
    ) -> list[LeaseBudgetDecision]:
        decisions: list[LeaseBudgetDecision] = []
        old_leases = [
            lease
            for lease in leases
            if lease.manifest_version < active_manifest_version and not lease.read_only
        ]

        expired = [lease for lease in leases if lease.age_s > self.max_age_s]
        for lease in expired:
            decisions.append(
                LeaseBudgetDecision("kill", lease.lease_id, "max_lease_age")
            )

        pinned_session = sum(lease.pinned_bytes for lease in old_leases)
        if pinned_session > self.max_pinned_bytes_per_session:
            decisions.append(
                LeaseBudgetDecision("backpressure", None, "session_pinned_bytes")
            )

        if len(old_leases) > self.max_old_manifests:
            oldest = max(old_leases, key=lambda lease: lease.age_s)
            decisions.append(
                LeaseBudgetDecision("kill", oldest.lease_id, "max_old_manifests")
            )

        if global_pinned_bytes > self.max_total_pinned_bytes_global:
            oldest = max(leases, key=lambda lease: lease.age_s, default=None)
            decisions.append(
                LeaseBudgetDecision(
                    "evict_session",
                    oldest.lease_id if oldest else None,
                    "global_pinned_bytes",
                )
            )

        if not decisions:
            decisions.append(LeaseBudgetDecision("allow", None, "within_budget"))
        return decisions


def classify_shell_mode(command: str) -> ShellMode:
    stripped = command.strip()
    read_only_prefixes = (
        "pytest",
        "uv run pytest",
        "ruff",
        "uv run ruff",
        "mypy",
        "npm test",
        "npm run test",
        "npm run lint",
        "cargo test",
        "cargo check",
        "go test",
    )
    exclusive_prefixes = (
        "npm run build",
        "cargo build",
        "make",
        "uv build",
    )
    strict_indicators = (
        "codegen",
        "generate",
        "openapi-generator",
        "protoc",
    )
    if stripped.startswith(read_only_prefixes):
        return ShellMode.READ_ONLY
    if stripped.startswith(exclusive_prefixes):
        return ShellMode.EXCLUSIVE
    if any(token in stripped for token in strict_indicators):
        return ShellMode.STRICT_STALE
    return ShellMode.GATED
