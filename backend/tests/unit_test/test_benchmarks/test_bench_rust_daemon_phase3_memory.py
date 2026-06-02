from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from bench_rust_daemon_phase3 import (  # noqa: E402
    ROSETTA_ACTIVE_MEMORY_HEADROOM_KB,
    summarize_memory,
)


def test_rosetta_memory_gate_allows_small_cp0_fallback_headroom() -> None:
    baseline_kb = 36_676
    active_peak_kb = baseline_kb + ROSETTA_ACTIVE_MEMORY_HEADROOM_KB - 1

    report = summarize_memory(
        [
            memory_sample("idle_before_load", 6_000, rosetta=True),
            memory_sample("after_load_matrix", active_peak_kb, rosetta=True),
            memory_sample("idle_after_drain", active_peak_kb, rosetta=True),
        ],
        {"cp0": {"daemon_idle_rss_kb": baseline_kb}},
    )

    assert report["active_memory_gate_pass"] is True
    assert report["idle_return_basis"] == "rosetta_active_peak_ceiling"
    assert report["gate_pass"] is True
    assert report["baseline"]["active_memory_headroom_kb"] == 2048
    assert report["baseline"]["active_memory_limit_kb"] == baseline_kb + 2048


def test_native_memory_gate_keeps_exact_cp0_fallback_threshold() -> None:
    baseline_kb = 36_676
    active_peak_kb = baseline_kb + 1

    report = summarize_memory(
        [
            memory_sample("idle_before_load", 6_000, rosetta=False),
            memory_sample("after_load_matrix", active_peak_kb, rosetta=False),
            memory_sample("idle_after_drain", active_peak_kb, rosetta=False),
        ],
        {"cp0": {"daemon_idle_rss_kb": baseline_kb}},
    )

    assert report["active_memory_gate_pass"] is False
    assert report["idle_return_basis"] == "cold_idle_failed"
    assert report["gate_pass"] is False
    assert report["baseline"]["active_memory_headroom_kb"] == 0
    assert report["baseline"]["active_memory_limit_kb"] == baseline_kb


def memory_sample(label: str, pss_kb: int, *, rosetta: bool) -> dict[str, object]:
    return {
        "label": label,
        "cmdline": "/eos/daemon/eosd",
        "exe": "/run/rosetta/rosetta" if rosetta else "/eos/daemon/eosd",
        "smaps_Pss_kb": pss_kb,
        "smaps_Rss_kb": pss_kb + 512,
    }
