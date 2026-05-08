"""Scenario protocol + scenario registry."""

from __future__ import annotations

from benchmarks.sweevo.live_test.scenarios.base import Scenario
from benchmarks.sweevo.live_test.scenarios.correctness_testing import (
    CorrectnessTesting,
)

SCENARIO_REGISTRY: dict[str, type[Scenario]] = {
    "correctness_testing": CorrectnessTesting,
}

__all__ = ["SCENARIO_REGISTRY", "CorrectnessTesting"]
