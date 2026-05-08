"""Scenario protocol + scenario registry."""

from __future__ import annotations

from benchmarks.sweevo.live_test.scenarios.base import Scenario
from benchmarks.sweevo.live_test.scenarios.correctness_testing import (
    CorrectnessTesting,
)
from benchmarks.sweevo.live_test.scenarios.full_case_user_input import (
    FullCaseUserInput,
)

SCENARIO_REGISTRY: dict[str, type[Scenario]] = {
    "correctness_testing": CorrectnessTesting,
    "full_case_user_input": FullCaseUserInput,
}

__all__ = ["SCENARIO_REGISTRY", "CorrectnessTesting", "FullCaseUserInput"]
