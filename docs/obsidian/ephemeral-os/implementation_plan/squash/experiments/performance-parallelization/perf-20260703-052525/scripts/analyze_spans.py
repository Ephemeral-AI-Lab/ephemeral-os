#!/usr/bin/env python3
"""Attribute squash latency from a harvested daemon observability.ndjson.

Each squash invocation is one trace containing a `layerstack.squash` span
(open + plan + build + commit + the remount sweep + result assembly) and one
`workspace_session.remount` child span per swept session (dur_ms + disposition).

For each squash trace we report:
  - parent  : layerstack.squash dur_ms (the whole daemon-side op)
  - sweep   : sum of remount child dur_ms (serial, so ~= wall time of the sweep)
  - non_sweep = parent - sweep - faulty_destroy  (~= open+plan+build+commit)
  - a per-disposition breakdown of the remount spans with min/median/max/sum

Usage: analyze_spans.py FILE [FILE ...] [--json OUT.json]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict

SQUASH = "layerstack.squash"
REMOUNT = "workspace_session.remount"
DESTROY = "workspace_session.destroy"


def load_spans(paths):
    spans = []
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("kind") == "span":
                    spans.append(rec)
    return spans


def dist(values):
    if not values:
        return {"n": 0}
    values = sorted(values)
    return {
        "n": len(values),
        "sum_ms": round(sum(values), 3),
        "min_ms": round(values[0], 3),
        "median_ms": round(statistics.median(values), 3),
        "max_ms": round(values[-1], 3),
        "mean_ms": round(statistics.fmean(values), 3),
    }


def analyze(spans):
    by_trace = defaultdict(list)
    for span in spans:
        by_trace[span.get("trace")].append(span)

    invocations = []
    for trace, group in by_trace.items():
        squash = [s for s in group if s.get("name") == SQUASH]
        if not squash:
            continue
        parent = max(s["dur_ms"] for s in squash)
        remounts = [s for s in group if s.get("name") == REMOUNT]
        destroys = [s for s in group if s.get("name") == DESTROY]

        by_disp = defaultdict(list)
        for s in remounts:
            disp = str(s.get("attrs", {}).get("disposition", "?"))
            disp = disp.split("{")[0].strip()  # "Leased { reason: .. }" -> "Leased"
            by_disp[disp].append(s["dur_ms"])

        sweep_sum = sum(s["dur_ms"] for s in remounts)
        destroy_sum = sum(s["dur_ms"] for s in destroys)
        blocks = max((s.get("attrs", {}).get("blocks", 0) for s in squash), default=0)
        invocations.append({
            "trace": trace,
            "ts": min(s["ts"] for s in squash),
            "blocks": blocks,
            "parent_ms": round(parent, 3),
            "sweep_ms": round(sweep_sum, 3),
            "faulty_destroy_ms": round(destroy_sum, 3),
            "non_sweep_ms": round(parent - sweep_sum - destroy_sum, 3),
            "sessions_swept": len(remounts),
            "by_disposition": {
                d: dist(v) for d, v in sorted(by_disp.items())
            },
        })

    invocations.sort(key=lambda inv: inv["ts"])
    return invocations


def render(invocations):
    lines = []
    for i, inv in enumerate(invocations, 1):
        lines.append(f"=== squash invocation {i}  (blocks={inv['blocks']}, sessions={inv['sessions_swept']}) ===")
        lines.append(f"  parent (layerstack.squash) : {inv['parent_ms']:>10.3f} ms")
        lines.append(f"  remount sweep (serial sum) : {inv['sweep_ms']:>10.3f} ms"
                     f"   ({pct(inv['sweep_ms'], inv['parent_ms'])} of parent)")
        if inv["faulty_destroy_ms"]:
            lines.append(f"  faulty destroys            : {inv['faulty_destroy_ms']:>10.3f} ms")
        lines.append(f"  non-sweep (open+plan+build+commit) :"
                     f" {inv['non_sweep_ms']:>10.3f} ms   ({pct(inv['non_sweep_ms'], inv['parent_ms'])} of parent)")
        for disp, d in inv["by_disposition"].items():
            if d["n"] == 0:
                continue
            lines.append(f"    {disp:<10} n={d['n']:>4}  sum={d['sum_ms']:>9.3f}  "
                         f"min={d['min_ms']:>7.3f}  med={d['median_ms']:>7.3f}  max={d['max_ms']:>7.3f}  mean={d['mean_ms']:>7.3f} ms")
        lines.append("")
    return "\n".join(lines)


def pct(part, whole):
    if not whole:
        return "  n/a"
    return f"{100.0 * part / whole:5.1f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args()

    spans = load_spans(args.files)
    invocations = analyze(spans)
    if not invocations:
        print("no layerstack.squash spans found", file=sys.stderr)
        return 1
    print(render(invocations))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(invocations, handle, indent=2)
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
