# Sandbox Command Session Default Iteration Report

**Date:** 2026-06-02.
**Status:** Implemented and smoke-verified.
**Plan:** `docs/plans/sandbox-command-session-default-implementation-plan.md`.

## Summary

- `exec_command` now has no public mode selector. It always uses the managed
  command-session path and returns `command_session_id` when the execution scope
  is still alive after the yield window.
- `write_stdin` is the only model-facing session-control tool. Empty `chars`
  polls progress; non-empty `chars` writes literal input and returns the updated
  transcript/status.
- The previous finite-vs-session split was removed from the Rust runner and
  daemon benchmark gates. The duplicate finite category is gone; the live gate
  now measures the default command-session path, progress polling, input echo,
  cancellation cleanup, and the load matrix.
- Legacy model-facing control tools and public names were removed from active
  source, tests, scripts, agent profiles, architecture pages, and the current
  plan. Generated cache folders carrying retired names were deleted.
- Terminal allocation is behind `eos-terminal-pair`, a tiny safe wrapper crate,
  so `eos-daemon` imports a neutral command-session API while retaining
  `#![forbid(unsafe_code)]`.

## Verification

- `uv run ruff check $(git diff --name-only -- '*.py')`: passed.
- `uv run pytest backend/tests/unit_test/test_test_runner/test_probe_bridge.py backend/tests/unit_test/test_sandbox/test_api/test_command.py backend/tests/unit_test/test_tools/test_sandbox_toolkit/test_toolkit.py backend/src/test_runner/tests/mock/contracts/test_scenario_event_source_spike.py`: 14 passed, 2 skipped.
- Broader changed-unit subset covering engine background state, sandbox transport,
  routing invariants, lifecycle hooks, and command-result rendering: 93 passed.
- `cargo fmt --all --check`: passed.
- `cargo test -p eos-terminal-pair`: passed, 0 tests.
- `cargo test -p eos-runner`: passed, 8 tests.
- `cargo test -p eos-daemon command`: passed, 10 tests.
- `cargo test -p eos-daemon isolated -- --test-threads=1`: passed, 6 tests across lib and phase2 read-path filters.
- Active-surface explicit legacy-name scan across `backend/src`, `backend/tests`,
  `backend/scripts`, `sandbox/crates`, `docs/architecture`, and this plan:
  passed after filtering ordinary words such as `empty`, `pretty`, and
  `EventType`.

## Live Evidence

- Rebuilt and uploaded `eosd` for amd64 with `rust-lld`.
- Upload report: `bench/local-eosd-amd64-upload-command-session.json`.
- Uploaded artifact SHA: `321efbdb58b19269e8334910cdbf22c4c6da7b94020e091de03d9bcede90fcfe`.
- Upload gate: passed; upload time `39.664 ms`.
- Live smoke report: `bench/phase3t-command-session-smoke.json`.
- Smoke run id: `local-f866be4075dd`.
- Smoke gate: passed; correctness gate passed; load gate passed.
- Single-sample operation p95s:
  - default command: `52.053 ms`.
  - progress poll: `2.235 ms`.
  - input echo: `53.743 ms`.
  - cancellation call: `55.011 ms`.
  - cancellation cleanup: `361.573 ms`.

## Notes

- Historical planning records still preserve older terminology as past evidence.
  Runtime code, current model-facing tools, active tests, active scripts,
  architecture pages, and the current plan use command-session terminology.
