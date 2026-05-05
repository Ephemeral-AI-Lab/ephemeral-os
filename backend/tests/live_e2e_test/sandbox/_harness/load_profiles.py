"""Named load profiles. See ``../load_testing_standard.md``."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoadProfile:
    name: str
    shells_per_sec: int
    edits_per_sec: int
    duration_s: int
    overlap_ratio: float
    gitignored_ratio: float
    max_p99_ms: int
    max_drift: int
    max_emergency_depth_events: int


SMOKE = LoadProfile(
    name="smoke",
    shells_per_sec=2,
    edits_per_sec=4,
    duration_s=30,
    overlap_ratio=0.25,
    gitignored_ratio=0.40,
    max_p99_ms=500,
    max_drift=0,
    max_emergency_depth_events=0,
)

SUSTAINED = LoadProfile(
    name="sustained",
    shells_per_sec=8,
    edits_per_sec=16,
    duration_s=60,
    overlap_ratio=0.50,
    gitignored_ratio=0.40,
    max_p99_ms=1_000,
    max_drift=0,
    max_emergency_depth_events=0,
)

# §8 default (adopted): tighten `burst` to zero emergency-depth events to
# match E5's pass-bar; if the bar proves too strict we revisit before
# wiring this profile into CI promotion gates.
BURST = LoadProfile(
    name="burst",
    shells_per_sec=30,
    edits_per_sec=60,
    duration_s=20,
    overlap_ratio=0.50,
    gitignored_ratio=0.40,
    max_p99_ms=2_500,
    max_drift=0,
    max_emergency_depth_events=0,
)

SOAK = LoadProfile(
    name="soak",
    shells_per_sec=4,
    edits_per_sec=8,
    duration_s=15 * 60,
    overlap_ratio=0.35,
    gitignored_ratio=0.40,
    max_p99_ms=1_200,
    max_drift=0,
    max_emergency_depth_events=0,
)

PROFILES: dict[str, LoadProfile] = {
    profile.name: profile for profile in (SMOKE, SUSTAINED, BURST, SOAK)
}


def profile(name: str) -> LoadProfile:
    try:
        return PROFILES[name]
    except KeyError as exc:
        raise KeyError(f"unknown load profile: {name!r}") from exc


__all__ = ["LoadProfile", "SMOKE", "SUSTAINED", "BURST", "SOAK", "PROFILES", "profile"]
