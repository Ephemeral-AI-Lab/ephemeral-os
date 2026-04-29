"""Role-class aliases used by the task graph.

`TaskRole` (in `task.py`) is the stored role per task. `GeneratorRole` is
the subset of in-DAG node roles a planner may emit on a `submit_full_plan`
or `submit_partial_plan` DAG entry.
"""

from __future__ import annotations

from typing import Literal

GeneratorRole = Literal["executor", "verifier"]

__all__ = ["GeneratorRole"]
