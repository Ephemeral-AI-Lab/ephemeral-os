"""pytest conftest — re-exports fixtures for test_runner tests.

The scenario suite intentionally does not load repo ``.env``. Defaults come
from ``ephemeralos.yaml``; explicit overrides must be process env vars.
"""

pytest_plugins = ["test_runner.core.fixtures"]
