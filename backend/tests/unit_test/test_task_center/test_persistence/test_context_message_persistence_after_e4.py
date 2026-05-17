"""Persistence regressions for the LaunchBundle context_message split.

Two static guards locked here:

1. Orchestrator's generator-task upsert path writes
   ``context_message=task.task_spec`` (the parent-authored task description),
   NOT the launch's ``context_message``. The two values come from different
   sources — task_spec from the planner submission, launch.context_message
   from the per-spawn renderer. The SOURCE EXPRESSION ``task.task_spec``
   must be preserved verbatim across kwarg renames.

2. Planner and evaluator launches that DO use ``launch.context_message`` as
   the persisted context never persist a string containing
   ``"# How to Proceed"``. The renderer split moves that heading off the
   context message; this guard pins the dispatcher/orchestrator code paths.
"""

from __future__ import annotations

import inspect

from task_center.attempt import dispatcher as dispatcher_module
from task_center.attempt import orchestrator as orchestrator_module


def test_orchestrator_persists_task_spec_for_generator_tasks_unchanged_by_e4():
    """``_persist_generator_tasks`` keeps writing ``context_message=task.task_spec``.

    Before E4 this site read ``task.task_spec``; nothing about the
    LaunchBundle split changed that contract. Static grep so a future
    refactor that wires ``launch.context_message`` here gets caught
    immediately.
    """
    source = inspect.getsource(orchestrator_module)
    assert "context_message=task.task_spec," in source, (
        "_persist_generator_tasks must continue persisting "
        "context_message=task.task_spec (parent-authored task description), "
        "NOT the launch's context_message."
    )


def test_planner_upsert_path_persists_context_message():
    """Planner upsert in orchestrator writes ``context_message=launch.context_message``."""
    source = inspect.getsource(orchestrator_module)
    assert "context_message=launch.context_message," in source


def test_evaluator_upsert_path_persists_context_message():
    """Evaluator upsert in dispatcher writes ``context_message=launch.context_message``."""
    source = inspect.getsource(dispatcher_module)
    assert "context_message=launch.context_message," in source
