"""External trigger module.

The shared ``runner.run()`` loop is used by external-trigger callers such as
pause assessment and task-center checkpoint notes.
"""

from external_trigger.runner import RunResult, run

__all__ = ["RunResult", "run"]
