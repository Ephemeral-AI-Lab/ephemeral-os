"""``pipeline_run`` pytest fixture — auto-awaits ``performance_report_task``.

Phase 3 of the task_center_runner restructure decouples perf-report writes
from ``AuditRecorder.dispose()``: the report is now produced by an
``asyncio.Task`` whose handle rides on
``RunReport.performance_report_task`` /
``RealAgentRunReport.performance_report_task`` /
``PipelineReport.performance_report_task``. Tests that drive ``run_scenario`` /
``run_sweevo_real_agent`` (and, in Phase 4, ``run_pipeline``) MUST await that
task to avoid:

- ``Task was destroyed but it is pending!`` warnings at event-loop teardown;
- silent loss of ``performance_report.json`` when the writer is interrupted.

The ``pipeline_run`` fixture is the canonical test surface: callers pass each
report they produce to ``pipeline_run(report)`` and the fixture awaits any
non-None ``performance_report_task`` during teardown, logging the resulting
path.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import Any

import pytest


_log = logging.getLogger(__name__)


@pytest.fixture
def pipeline_run() -> Iterator[Any]:
    """Yield a tracker that auto-awaits each tracked report's perf-report task.

    Usage::

        async def test_something(pipeline_run):
            report = await run_scenario(scenario, ...)
            pipeline_run(report)  # register; teardown awaits the perf task
            ...

    On teardown the fixture awaits every tracked report's
    ``performance_report_task`` if non-None.
    """
    tracked: list[Any] = []

    def track(report: Any) -> Any:
        tracked.append(report)
        return report

    yield track

    pending = [
        getattr(report, "performance_report_task", None) for report in tracked
    ]
    pending_tasks = [task for task in pending if task is not None and not task.done()]
    if not pending_tasks:
        return

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    async def _drain() -> None:
        results = await asyncio.gather(*pending_tasks, return_exceptions=True)
        for task, result in zip(pending_tasks, results, strict=True):
            if isinstance(result, BaseException):
                _log.warning(
                    "pipeline_run: perf-report task %s raised: %s", task.get_name(), result
                )
            else:
                _log.debug(
                    "pipeline_run: perf-report task %s wrote %s",
                    task.get_name(),
                    result,
                )

    if loop.is_running():
        # Schedule on the running loop; the test caller is responsible for not
        # exiting before the task can drain. This branch is unusual — pytest
        # teardown runs after the test's event loop closes, so we rarely hit
        # it. Logging only.
        _log.warning(
            "pipeline_run: event loop still running at teardown; "
            "perf-report draining may not complete before the loop closes."
        )
    else:
        loop.run_until_complete(_drain())


__all__ = ["pipeline_run"]
