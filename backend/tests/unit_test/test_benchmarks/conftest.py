"""Collection policy for runner-owned benchmark tests.

SWEEvo benchmark tests still exercise ``task_center_runner`` internals. That
runner package is intentionally deferred in the TaskCenter -> Workflow rename,
so these files are not collected by the non-runner unit suite.
"""

collect_ignore_glob = ["test_sweevo_*.py"]
