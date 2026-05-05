"""Shell-call snapshot isolation under concurrent api edits.

Backs §4.4. Drives ``sandbox.api.tool``. Pass bar: drift incidents = 0
across 100 paired runs.
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle


_PENDING = "pending: needs integrated_sandbox fixture (overlay+occ register on live sandbox)"


def test_in_flight_shell_does_not_see_concurrent_api_edit(
    integrated_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_shell_started_before_edit_sees_pre_edit_view(
    integrated_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)


def test_two_shells_overlapping_paths_first_commits_wins(
    integrated_sandbox: SandboxHandle,
) -> None:
    pytest.skip(_PENDING)
