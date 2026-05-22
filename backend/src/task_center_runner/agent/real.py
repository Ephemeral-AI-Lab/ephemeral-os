"""``real_agent_runner_factory`` — runner factory for the real-LLM path.

Per plan §2, the real-agent path uses ``runner=None`` when calling
``start_task_center_run`` so the production attempt-agent launcher
takes over. This module exposes a single tiny factory that callers (e.g.
``run_sweevo_real_agent`` shim, ``entrypoints/__main__.py``) pass as
``RunConfig.runner_factory``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from task_center_runner.core.config import RunContext


def real_agent_runner_factory(ctx: "RunContext") -> None:
    """Real-LLM runner factory: ``None`` means use the production runner."""
    return None


__all__ = ["real_agent_runner_factory"]
