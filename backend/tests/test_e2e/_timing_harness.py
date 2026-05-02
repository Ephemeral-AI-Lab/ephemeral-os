"""Live-E2E timing harness — measures, reports, persists, and compares phase timings.

Each phase of the in-sandbox-daemon migration uses this harness to record a
canonical baseline (`phase_<N>_<test>_<ts>.json`) and to render deltas against
the previous phase's baseline. The format is fixed so that report output
can be pasted into PR descriptions verbatim.

Public API (every method has a matching unit test in
``test_timing_harness_unit.py``):

* ``TimingHarness(phase, test_name)`` — construct.
* ``step(name)`` — context manager that times a block.
* ``record(name, *, count, bytes_)`` — attach metadata to a step (or a bare key).
* ``report() -> str`` — render the human-readable report.
* ``dump_json() -> Path`` — write the JSON payload to ``_timings/`` atomically.
* ``compare_to(baseline_path) -> str`` — render per-step deltas vs. a baseline.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

__all__ = ["TimingHarness", "TimingStep"]


_TIMINGS_DIR = Path(__file__).resolve().parent / "_timings"


@dataclass
class TimingStep:
    """One step in a timing run. ``count`` and ``bytes_`` are optional metadata."""

    name: str
    elapsed_s: float = 0.0
    count: int | None = None
    bytes_: int | None = None


@dataclass
class TimingHarness:
    """Records timed steps, renders a fixed-format report, persists JSON."""

    phase: int
    test_name: str
    _steps: list[TimingStep] = field(default_factory=list)
    _step_index: dict[str, TimingStep] = field(default_factory=dict)

    def __init__(self, phase: int, test_name: str) -> None:
        self.phase = phase
        self.test_name = test_name
        self._steps = []
        self._step_index = {}

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        """Time a block. Records elapsed seconds under ``name`` (insertion order)."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            existing = self._step_index.get(name)
            if existing is None:
                ts = TimingStep(name=name, elapsed_s=elapsed)
                self._steps.append(ts)
                self._step_index[name] = ts
            else:
                existing.elapsed_s = elapsed

    def record(
        self,
        name: str,
        *,
        count: int | None = None,
        bytes_: int | None = None,
    ) -> None:
        """Attach a count and/or byte-size to a previously-stepped key.

        If the key has not been ``step()``-ed yet, a new bare entry is created
        with ``elapsed_s == 0.0`` so subsequent reporting still surfaces the
        metadata.
        """
        existing = self._step_index.get(name)
        if existing is None:
            existing = TimingStep(name=name, elapsed_s=0.0)
            self._steps.append(existing)
            self._step_index[name] = existing
        if count is not None:
            existing.count = count
        if bytes_ is not None:
            existing.bytes_ = bytes_

    def report(self) -> str:
        """Render the canonical report.

        Format (reproduced verbatim from the phase-00 spec):

            === Phase N E2E timing breakdown for <test_name> ===
            <name>: <padded><elapsed>s<padding>(<bytes_human>, <count> files)
            ...
            --- TOTAL: <sum>s ---
        """
        header = f"=== Phase {self.phase} E2E timing breakdown for {self.test_name} ==="
        lines = [header]
        total = 0.0
        # Right-pad name column so the elapsed column lines up; spec shows
        # roughly 26 chars before the time column.
        max_name = max((len(s.name) for s in self._steps), default=0)
        name_col = max(max_name + 1, 25)
        for s in self._steps:
            total += s.elapsed_s
            elapsed_str = f"{s.elapsed_s:.3f}s"
            base = f"{(s.name + ':').ljust(name_col)} {elapsed_str}"
            extras = _format_extras(s)
            line = f"{base}   {extras}" if extras else base
            lines.append(line.rstrip())
        lines.append(f"--- TOTAL: {total:.3f}s ---")
        return "\n".join(lines)

    def to_payload(self) -> dict:
        """Build the JSON-serializable payload (used by ``dump_json``)."""
        steps = [
            {
                "name": s.name,
                "elapsed_s": s.elapsed_s,
                "count": s.count,
                "bytes": s.bytes_,
            }
            for s in self._steps
        ]
        total = sum(s.elapsed_s for s in self._steps)
        return {
            "phase": self.phase,
            "test_name": self.test_name,
            "timestamp": datetime.now(UTC).isoformat(),
            "steps": steps,
            "total_s": total,
        }

    def dump_json(self, dir_: Path | None = None) -> Path:
        """Write JSON to ``_timings/phase_<N>_<test>_<ts>.json`` atomically.

        ``dir_`` allows tests to redirect the write target. The default
        is the package-relative ``_timings/`` directory.
        """
        target_dir = Path(dir_) if dir_ is not None else _TIMINGS_DIR
        target_dir.mkdir(parents=True, exist_ok=True)
        # Filesystem-safe ISO timestamp (drop colons; UTC; second resolution).
        ts = (
            datetime.now(UTC)
            .strftime("%Y-%m-%dT%H-%M-%SZ")
        )
        target = target_dir / f"phase_{self.phase}_{self.test_name}_{ts}.json"
        tmp = target.with_suffix(target.suffix + ".tmp")
        payload = json.dumps(self.to_payload(), indent=2)
        tmp.write_text(payload + "\n", encoding="utf-8")
        os.replace(tmp, target)
        return target

    def compare_to(self, baseline_path: Path | str) -> str:
        """Render per-step deltas vs. a baseline JSON written by ``dump_json``.

        New-run order is preserved. Baseline-only steps appear at the end as
        ``(REMOVED)``. Steps not in the baseline are annotated
        ``(NEW cost, must be amortized)``.
        """
        bp = Path(baseline_path)
        baseline_payload = json.loads(bp.read_text(encoding="utf-8"))
        baseline_steps = {
            s["name"]: s for s in baseline_payload.get("steps", [])
        }
        max_name = max(
            (len(s.name) for s in self._steps),
            default=max((len(n) for n in baseline_steps), default=0),
        )
        name_col = max(max_name + 1, 25)
        header = f"--- vs Phase {baseline_payload.get('phase', '?')} baseline ({bp.name}) ---"
        lines = [header]
        seen = set()
        for s in self._steps:
            seen.add(s.name)
            base = baseline_steps.get(s.name)
            current = s.elapsed_s
            if base is None:
                line = (
                    f"{(s.name + ':').ljust(name_col)} +{current:.3f}s "
                    "(NEW cost, must be amortized)"
                )
            else:
                base_elapsed = float(base.get("elapsed_s", 0.0))
                delta = current - base_elapsed
                if base_elapsed <= 0 and delta == 0:
                    annotation = "(no change, expected)"
                elif delta == 0:
                    annotation = "(no change)"
                else:
                    sign = "-" if delta < 0 else "+"
                    pct: str
                    if base_elapsed > 0:
                        pct_value = abs(delta) / base_elapsed * 100.0
                        pct_label = "faster" if delta < 0 else "slower"
                        pct = f"{int(round(pct_value))}% {pct_label}"
                    else:
                        pct = "new baseline"
                    annotation = f"({sign}{abs(delta):.3f}s, {pct})"
                line = (
                    f"{(s.name + ':').ljust(name_col)} {current:.3f}s  {annotation}"
                )
            lines.append(line.rstrip())
        for name, base in baseline_steps.items():
            if name in seen:
                continue
            line = (
                f"{(name + ':').ljust(name_col)} {float(base.get('elapsed_s', 0.0)):.3f}s "
                "(REMOVED)"
            )
            lines.append(line)
        return "\n".join(lines)


def _format_extras(step: TimingStep) -> str:
    """Render the optional ``(<bytes>, <count> files)`` suffix."""
    parts: list[str] = []
    if step.bytes_ is not None:
        parts.append(_format_bytes(step.bytes_))
    if step.count is not None:
        parts.append(f"{step.count} files")
    if not parts:
        return ""
    return f"({', '.join(parts)})"


def _format_bytes(num: int) -> str:
    """Render a byte count in human-readable form (KB/MB/GB)."""
    abs_num = abs(num)
    if abs_num < 1024:
        return f"{num} B"
    if abs_num < 1024 * 1024:
        return f"{num / 1024:.1f} KB"
    if abs_num < 1024 * 1024 * 1024:
        return f"{num / (1024 * 1024):.1f} MB"
    return f"{num / (1024 * 1024 * 1024):.1f} GB"
