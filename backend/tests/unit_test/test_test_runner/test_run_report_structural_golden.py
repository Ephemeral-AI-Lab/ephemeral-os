"""Invariance guard for the ``RunReport`` shape.

The ``run_scenario`` shim rebuilds ``RunReport`` from a ``PipelineReport``,
accumulated ``MOCK_*`` events, and graph state. This test asserts the field set
stays identical to the captured golden so external consumers that access
``report.launches`` / ``.tool_calls`` / etc. continue to work unchanged.

Captured at commit 1bdb21de — see ``backend/tests/golden/run_report_structural.json``.

To regenerate after an intentional shape change, edit the golden file
directly and re-run this test. Keep the change in a dedicated commit so the
shape change is reviewable.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from task_center_runner.core.runner import RunReport


_GOLDEN_PATH = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "golden"
    / "run_report_structural.json"
)


def test_run_report_field_set_matches_golden() -> None:
    golden = json.loads(_GOLDEN_PATH.read_text())
    actual = sorted(f.name for f in fields(RunReport))
    expected = sorted(golden["field_names"])
    assert actual == expected, (
        "RunReport field set drifted from the structural golden.\n"
        f"Added:   {sorted(set(actual) - set(expected))}\n"
        f"Removed: {sorted(set(expected) - set(actual))}\n"
        f"Golden:  {_GOLDEN_PATH}"
    )
    assert len(actual) == golden["field_count"]
