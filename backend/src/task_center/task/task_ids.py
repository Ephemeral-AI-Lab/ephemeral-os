"""Stable task ids for harness graph planner, generator, and evaluator rows."""

from __future__ import annotations


def planner_task_id(harness_graph_id: str) -> str:
    return f"{harness_graph_id}:planner"


def generator_task_id(harness_graph_id: str, local_task_id: str) -> str:
    return f"{harness_graph_id}:gen:{local_task_id}"


def evaluator_task_id(harness_graph_id: str) -> str:
    return f"{harness_graph_id}:evaluator"
