"""Verifier dispatch context construction.

The verifier validates the work of its DAG dependencies against its own
verification specification (its ``task_input``). It is scoped to one node —
it does NOT see ``root_goal``, plan summary, or sibling work outside its
dep set. End-of-graph closure decisions belong to the evaluator.

The structural shape of ``VerifierLaunchContext`` mirrors
``ExecutorLaunchContext`` because both roles are scoped the same way (own
task input + DONE deps). The role-specific behavior (run independent
checks, do not trust self-reports, decide pass/fail) lives in the prompt
and the verifier's ``agent.md``, not in the context shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from task_center.graph.store import TaskGraph
from task_center.harness_agents.executor.context import DependencyBundle
from task_center.model import HarnessGraphId, Status, Task, TaskId

_VERIFIER_PROMPT_INSTRUCTIONS = (
    "Read DEPENDENCY_SUMMARIES as the artifacts to verify. Run independent "
    "verification against TASK_INPUT (your verification specification) — do "
    "not trust executor self-reports. Probe boundaries, look for circular "
    "tests. Then decide submit_verification_success or "
    "submit_verification_failure (after consulting the advisor)."
)


@dataclass
class VerifierLaunchContext:
    """Structural context for a mid-graph verifier task at dispatch time.

    Shape mirrors ``ExecutorLaunchContext``: same scoping (own task + DONE
    deps), different role prompt. Re-uses ``DependencyBundle`` from the
    executor module so the dep representation is single-sourced.
    """

    task_id: TaskId
    task_input: str
    harness_graph_id: HarnessGraphId | None
    completed_dependencies: list[DependencyBundle] = field(default_factory=list)

    def to_verifier_prompt(self) -> str:
        if not self.completed_dependencies:
            deps_block = "(none)"
        else:
            parts: list[str] = []
            for dep in self.completed_dependencies:
                summary_lines = "\n".join(
                    f"  - [{s.kind}] {s.text}" for s in dep.summaries
                ) or "  (no summaries)"
                parts.append(
                    f"### {dep.task_id}\n"
                    f"input: {dep.task_input}\n"
                    f"summaries:\n{summary_lines}"
                )
            deps_block = "\n\n".join(parts)
        return (
            f"## INSTRUCTIONS\n{_VERIFIER_PROMPT_INSTRUCTIONS}\n\n"
            f"## DEPENDENCY_SUMMARIES\n{deps_block}\n\n"
            f"## TASK_INPUT\n{self.task_input}"
        )


def build_verifier_launch_context(
    graph: TaskGraph, task: Task
) -> VerifierLaunchContext:
    """Bundle a verifier's task input with its DONE dependency summaries."""
    if task.role != "verifier":
        raise ValueError("build_verifier_launch_context requires a verifier caller")
    deps: list[DependencyBundle] = []
    for dep_id in sorted(task.needs):
        dep = graph.tasks.get(dep_id)
        if dep is None or dep.status is not Status.DONE:
            continue
        deps.append(
            DependencyBundle(
                task_id=dep.id,
                task_input=dep.input,
                summaries=list(dep.summaries),
            )
        )
    return VerifierLaunchContext(
        task_id=task.id,
        task_input=task.input,
        harness_graph_id=task.task_center_harness_graph_id,
        completed_dependencies=deps,
    )
