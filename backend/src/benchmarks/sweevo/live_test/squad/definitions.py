"""Mock SWE-EVO squad AgentDefinitions.

Relocated from ``benchmarks.sweevo.mock_agent_execution`` in S-03.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from agents import (
    AgentDefinition,
    list_definitions,
    register_definition,
    unregister_definition,
)


@contextlib.contextmanager
def registered_mock_sweevo_agents() -> Iterator[None]:
    """Temporarily install the minimal TaskCenter squad definitions."""
    previous = list_definitions()
    for definition in previous:
        unregister_definition(definition.name)

    for definition in mock_agent_definitions():
        register_definition(definition)

    try:
        yield
    finally:
        for definition in list_definitions():
            unregister_definition(definition.name)
        for definition in previous:
            register_definition(definition)


def mock_agent_definitions() -> tuple[AgentDefinition, ...]:
    return (
        AgentDefinition(
            name="entry_executor",
            description="SWE-EVO mock entry executor",
            system_prompt="You are the SWE-EVO entry executor. Decide whether to execute the request directly or request a mission solution.",
            role="executor",
            context_recipe="entry_executor_v1",
            terminals=[
                "request_mission_solution",
                "submit_execution_success",
                "submit_execution_failure",
            ],
        ),
        AgentDefinition(
            name="planner",
            description="SWE-EVO mock planner",
            system_prompt="You are the SWE-EVO planner. Convert the mission into executable and verifiable task graph work.",
            role="planner",
            context_recipe="planner_v1",
            terminals=["submit_full_plan", "submit_partial_plan"],
        ),
        AgentDefinition(
            name="executor",
            description="SWE-EVO mock executor",
            system_prompt="You are the SWE-EVO executor. Use the available sandbox tools to complete the assigned repository task.",
            role="executor",
            context_recipe="generator_v1",
            allowed_tools=["read_file", "write_file", "edit_file", "shell"],
            terminals=[
                "request_mission_solution",
                "submit_execution_success",
                "submit_execution_failure",
            ],
        ),
        AgentDefinition(
            name="verifier",
            description="SWE-EVO mock verifier",
            system_prompt="You are the SWE-EVO verifier. Inspect sandbox evidence and report whether the assigned checkpoint passes.",
            role="verifier",
            context_recipe="generator_v1",
            allowed_tools=["read_file", "shell"],
            terminals=[
                "submit_verification_success",
                "submit_verification_failure",
            ],
        ),
        AgentDefinition(
            name="evaluator",
            description="SWE-EVO mock evaluator",
            system_prompt="You are the SWE-EVO evaluator. Judge final mission evidence against the mission criteria.",
            role="evaluator",
            context_recipe="evaluator_v1",
            terminals=["submit_evaluation_success", "submit_evaluation_failure"],
        ),
    )


__all__ = [
    "mock_agent_definitions",
    "registered_mock_sweevo_agents",
]
