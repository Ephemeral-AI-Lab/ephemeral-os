from __future__ import annotations

import pytest

from .conftest import (
    assert_success,
    make_workdir,
    parse_json_line,
    print_live_metric,
    python_json_command,
    run_live_command,
    xfail_production_binding_missing,
)

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]


async def test_e09_write_then_rename_recovery_oracle(live_snapshot_sandbox):
    workdir = await make_workdir(live_snapshot_sandbox, "e09")
    command = python_json_command(
        f"""
        import json
        import pathlib

        root = pathlib.Path({workdir!r})
        stack = root / "stack"
        stack.mkdir()
        committed = stack / "layer_0001"
        partial = stack / "layer_0002.partial"
        committed.mkdir()
        partial.mkdir()
        (committed / "file.txt").write_text("committed\\n", encoding="utf-8")
        (partial / "file.txt").write_text("partial\\n", encoding="utf-8")
        manifest = stack / "manifest.json"
        manifest.write_text(json.dumps({{"layers": [str(committed)]}}), encoding="utf-8")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        dangling = [path for path in payload["layers"] if not pathlib.Path(path).exists()]
        leaked_partials = sorted(path.name for path in stack.glob("*.partial"))
        print(json.dumps({{"dangling": dangling, "leaked_partials": leaked_partials}}))
        """
    )
    result = await run_live_command(
        live_snapshot_sandbox,
        command,
        timeout=60,
        label="e09.recovery_oracle",
    )
    assert_success(result)
    payload = parse_json_line(result.stdout)
    print_live_metric("e09.summary", **payload)
    assert payload["dangling"] == []
    assert payload["leaked_partials"] == ["layer_0002.partial"]


async def test_e09_production_crash_recovery_contract_required():
    xfail_production_binding_missing("E9 squash/mid-commit crash recovery")
