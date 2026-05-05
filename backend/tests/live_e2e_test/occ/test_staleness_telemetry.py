"""E11 — staleness telemetry surfaced unconditionally.

Backs §4.3. Pass bar: long-shell write to derived path **always**
accepts; ``manifest_lag`` and ``shell_age_seconds`` populated.
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle


_PENDING = "pending: needs occ_sandbox + assertions.assert_telemetry_present"


def test_long_shell_clean_cas_accepts_with_lag_telemetry(
    occ_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_manifest_lag_field_increments_with_intervening_commits(
    occ_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_no_age_or_lag_based_rejection(occ_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)
