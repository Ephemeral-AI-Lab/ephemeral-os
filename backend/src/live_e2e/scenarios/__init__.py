"""Scenario protocol + scenario registry."""

from __future__ import annotations

from live_e2e.scenarios.base import Scenario
from live_e2e.scenarios.correctness_testing import (
    CorrectnessTesting,
)
from live_e2e.scenarios.full_case_user_input import (
    FullCaseUserInput,
)
from live_e2e.scenarios.full_stack_adversarial import (
    FullStackAdversarial,
)

SCENARIO_REGISTRY: dict[str, type[Scenario]] = {
    "correctness_testing": CorrectnessTesting,
    "full_case_user_input": FullCaseUserInput,
    "full_stack_adversarial": FullStackAdversarial,
}

__all__ = [
    "SCENARIO_REGISTRY",
    "CorrectnessTesting",
    "FullCaseUserInput",
    "FullStackAdversarial",
]
