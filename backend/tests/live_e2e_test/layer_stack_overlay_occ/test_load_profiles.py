"""E8 — named load-profile pass bars.

Backs §4.4. Drives ``sandbox.api.tool``. Pass bar: see
``../load_testing_standard.md`` per-profile budgets; net wall-time ≤
1.2× old-design median, ≤ 1.5× p99.
"""

from __future__ import annotations

import pytest

from .._harness.load_profiles import BURST, SMOKE, SOAK, SUSTAINED
from .._harness.sandbox_fixture import SandboxHandle


_PENDING_PREFIX = "pending: needs integrated_sandbox + load runner driving load_profiles."


def test_smoke_profile_passes(integrated_sandbox: SandboxHandle) -> None:
    pytest.skip(f"{_PENDING_PREFIX}{SMOKE.name}")


def test_sustained_profile_meets_p99_budget(
    integrated_sandbox: SandboxHandle,
) -> None:
    pytest.skip(f"{_PENDING_PREFIX}{SUSTAINED.name}")


def test_burst_profile_recovers_within_squash_window(
    integrated_sandbox: SandboxHandle,
) -> None:
    pytest.skip(f"{_PENDING_PREFIX}{BURST.name}")


def test_soak_profile_no_regression_over_15_min(
    integrated_sandbox: SandboxHandle,
) -> None:
    pytest.skip(f"{_PENDING_PREFIX}{SOAK.name}")
