"""pytest conftest — re-exports fixtures for task_center_runner tests.

The scenario suite intentionally does not load repo ``.env``. Defaults come
from ``ephemeralos.yaml``; explicit overrides must be process env vars.
"""

pytest_plugins = ["task_center_runner.core.fixtures"]
