"""E10 — per-path CAS gate.

Backs §4.3. Pass bar: zero false-accept and zero false-reject across 10k
iterations of the gated matrix.
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle


_PENDING = "pending: needs occ_sandbox fixture wiring (register_occ_service) and synthetic layer-stack base-view helper"


def test_write_write_conflict_rejects_loser(occ_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)


def test_disjoint_paths_both_accept(occ_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)


def test_anchor_miss_rejects_edit(occ_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)


def test_existence_change_rejects_create(occ_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)


def test_delete_already_deleted_is_noop(occ_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)
