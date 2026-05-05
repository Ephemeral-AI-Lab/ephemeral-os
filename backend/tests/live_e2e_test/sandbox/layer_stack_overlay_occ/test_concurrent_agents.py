"""E4 — concurrent agents under sustained load.

Backs §4.4. Drives ``sandbox.api.tool``. Pass bar: 0 correctness
violations across 10 runs; final manifest reproducible from per-call
captures.
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle


_PENDING = "pending: needs integrated_sandbox + load_profiles.SUSTAINED runner"


def test_8_shells_per_sec_plus_16_edits_per_sec_for_60s_no_torn_reads(
    integrated_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_every_accepted_write_visible_in_final_view(
    integrated_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_every_rejected_write_left_no_trace(
    integrated_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_overlapping_50pct_paths(integrated_sandbox: SandboxHandle) -> None:
    pytest.skip(_PENDING)
