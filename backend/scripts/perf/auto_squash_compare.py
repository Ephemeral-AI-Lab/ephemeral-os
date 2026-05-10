#!/usr/bin/env python3
"""Auto-squash performance aggregator.

Per `.omc/plans/occ-auto-squash-perf-verification-test-plan-20260511.md`:
walks message.jsonl files under a scenario run dir, extracts
``metadata.timings`` from every tool-result message, and computes per-tool,
per-key aggregates (count, avg, p50, p95, max, total).

Two modes:

    baseline   single run-root, emits per-scenario metrics.json + summary.md
    compare    baseline + experimental, emits comparison.md + verdict.json

The compare mode is forward-looking: until the optimization PR introduces an
``EOS_OCC_SQUASH_MODE`` flag, only baseline runs.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Plan §Metrics — required keys per family.
METRIC_FAMILIES: dict[str, list[str]] = {
    "edit": [
        "api.edit.total_s",
        "api.edit.snapshot_read_s",
        "api.edit.derive_bytes_s",
        "api.edit.occ_apply_s",
    ],
    "write": [
        "api.write.total_s",
        "api.write.occ_apply_s",
    ],
    "shell": [
        "api.shell.total_s",
        "api.shell.dispatch_total_s",
        "api.shell.overlay_s",
        "command_exec.run_command_s",
        "command_exec.occ_apply_s",
    ],
    "occ_apply": [
        "occ.apply.total_s",
        "occ.apply.commit_queue_wait_s",
        "occ.apply.commit_worker_s",
        "occ.apply.commit_resume_wait_s",
        "occ.apply.commit_s",
    ],
    "occ_prepare": [
        "occ.prepare.total_s",
        "occ.prepare.gitignore_s",
        "occ.prepare.route_and_base_hash_s",
    ],
    "auto_squash": [
        "layer_stack.auto_squash.total_s",
        "layer_stack.auto_squash.depth_before",
        "layer_stack.auto_squash.depth_after",
        "layer_stack.auto_squash.max_depth",
        "layer_stack.auto_squash.manifest_version",
        "layer_stack.auto_squash.raced",
    ],
    "layer_publish": [
        "layer_stack.publish.total_s",
        "layer_stack.publish.write_manifest_s",
        "layer_stack.transaction.lock_held_s",
        "layer_stack.transaction.lock_wait_s",
    ],
}

# Per-tool gating thresholds — see plan §Performance Acceptance Thresholds.
HARD_FLOOR_KEYS = {
    "api.edit.total_s",
    "api.write.total_s",
    "api.shell.total_s",
    "occ.apply.commit_queue_wait_s",
    "occ.apply.commit_worker_s",
}


@dataclass
class TimingObservation:
    tool_name: str
    is_error: bool
    timings: dict[str, float] = field(default_factory=dict)


def _percentile(values: list[float], q: float) -> float:
    """Linear-interpolation percentile (matches numpy default)."""
    if not values:
        return math.nan
    sv = sorted(values)
    if len(sv) == 1:
        return sv[0]
    pos = (len(sv) - 1) * q
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return sv[int(pos)]
    frac = pos - lower
    return sv[lower] + (sv[upper] - sv[lower]) * frac


def _aggregate(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "avg": statistics.fmean(values),
        "p50": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
        "max": max(values),
        "total": sum(values),
    }


def _walk_message_jsonls(run_dir: Path) -> list[Path]:
    return sorted(run_dir.rglob("message.jsonl"))


def _extract_observations(jsonl_path: Path) -> list[TimingObservation]:
    """Extract one TimingObservation per tool_result row."""
    tool_name_by_id: dict[str, str] = {}
    out: list[TimingObservation] = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = row.get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            btype = block.get("type")
            if btype == "tool_use":
                tu_id = block.get("id")
                name = block.get("name")
                if tu_id and name:
                    tool_name_by_id[tu_id] = name
            elif btype == "tool_result":
                tu_id = block.get("tool_use_id")
                if not tu_id:
                    continue
                name = tool_name_by_id.get(tu_id, "unknown")
                meta = block.get("metadata") or {}
                if not isinstance(meta, dict):
                    continue
                timings = meta.get("timings") or {}
                if not isinstance(timings, dict):
                    continue
                # Coerce floats; drop non-numeric values.
                clean: dict[str, float] = {}
                for k, v in timings.items():
                    try:
                        clean[k] = float(v)
                    except (TypeError, ValueError):
                        continue
                out.append(
                    TimingObservation(
                        tool_name=name,
                        is_error=bool(block.get("is_error", False)),
                        timings=clean,
                    )
                )
    return out


def _scenario_label(run_dir: Path) -> str:
    # scenario_logs/<scenario>/<run_id>/...  → scenario
    for parent in run_dir.resolve().parents:
        if parent.name == "scenario_logs":
            return run_dir.resolve().relative_to(parent).parts[0]
    return run_dir.name


def _load_user_facing_latency(run_dir: Path) -> dict[str, Any]:
    """Pull the runner-captured user-facing latency block from metrics.json."""
    mp = run_dir / "metrics.json"
    if not mp.exists():
        return {}
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def aggregate_run(run_dir: Path) -> dict[str, Any]:
    """Aggregate a single scenario run into the structured metrics dict."""
    observations: list[TimingObservation] = []
    for jp in _walk_message_jsonls(run_dir):
        observations.extend(_extract_observations(jp))

    # Aggregate timing keys (in MILLISECONDS) per tool.
    by_tool_key: dict[str, dict[str, list[float]]] = {}
    tool_call_counts: dict[str, int] = {}
    tool_error_counts: dict[str, int] = {}
    for obs in observations:
        tool_call_counts[obs.tool_name] = tool_call_counts.get(obs.tool_name, 0) + 1
        if obs.is_error:
            tool_error_counts[obs.tool_name] = (
                tool_error_counts.get(obs.tool_name, 0) + 1
            )
        for k, v in obs.timings.items():
            # Convert seconds to ms for `*_s` keys; leave depth/version as-is.
            value_ms = v * 1000.0 if k.endswith("_s") else v
            by_tool_key.setdefault(obs.tool_name, {}).setdefault(k, []).append(
                value_ms
            )

    metrics: dict[str, Any] = {
        "scenario": _scenario_label(run_dir),
        "run_dir": str(run_dir),
        "user_facing_latency": _load_user_facing_latency(run_dir),
        "tools": {},
        "families": {},
    }

    for tool, key_to_values in sorted(by_tool_key.items()):
        tool_block: dict[str, Any] = {
            "call_count": tool_call_counts.get(tool, 0),
            "error_count": tool_error_counts.get(tool, 0),
            "keys": {},
        }
        for key, values in sorted(key_to_values.items()):
            tool_block["keys"][key] = _aggregate(values)
        metrics["tools"][tool] = tool_block

    # Family roll-ups (per-family per-tool, only the keys the plan lists).
    for family, keys in METRIC_FAMILIES.items():
        family_block: dict[str, Any] = {}
        for tool, key_to_values in by_tool_key.items():
            sub: dict[str, dict[str, float]] = {}
            for k in keys:
                if k in key_to_values:
                    sub[k] = _aggregate(key_to_values[k])
            if sub:
                family_block[tool] = sub
        if family_block:
            metrics["families"][family] = family_block

    return metrics


def render_summary_markdown(scenario_metrics: list[dict[str, Any]]) -> str:
    """Render a single summary markdown for a baseline run set."""
    lines: list[str] = ["# OCC Auto-Squash Baseline Metrics", ""]
    for sm in scenario_metrics:
        lines.append(f"## Scenario: `{sm['scenario']}`")
        lines.append("")
        lines.append(f"- Run dir: `{sm['run_dir']}`")
        lines.append("")
        lines.append("### User-facing tool latency (ms) — wallclock at test process")
        lines.append("")
        ufl = (sm.get("user_facing_latency") or {}).get("per_tool") or {}
        if ufl:
            lines.append(
                "| Tool | calls | errors | p50 ms | p95 ms | total ms |"
            )
            lines.append("|---|---:|---:|---:|---:|---:|")
            for tool, ub in sorted(ufl.items()):
                lines.append(
                    f"| `{tool}` | {ub.get('count', 0)} | {ub.get('errors', 0)} | "
                    f"{ub.get('p50_ms', 0):.1f} | {ub.get('p95_ms', 0):.1f} | "
                    f"{ub.get('total_ms', 0):.1f} |"
                )
        else:
            lines.append("_no runner-captured per-tool latency_")
        lines.append("")
        lines.append("### Auto-squash family")
        lines.append("")
        as_block = sm["families"].get("auto_squash") or {}
        if not as_block:
            lines.append("_no auto-squash events captured_")
        else:
            lines.append("| Tool | key | count | p50 | p95 | total |")
            lines.append("|---|---|---:|---:|---:|---:|")
            for tool, sub in as_block.items():
                for key, agg in sub.items():
                    if agg.get("count", 0) == 0:
                        continue
                    lines.append(
                        f"| `{tool}` | `{key}` | {agg['count']} | "
                        f"{agg.get('p50', 0):.1f} | {agg.get('p95', 0):.1f} | "
                        f"{agg.get('total', 0):.1f} |"
                    )
        lines.append("")
        lines.append("### OCC apply family (commit critical path)")
        lines.append("")
        oa_block = sm["families"].get("occ_apply") or {}
        if oa_block:
            lines.append("| Tool | key | count | p50 | p95 | total |")
            lines.append("|---|---|---:|---:|---:|---:|")
            for tool, sub in oa_block.items():
                for key, agg in sub.items():
                    if agg.get("count", 0) == 0:
                        continue
                    lines.append(
                        f"| `{tool}` | `{key}` | {agg['count']} | "
                        f"{agg.get('p50', 0):.1f} | {agg.get('p95', 0):.1f} | "
                        f"{agg.get('total', 0):.1f} |"
                    )
        lines.append("")
    return "\n".join(lines) + "\n"


def render_comparison_markdown(
    baseline: dict[str, Any],
    experimental: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Compare two scenario metrics blocks. Returns (markdown, verdict)."""
    verdict: dict[str, Any] = {
        "scenario": baseline.get("scenario"),
        "behavior_pass": True,
        "perf_pass": True,
        "perf_violations": [],
    }
    lines = [f"# Comparison — `{baseline.get('scenario')}`", ""]
    lines.append("| Tool | Key | Base p95 | Exp p95 | Δ | Verdict |")
    lines.append("|---|---|---:|---:|---:|---|")
    for tool, b_block in baseline.get("tools", {}).items():
        e_block = experimental.get("tools", {}).get(tool)
        if not e_block:
            continue
        for key, b_agg in b_block.get("keys", {}).items():
            e_agg = e_block.get("keys", {}).get(key)
            if not e_agg or b_agg.get("count", 0) == 0:
                continue
            b_p95 = b_agg.get("p95", 0)
            e_p95 = e_agg.get("p95", 0)
            delta = e_p95 - b_p95
            pct = (delta / b_p95 * 100.0) if b_p95 else 0.0
            v = "OK"
            # commit_resume_wait_s should drop ≥ 50% or stay below 500 ms.
            if key == "occ.apply.commit_resume_wait_s":
                if e_p95 >= 500.0 and pct > -50.0:
                    v = "FAIL"
                    verdict["perf_pass"] = False
                    verdict["perf_violations"].append(
                        {"tool": tool, "key": key, "delta_ms": delta, "pct": pct}
                    )
            elif key in HARD_FLOOR_KEYS:
                # Hard floor: > 25% regression on critical-path totals.
                if pct > 25.0:
                    v = "FAIL"
                    verdict["perf_pass"] = False
                    verdict["perf_violations"].append(
                        {"tool": tool, "key": key, "delta_ms": delta, "pct": pct}
                    )
            lines.append(
                f"| `{tool}` | `{key}` | {b_p95:.1f} | {e_p95:.1f} | "
                f"{delta:+.1f} ({pct:+.1f}%) | {v} |"
            )
    return "\n".join(lines) + "\n", verdict


