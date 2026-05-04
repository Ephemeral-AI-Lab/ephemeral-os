from __future__ import annotations

import pytest

from .conftest import (
    assert_success,
    full_experiment_enabled,
    make_workdir,
    parse_json_line,
    print_live_metric,
    python_json_command,
    run_live_command,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e07_tmpfs_sizing_measurement_pipeline(live_snapshot_sandbox):
    workdir = await make_workdir(live_snapshot_sandbox, "e07")
    megabytes = 64 if full_experiment_enabled() else 8
    command = python_json_command(
        f"""
        import json
        import pathlib
        import subprocess

        root = pathlib.Path({workdir!r})
        layers = root / "layers"
        layers.mkdir(parents=True, exist_ok=True)
        block = b"x" * 1024 * 1024
        for index in range({megabytes}):
            (layers / f"blob_{{index:03d}}.bin").write_bytes(block)
        du = subprocess.check_output(["du", "-sk", str(root)], text=True).split()[0]
        print(json.dumps({{"requested_mb": {megabytes}, "du_kb": int(du)}}))
        """
    )
    result = await run_live_command(
        live_snapshot_sandbox,
        command,
        timeout=180,
        label="e07.tmpfs_sizing",
    )
    assert_success(result)
    payload = parse_json_line(result.stdout)
    print_live_metric("e07.summary", **payload)
    assert payload["du_kb"] >= megabytes * 900
