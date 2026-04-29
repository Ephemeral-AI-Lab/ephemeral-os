"""Verifier role — mid-graph node-scoped verification.

A verifier validates the work of its DAG dependencies against its own
verification specification (its task_input). It is scoped to one node — no
root_goal, no graph-level plan summary, no sibling work outside its dep set.

End-of-graph closure decisions belong to the evaluator, not the verifier.
"""

from task_center.harness_agents.verifier import lifecycle
from task_center.harness_agents.verifier.context import (
    VerifierLaunchContext,
    build_verifier_launch_context,
)
from task_center.harness_agents.verifier.definition import (
    VERIFIER,
    load_system_prompt,
)

__all__ = [
    "VERIFIER",
    "VerifierLaunchContext",
    "build_verifier_launch_context",
    "lifecycle",
    "load_system_prompt",
]
