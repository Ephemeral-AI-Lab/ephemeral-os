"""E13 — gitignore-driven CAS/LWW routing.

Backs §4.3. Pass bar: zero classification leaks; gitignored paths never
CAS-rejected; tracked paths never LWW-accepted.
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle


_PENDING = "pending: needs occ_sandbox + gitignore-classification helper"


def test_tracked_path_uses_cas(occ_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)


def test_gitignored_path_uses_lww(occ_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)


def test_mixed_changeset_partial_commit(occ_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)


def test_gitignore_evaluated_at_snapshot_time(occ_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)
