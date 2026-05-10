#!/usr/bin/env python3
"""CLI summary renderer for ``complex_project_build.perf.v1`` artifacts.

Reads a ``perf.json`` saved by the complex_project_build scenario and prints a
one-screen summary table covering tool-use mix, layer-stack timings, overlay
capture cost, and OCC commit + conflict counts.

Usage::

    backend/scripts/analyze_complex_build_perf.py /path/to/perf.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


_ACCEPTED_SCHEMAS = ("complex_project_build.perf.v1",)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("perf_json", type=Path, help="Path to a saved perf.json artifact")
    args = parser.parse_args(argv)

    payload = json.loads(args.perf_json.read_text(encoding="utf-8"))
    schema = str(payload.get("schema") or "")
    if schema not in _ACCEPTED_SCHEMAS:
        print(f"Unsupported schema: {schema!r}", file=sys.stderr)
        return 2

    print(f"# {payload.get('scenario')!r} — run {payload.get('run_id') or '<unknown>'}")
    print(f"  wall_seconds_total = {payload.get('wall_seconds_total', 0.0):.2f}s")

    tool_use = payload.get("tool_use") or {}
    print()
    print("## Tool use")
    print(f"  total_calls          = {tool_use.get('total_calls', 0)}")
    print(f"  errors_total         = {tool_use.get('errors_total', 0)}")
    print(f"  expected_errors      = {tool_use.get('expected_errors_total', 0)}")
    print(f"  edit_to_write_ratio  = {tool_use.get('edit_to_write_ratio', 0.0):.2f}")
    print()
    print("## Per-tool")
    by_tool = tool_use.get("by_tool") or {}
    print(f"  {'tool':24s} {'count':>6s} {'errors':>6s} {'p50':>8s} {'p95':>8s} {'max':>8s}")
    for name, stats in sorted(by_tool.items()):
        print(
            f"  {name:24s} {int(stats.get('count', 0)):>6d} "
            f"{int(stats.get('errors', 0)):>6d} "
            f"{float(stats.get('wall_seconds_p50', 0.0)):>8.3f} "
            f"{float(stats.get('wall_seconds_p95', 0.0)):>8.3f} "
            f"{float(stats.get('wall_seconds_max', 0.0)):>8.3f}"
        )

    layer = payload.get("layer_stack") or {}
    print()
    print("## Layer stack")
    print(f"  squash_count        = {layer.get('squash_count', 0)}")
    print(f"  squash_total_s      = {float(layer.get('squash_total_s', 0.0)):.3f}")
    print(f"  squash_p95_s        = {float(layer.get('squash_p95_s', 0.0)):.3f}")
    print(f"  max_depth_before    = {float(layer.get('max_depth_before', 0.0)):.1f}")
    print(f"  materialize_count   = {layer.get('materialize_count', 0)}")

    overlay = payload.get("overlay") or {}
    print()
    print("## Overlay capture")
    print(f"  capture_count       = {overlay.get('capture_upperdir_count', 0)}")
    print(f"  capture_total_s     = {float(overlay.get('capture_upperdir_s_total', 0.0)):.3f}")
    print(f"  capture_p95_s       = {float(overlay.get('capture_upperdir_p95_s', 0.0)):.3f}")
    print(f"  shell_calls         = {overlay.get('shell_calls', 0)}")

    occ = payload.get("occ") or {}
    print()
    print("## OCC")
    print(f"  commit_count        = {occ.get('commit_count', 0)}")
    print(f"  commit_total_s      = {float(occ.get('commit_total_s', 0.0)):.3f}")
    print(f"  commit_resume_p95_s = {float(occ.get('commit_resume_wait_p95_s', 0.0)):.3f}")
    print(f"  conflict_count      = {occ.get('conflict_count', 0)}")
    print(f"  conflict_expected   = {occ.get('conflict_expected_count', 0)}")
    print(f"  conflict_unexpected = {occ.get('conflict_unexpected_count', 0)}")

    phases = payload.get("phases") or []
    if phases:
        print()
        print("## Phases")
        for phase in phases:
            name = phase.get("name") or "<unnamed>"
            duration = float(phase.get("duration_s", 0.0))
            calls = phase.get("tool_calls_at_end", 0)
            print(f"  {name:24s} duration={duration:7.2f}s tool_calls_at_end={calls}")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
