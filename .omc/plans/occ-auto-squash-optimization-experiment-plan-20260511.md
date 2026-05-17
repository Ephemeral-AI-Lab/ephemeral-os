# OCC Auto-Squash Optimization — Final Decision

Generated: 2026-05-11
Parent report: `.omc/plans/occ-layer-stack-commit-resume-auto-squash-report-20260511.md`
Verification harness: `.omc/plans/occ-auto-squash-perf-verification-test-plan-20260511.md`
Baseline numbers: `.omc/perf/baselines/2026-05-11/`
Aggregator: `backend/scripts/perf/auto_squash_compare.py`
Status: **Implemented — H1 coalesced synchronous squash is the only supported path.**

## Decision

The accepted optimization is the H1 coalesced synchronous squash path. Auto-squash
still runs after publish, but concurrent publish callers no longer all perform
redundant squash work. When a squash is already in flight, later callers mark a
single pending re-check and return after their publish path completes.

This keeps auto-squash failure semantics synchronous and fail-closed while removing
the observed CAS-race amplification from the hot edit/write path.

## Removed Options

The following experiment options are no longer supported runtime choices:

| Removed option | Reason |
|---|---|
| Async background squash | Best latency projection, but introduces a new maintenance-failure surface where a returned tool call can succeed while later squash work fails. |
| Runtime squash-depth tuning | Lower-risk as a sensitivity probe, but it shifts work and increases manifest-depth variance rather than fixing redundant squash contention. |
| Squash-mode environment selection | Production now has one known path; daemon startup does not forward or honor squash mode parameters. |

## Current Runtime Contract

- `AUTO_SQUASH_MAX_DEPTH` remains the fixed production threshold.
- H1 coalescing is default and unconditional.
- `OccService` does not expose async maintenance workers, queues, drain hooks, or
  maintenance-status metrics.
- Daemon transport does not forward host squash-mode or squash-depth environment
  variables.
- Tool calls keep the existing fail-closed behavior: if required synchronous OCC
  maintenance fails, the originating operation does not silently hide it behind an
  async monitor event.

## Verification

The implementation must keep the following checks green:

```bash
uv run ruff check \
  backend/src/sandbox/host/daemon_client.py \
  backend/src/sandbox/occ/service.py \
  backend/src/sandbox/runtime/daemon/service/occ_backend.py \
  backend/src/sandbox/runtime/daemon/handler/metrics.py \
  backend/src/sandbox/runtime/daemon/handler/workspace.py \
  backend/tests/unit_test/test_sandbox/test_daemon/test_daemon_transport.py \
  backend/tests/unit_test/test_sandbox/test_occ/test_auto_squash.py

uv run pytest \
  backend/tests/unit_test/test_sandbox/test_occ/test_auto_squash.py \
  backend/tests/unit_test/test_sandbox/test_daemon/test_daemon_transport.py \
  -q

uv run pytest -q \
  'backend/src/live_e2e/tests/sweevo/test_focused_scenarios.py::test_focused_reference_scenario_runs[sandbox.occ_concurrent_conflicts]' \
  backend/src/live_e2e/tests/sweevo/test_full_stack_adversarial.py \
  backend/src/live_e2e/tests/sweevo/test_correctness.py \
  backend/src/live_e2e/tests/sweevo/test_full_case_user_input.py
```

## Acceptance

The optimization is accepted only when:

- Unit coverage proves H1 coalescing still squashes once and performs the pending
  re-check when needed.
- Compatibility coverage proves removed squash-mode environment variables are
  ignored and not forwarded into the daemon.
- The requested live scenario suite passes without behavioral regressions.
