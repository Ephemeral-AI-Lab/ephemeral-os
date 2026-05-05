"""Typed-change round-trip through OCC.

Backs §4.3. Pass bar: each typed change routes through the policy of §4d
in the migration plan.
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle


_PENDING = "pending: needs occ_sandbox + Change-builder helpers"


def test_writechange_round_trip(occ_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)


def test_editchange_anchor_resolution(occ_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)


def test_binarychange_existence_size_cas(occ_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)


def test_symlinkchange_existence_cas(occ_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)


def test_opaquedir_lww_documented(occ_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)
