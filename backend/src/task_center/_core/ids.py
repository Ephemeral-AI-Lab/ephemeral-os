"""Stable task ids for harness attempt planner, generator, and evaluator rows."""

from __future__ import annotations


def planner_task_id(attempt_id: str) -> str:
    return f"{attempt_id}:planner"


def generator_task_id(attempt_id: str, local_task_id: str) -> str:
    return f"{attempt_id}:gen:{local_task_id}"


def evaluator_task_id(attempt_id: str) -> str:
    return f"{attempt_id}:evaluator"
