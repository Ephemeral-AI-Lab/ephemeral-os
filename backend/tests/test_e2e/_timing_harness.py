"""Live-E2E timing harness — measures, reports, persists, and compares phase timings.

Each phase of the in-sandbox-daemon migration uses this harness to record a
canonical baseline (`phase_<N>_<test>_<ts>.json`) and to render deltas against
the previous phase's baseline. The format is fixed so that report output
can be pasted into PR descriptions verbatim.

Public API (every method has a matching unit test in
``test_timing_harness_unit.py``):

* ``TimingHarness(phase, test_name)`` — construct.
* ``step(name)`` — context manager that times a block.
* ``step_repeat(name, n)`` — yields *n* sample-context-managers under one
  distribution; ``report()`` renders p50/p95/p99/min/max.
* ``record_distribution(name, samples)`` — record a pre-collected concurrent
  timing distribution.
* ``record(name, *, count, bytes_)`` — attach metadata to a step (or a bare key).
* ``sample_rss_mb(label, transport, sandbox_id, pid)`` — one MB sample of
  ``/proc/<pid>/status``\\ 's ``VmRSS`` over the transport.
* ``sample_fds(label, transport, sandbox_id, pid)`` — one FD-count sample
  of ``ls /proc/<pid>/fd | wc -l`` over the transport.
* ``report() -> str`` — render the human-readable report.
* ``dump_json() -> Path`` — write the JSON payload to ``_timings/`` atomically.
* ``compare_to(baseline_path) -> str`` — render per-step deltas vs. a baseline.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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

    phase: int | float
    test_name: str
    _steps: list[TimingStep] = field(default_factory=list)
    _step_index: dict[str, TimingStep] = field(default_factory=dict)
    distributions: dict[str, dict[str, float]] = field(default_factory=dict)
    values: dict[str, float] = field(default_factory=dict)
    _samples: dict[str, list[float]] = field(default_factory=dict)

    def __init__(self, phase: int | float, test_name: str) -> None:
        self.phase = phase
        self.test_name = test_name
        self._steps = []
        self._step_index = {}
        self.distributions = {}
        self.values = {}
        self._samples = {}

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        """Time a block. Records elapsed seconds under ``name`` (insertion order)."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.values[name] = elapsed
            existing = self._step_index.get(name)
            if existing is None:
                ts = TimingStep(name=name, elapsed_s=elapsed)
                self._steps.append(ts)
                self._step_index[name] = ts
            else:
                existing.elapsed_s = elapsed

    def step_repeat(self, name: str, n: int = 100) -> Iterator[Any]:
        """Yield ``n`` step-context-managers under the same distribution name.

        Each yielded item is itself a context manager wrapping ``time.perf_counter``;
        on exit it appends the elapsed seconds to ``self._samples[name]``.
        :meth:`report` later renders ``p50/p95/p99/min/max (N samples)``.

        Usage::

            for step in h.step_repeat("write_file", n=100):
                with step:
                    svc.write_file([WriteSpec(...)])
        """
        bucket = self._samples.setdefault(name, [])

        @contextmanager
        def _one_sample() -> Iterator[None]:
            t0 = time.perf_counter()
            try:
                yield
            finally:
                bucket.append(time.perf_counter() - t0)

        for _ in range(int(n)):
            yield _one_sample()
        self.distributions[name] = _percentiles(bucket)

    def record_distribution(self, name: str, samples: list[float]) -> dict[str, float]:
        """Record a distribution whose samples were collected externally.

        Concurrent live E2E tests cannot use :meth:`step_repeat` because their
        samples complete out of order across asyncio tasks. This method keeps
        those probes on the same JSON/report schema.
        """
        bucket = [float(sample) for sample in samples]
        self._samples[name] = bucket
        stats = _percentiles(bucket)
        self.distributions[name] = stats
        return stats

    def sample_rss_mb(
        self,
        label: str,
        transport: Any,
        sandbox_id: str,
        pid: int,
    ) -> float:
        """Read ``VmRSS`` from ``/proc/<pid>/status`` over the transport (MB)."""
        cmd = f"cat /proc/{int(pid)}/status | grep VmRSS"
        text = _exec_get_stdout(transport, sandbox_id, cmd)
        mb = _parse_vmrss_mb(text)
        self.values[label] = mb
        return mb

    def sample_fds(
        self,
        label: str,
        transport: Any,
        sandbox_id: str,
        pid: int,
    ) -> int:
        """Count open FDs by listing ``/proc/<pid>/fd`` over the transport."""
        cmd = f"ls /proc/{int(pid)}/fd 2>/dev/null | wc -l"
        text = _exec_get_stdout(transport, sandbox_id, cmd)
        try:
            count = int((text or "0").strip().split()[0])
        except (ValueError, IndexError):
            count = 0
        self.values[label] = float(count)
        return count

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

        Phase 3.5 extends the format with two optional sections:

            --- DISTRIBUTIONS ---
            <name>: p50=Xs p95=Ys p99=Zs (N samples)
            --- RESOURCE SAMPLES ---
            <label>: <value>
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
        if self.distributions:
            lines.append("--- DISTRIBUTIONS ---")
            d_max = max((len(n) for n in self.distributions), default=0)
            d_col = max(d_max + 1, name_col)
            for name, stats in self.distributions.items():
                line = (
                    f"{(name + ':').ljust(d_col)} "
                    f"p50={stats['p50']:.4f}s p95={stats['p95']:.4f}s "
                    f"p99={stats['p99']:.4f}s "
                    f"min={stats['min']:.4f}s max={stats['max']:.4f}s "
                    f"({int(stats['n'])} samples)"
                )
                lines.append(line)
        if self.values:
            lines.append("--- RESOURCE SAMPLES ---")
            v_max = max((len(n) for n in self.values), default=0)
            v_col = max(v_max + 1, name_col)
            for label, value in self.values.items():
                lines.append(
                    f"{(label + ':').ljust(v_col)} {value:.2f}"
                )
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
        # Convert tiny IEEE noise on percentiles into stable JSON.
        distributions = {
            name: {k: float(v) for k, v in stats.items()}
            for name, stats in self.distributions.items()
        }
        return {
            "phase": self.phase,
            "test_name": self.test_name,
            "timestamp": datetime.now(UTC).isoformat(),
            "steps": steps,
            "total_s": total,
            "distributions": distributions,
            "values": dict(self.values),
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


# ---------------------------------------------------------------------------
# Phase 3.5 — distribution + resource sampling helpers
# ---------------------------------------------------------------------------


def _percentiles(samples: list[float]) -> dict[str, float]:
    """Return p50/p95/p99/min/max/n for a list of elapsed seconds."""
    if not samples:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0, "n": 0}
    ordered = sorted(samples)
    n = len(ordered)

    def _q(p: float) -> float:
        # Nearest-rank percentile; matches numpy.percentile(..., method='lower')
        # for small samples without pulling numpy into the test deps.
        idx = int(math.ceil(p * n)) - 1
        idx = max(0, min(idx, n - 1))
        return ordered[idx]

    return {
        "p50": _q(0.50),
        "p95": _q(0.95),
        "p99": _q(0.99),
        "min": ordered[0],
        "max": ordered[-1],
        "n": n,
    }


