"""External trigger module — shared runner for external_trigger and post_run tool phases.

Replaces the former ``ephemeral_task`` module. Both external-trigger calls
(pause assessment, checkpoint notes) and post-run calls (submission) use
the same ``runner.run()`` loop.
"""

from external_trigger.runner import RunResult, run

__all__ = ["RunResult", "run"]
