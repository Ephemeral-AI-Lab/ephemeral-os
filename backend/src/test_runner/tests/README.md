# Test Runner Test Suites

## Layout

- `mock/` — deterministic mocked-agent scenario tests. These may use the
  SWE-EVO Docker image as an environment fixture, but they do not run a real
  agent. `contracts/`, `environments/`, `sandbox/`, and `request/` are
  subcategories of this mocked-agent boundary.
  - `mock/sandbox/` — sandbox connection, stability, load, and
    sandbox-heavy task/request workflow tests.
  - `mock/request/` — basic task/request workflow correctness
    tests that are not load tests.
- `real_agent/` — tests that run real LLM agents through the test-runner
  runner.

The full SWE-EVO benchmark lifecycle is not a pytest suite. Run it through the
benchmark CLI:

```bash
uv run python -m test_runner.benchmarks.sweevo \
  --instance-id dask__dask_2023.3.2_2023.4.0
```

## Commands

```bash
uv run pytest -q backend/src/test_runner/tests/mock
uv run pytest -q backend/src/test_runner/tests/mock/sandbox
uv run pytest -q backend/src/test_runner/tests/mock/request
uv run pytest -q backend/src/test_runner/tests/real_agent
uv run python -m test_runner.benchmarks.sweevo \
  --instance-id dask__dask_2023.3.2_2023.4.0
```