def _exec_get_stdout(transport: Any, sandbox_id: str, command: str) -> str:
    """Run ``command`` over ``transport.exec`` (sync OR async) and return stdout.

    The harness uses a thin shim because some live-test fixtures hand back a
    sync ``raw_sandbox.process.exec`` (sweevo) and others an async transport.
    """
    raw = None
    err: Exception | None = None
    # Async transport path: ``transport.exec(sandbox_id, cmd)`` returning a
    # coroutine resolved via the harness-side asyncio loop.
    try:
        import asyncio

        coro = transport.exec(sandbox_id, command, timeout=30)
        if asyncio.iscoroutine(coro):
            raw = asyncio.get_event_loop().run_until_complete(coro)
        else:
            raw = coro
    except Exception as exc:  # pragma: no cover - exercised by sync fallback
        err = exc

    if raw is None:
        # Sync transport / raw sandbox object.
        try:
            response = getattr(transport, "exec", None)
            if response is None:
                # Final fallback: assume transport IS the sandbox object exposing process.exec.
                response = transport.process.exec(command, timeout=30)
            else:
                response = response(sandbox_id, command, timeout=30)
            raw = response
        except Exception:  # pragma: no cover - defensive
            if err is not None:
                raise err  # noqa: B904
            raise

    return str(
        getattr(raw, "stdout", None)
        or getattr(raw, "result", None)
        or ""
    )


def _parse_vmrss_mb(status_text: str) -> float:
    """Parse the ``VmRSS:`` line of ``/proc/<pid>/status`` into MB."""
    for line in (status_text or "").splitlines():
        if line.lower().startswith("vmrss:"):
            parts = line.split()
            if len(parts) >= 3:
                try:
                    kb = float(parts[1])
                    return round(kb / 1024.0, 2)
                except ValueError:
                    return 0.0
    return 0.0
