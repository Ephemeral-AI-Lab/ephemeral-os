"""Persistence regressions for the AgentEntryMessages context split.

Static guards on the orchestrator's task-upsert sources:

1. Generator-task upsert writes ``context_message=task.task_spec`` (the
   parent-authored task description), NOT the launch's rendered context.
2. Reducer-task upsert writes ``context_message=reducer.prompt`` (the
   reducer's exit-gate prompt), set at plan-submission time.
3. The planner upsert that DOES use ``launch.context`` (the ``<context>``
   envelope) as the persisted ``context_message`` column.

Both generator and reducer plan tasks are upserted in the orchestrator's
``_persist_plan_tasks`` (the old separate evaluator stage is gone — the reducer
is a plan task scheduled in the single RUN stage).
"""

from __future__ import annotations

import inspect

from workflow.attempt import orchestrator as orchestrator_module


def test_orchestrator_persists_task_spec_for_generator_tasks():
    """``_persist_plan_tasks`` persists ``context_message=task.task_spec`` for generators."""
    source = inspect.getsource(orchestrator_module)
    assert "context_message=task.task_spec," in source, (
        "_persist_plan_tasks must persist context_message=task.task_spec "
        "(parent-authored task description), NOT the launch's context."
    )


def test_orchestrator_persists_prompt_for_reducer_tasks():
    """``_persist_plan_tasks`` persists ``context_message=reducer.prompt`` for reducers."""
    source = inspect.getsource(orchestrator_module)
    assert "context_message=reducer.prompt," in source, (
        "_persist_plan_tasks must persist context_message=reducer.prompt "
        "(the reducer's exit-gate prompt)."
    )


def test_planner_upsert_path_persists_context_message():
    """Planner upsert in orchestrator writes ``context_message=launch.context``."""
    source = inspect.getsource(orchestrator_module)
    assert "context_message=launch.context," in source
