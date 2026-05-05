"""E13 — codegen race (tracked vs gitignored generated files).

Backs §4.4. Drives ``sandbox.api.tool``. Pass bar: tracked race → 1
accept + 1 reject deterministically; gitignored race → both accept
under LWW.
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle


_PENDING = "pending: needs integrated_sandbox + concurrency.gather_with_barrier"


def test_two_agents_writing_same_tracked_generated_file_second_rejects_with_path_conflict(
    integrated_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_dist_artifact_concurrent_writes_both_accept_lww(
    integrated_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_mixed_tracked_and_gitignored_partial_commit(
    integrated_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)
