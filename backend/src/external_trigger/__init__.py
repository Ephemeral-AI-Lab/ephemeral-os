"""External trigger module — ephemeral agents for external_trigger and post_run phases.

Both external-trigger calls (pause assessment, checkpoint notes) and post-run
calls (submission) use the same ``runner.run()`` loop. External triggers
spawn ephemeral agents via ``agent.spawn_and_run()``.
"""

from external_trigger.agent import spawn_and_run
from external_trigger.runner import RunResult, run

__all__ = ["RunResult", "run", "spawn_and_run"]
