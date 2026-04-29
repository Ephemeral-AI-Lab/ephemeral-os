from __future__ import annotations

from agents.builtins import BUILTIN_AGENTS, EXPLORER as BUILTIN_EXPLORER
from task_center.graph import TaskGraph
from task_center.harness_agents.advisor.definition import (
    load_system_prompt as load_advisor_prompt,
)
from task_center.harness_agents.evaluator.definition import (
    load_system_prompt as load_evaluator_prompt,
)
from task_center.harness_agents.executor.definition import (
    load_system_prompt as load_executor_prompt,
)
from task_center.harness_agents.explorer.definition import (
    EXPLORER as HARNESS_EXPLORER,
    load_system_prompt as load_explorer_prompt,
)
from task_center.harness_agents.planner.definition import (
    load_system_prompt as load_planner_prompt,
)
from task_center.harness_agents.prompts import build_task_prompt
from task_center.harness_agents.verifier.definition import (
    load_system_prompt as load_verifier_prompt,
)
from task_center.model import Status, Task, TaskSummary


def test_harness_role_prompts_are_concise_contracts() -> None:
    for prompt in (
        load_executor_prompt(),
        load_planner_prompt(),
        load_evaluator_prompt(),
        load_verifier_prompt(),
        load_explorer_prompt(),
        load_advisor_prompt(),
    ):
        assert "**What You Can Do**" in prompt
        assert "**What You Cannot Do**" in prompt
        assert "**Terminal Tools**" in prompt
        assert "**Operating loop**" not in prompt
        assert "**Mode Decision" not in prompt
        assert "Topology examples" not in prompt


def test_explorer_definition_lives_under_harness_agents() -> None:
    assert BUILTIN_EXPLORER is HARNESS_EXPLORER
    assert BUILTIN_EXPLORER.role == "explorer"
    assert BUILTIN_EXPLORER.agent_type == "subagent"
    assert BUILTIN_EXPLORER.system_prompt == load_explorer_prompt()
    assert "explorer" in {agent.name for agent in BUILTIN_AGENTS}


def test_verifier_dispatch_prompt_wraps_dependency_context() -> None:
    graph = TaskGraph()
    dep = Task(
        id="impl",
        role="executor",
        input="implement the thing",
        status=Status.DONE,
        summaries=[
            TaskSummary(
                kind="success",
                text="implemented and checked",
                source_task_id="impl",
            )
        ],
    )
    verifier = Task(
        id="verify",
        role="verifier",
        input="verify the thing",
        status=Status.READY,
        needs=frozenset({"impl"}),
    )
    graph.add(dep)
    graph.add(verifier)

    prompt = build_task_prompt(verifier, graph)

    assert "## DEPENDENCY_SUMMARIES" in prompt
    assert "### impl" in prompt
    assert "input: implement the thing" in prompt
    assert "- [success] implemented and checked" in prompt
    assert "## TASK_INPUT\nverify the thing" in prompt
