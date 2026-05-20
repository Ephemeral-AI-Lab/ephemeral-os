"""Adversarial self-test for the O(1) telemetry harness."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from ..._harness.lease_resource_probe import (
    LEGACY_MATERIALIZE,
    NEW_MOUNT_API,
    as_requested_path,
    assert_new_api_o1_bounds,
    build_layer_stack,
    has_cap_sys_admin,
    run_shell_batch,
)


pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not has_cap_sys_admin(),
    reason="O(1) overlay verification requires Linux with private mount namespaces",
)

_N = 50
_LAYER_COUNT = 10
_NO_WRITE = "test -f known_file.bin"
_UPPER_WRITE = (
    "python - <<'PY'\n"
    "from pathlib import Path\n"
    "Path('injected_upper_regression.bin').write_bytes(b'x' * (1024 * 1024))\n"
    "PY"
)


def test_adversarial_harness_names_upper_and_materialize_regressions(
    tmp_path: Path,
) -> None:
    """The self-test injects both planned regressions and requires both names."""
    asyncio.run(_run_adversarial_self_test(tmp_path))


async def _run_adversarial_self_test(tmp_path: Path) -> None:
    stack = build_layer_stack(
        tmp_path,
        manifest_depth=_LAYER_COUNT,
        base_payload_bytes=8 * 1024 * 1024,
    )
    normal_count = _N - 2
    normal = await run_shell_batch(
        stack=stack,
        workspace_root=tmp_path / "workspace-root",
        scratch_root=tmp_path / "scratch-normal",
        requested_path=NEW_MOUNT_API,
        commands=[_NO_WRITE] * normal_count,
        request_prefix="normal",
    )
    upper = await run_shell_batch(
        stack=stack,
        workspace_root=tmp_path / "workspace-root",
        scratch_root=tmp_path / "scratch-upper",
        requested_path=NEW_MOUNT_API,
        commands=[_UPPER_WRITE],
        request_prefix="upper_write_BAD",
    )
    materialized = await run_shell_batch(
        stack=stack,
        workspace_root=tmp_path / "workspace-root",
        scratch_root=tmp_path / "scratch-materialized",
        requested_path=LEGACY_MATERIALIZE,
        commands=[_NO_WRITE],
        request_prefix="forced_materialize_BAD",
    )

    rows = [
        *normal[:25],
        upper[0],
        *normal[25:],
        as_requested_path(
            materialized[0],
            NEW_MOUNT_API,
            request_id="forced_materialize_BAD-0000",
        ),
    ]

    with pytest.raises(AssertionError) as exc_info:
        assert_new_api_o1_bounds(rows)

    message = str(exc_info.value)
    assert "upper_write_BAD" in message
    assert "upper write" in message
    assert "forced_materialize_BAD" in message
    assert "forced materialize" in message