def cmd_baseline(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    all_metrics: list[dict[str, Any]] = []
    for run_dir_str in args.run_dirs:
        run_dir = Path(run_dir_str)
        if not run_dir.exists():
            print(f"WARN: run dir missing: {run_dir}", file=sys.stderr)
            continue
        m = aggregate_run(run_dir)
        scenario = m["scenario"]
        scen_out = out_dir / scenario
        scen_out.mkdir(parents=True, exist_ok=True)
        (scen_out / "metrics.json").write_text(
            json.dumps(m, indent=2), encoding="utf-8"
        )
        all_metrics.append(m)
    summary = render_summary_markdown(all_metrics)
    (out_dir / "summary.md").write_text(summary, encoding="utf-8")
    print(json.dumps({"scenarios": [m["scenario"] for m in all_metrics]}, indent=2))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    # TODO(optimization-PR): this currently only enforces the perf gate from
    # plan §"Performance Acceptance Thresholds". The plan §"Behavior-
    # Equivalence Assertions" (7 invariants — final-content byte-equality,
    # conflict surface, event sequence in report.events + sandbox_events.jsonl,
    # lease invariant, shell multi-path atomicity, fail-closed) MUST be wired
    # into a behavior gate before any experimental run can claim PASS. The
    # `behavior_pass` field below is hard-coded True today.
    baseline_root = Path(args.baseline_dir)
    exp_root = Path(args.experimental_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    overall_pass = True
    verdicts: list[dict[str, Any]] = []
    for run_dir_str in args.run_dir_pairs:
        b_path, e_path = run_dir_str.split("=", 1)
        b_metrics = aggregate_run(baseline_root / b_path)
        e_metrics = aggregate_run(exp_root / e_path)
        md, verdict = render_comparison_markdown(b_metrics, e_metrics)
        scen = b_metrics["scenario"]
        scen_out = out_dir / scen
        scen_out.mkdir(parents=True, exist_ok=True)
        (scen_out / "comparison.md").write_text(md, encoding="utf-8")
        verdicts.append(verdict)
        if not verdict["perf_pass"]:
            overall_pass = False
    (out_dir / "verdict.json").write_text(
        json.dumps({"overall_pass": overall_pass, "scenarios": verdicts}, indent=2),
        encoding="utf-8",
    )
    return 0 if overall_pass else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("baseline", help="aggregate baseline runs")
    pb.add_argument(
        "--out",
        required=True,
        help="output directory (e.g., .omc/perf/baselines/2026-05-11)",
    )
    pb.add_argument(
        "run_dirs", nargs="+", help="scenario run dir paths (one per scenario)"
    )
    pb.set_defaults(func=cmd_baseline)

    pc = sub.add_parser("compare", help="compare baseline vs experimental")
    pc.add_argument("baseline_dir", help="baseline root containing scenario logs")
    pc.add_argument(
        "experimental_dir", help="experimental root containing scenario logs"
    )
    pc.add_argument(
        "--out", required=True, help="output directory for comparison artifacts"
    )
    pc.add_argument(
        "run_dir_pairs",
        nargs="+",
        help="<baseline_run_path>=<experimental_run_path> per scenario",
    )
    pc.set_defaults(func=cmd_compare)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
