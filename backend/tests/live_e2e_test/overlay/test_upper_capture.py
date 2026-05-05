"""Overlay upperdir capture round-trip.

Backs §4.2. Pass bar: capture matches ``OverlayCapture`` schema; ordering
preserved across 1k captures.
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle


_PENDING = "pending: needs overlay_sandbox + capture/diff-ndjson helper"


def test_upperdir_captures_writes_deletes_and_whiteouts(
    overlay_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_capture_serializes_to_diff_ndjson_in_order(
    overlay_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)
