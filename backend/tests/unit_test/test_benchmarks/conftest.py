"""Collection policy for runner-owned benchmark tests.

SWEEvo benchmark tests still exercise ``test_runner`` internals. That
runner package is intentionally deferred in the task/request -> Workflow rename,
so these files are not collected by the non-runner unit suite.
"""

collect_ignore_glob = ["test_sweevo_*.py"]
