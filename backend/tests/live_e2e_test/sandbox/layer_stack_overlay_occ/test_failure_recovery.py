"""E9 — failure recovery: kill mid-publish / mid-squash / lease cleanup.

Backs §4.4. Drives ``sandbox.api.tool``. Pass bar: after fault, fsck
reports 0 dangling refs; no leaked layers; agent sees
``mutations='killed_lease_overrun'``.
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle


_PENDING = "pending: needs integrated_sandbox + runtime kill helper"


def test_kill_runtime_mid_layer_publish_no_dangling_manifest(
    integrated_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_kill_runtime_mid_squash_no_orphan_checkpoint(
    integrated_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_lease_cleaned_when_owning_shell_killed(
    integrated_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)
