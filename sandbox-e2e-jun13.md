# Sandbox E2E Jun 13

Append-only report for Phase 05+ E2E attempts from
`docs/plans/sandbox-event-tracing-and-response-contract_SPEC.md`.

Rules for this run:

- Run target failures first after any failure.
- Do not retry a suite after an early success or repeated good result.
- For each attempt, record command, result, finding, and fix.

## Attempts

### 2026-06-13 Attempt 1 - Phase 05 e2e inventory list

- Command: `cargo test -p eos-e2e-test -- --list`
- Result: stopped; the command compiled successfully, printed the library test
  inventory, then moved to `tests/core/mod.rs` without completing the full
  inventory in the allowed observation window.
- Finding: the broad inventory gate is not a useful first retry target because
  it can stall inside a specific suite after partial success.
- Fix: do not rerun the broad list immediately; run the targeted suite
  inventory first (`core -- --list`) and only return to the broad gate after the
  stuck suite is understood.

### 2026-06-13 Attempt 2 - Targeted core inventory

- Command: `cargo test -p eos-e2e-test --test core -- --list`
- Result: passed; listed 32 tests.
- Finding: `core` inventory itself is healthy, so the stopped broad list was
  not caused by a `core` test-binary startup problem.
- Fix: continue targeted inventory runs for the remaining suite binaries before
  retrying the broad list gate.

### 2026-06-13 Attempt 3 - Parallel targeted inventory batch

- Command: parallel `cargo test -p eos-e2e-test --test {daemon,ephemeral_workspace,workspace-runtime-isolated,eos-layerstack} -- --list`
- Result: mixed; `ephemeral_workspace` passed and listed 12 tests, while
  `daemon`, `workspace-runtime-isolated`, and `eos-layerstack` were stopped
  after entering their test binaries without producing inventory output.
- Finding: parallel `--list` runs introduce enough lock/contention noise that
  stopped suites cannot be treated as product failures.
- Fix: avoid parallel inventory retries; rerun stopped suites one at a time.

### 2026-06-13 Attempt 4 - Targeted daemon inventory

- Command: `cargo test -p eos-e2e-test --test daemon -- --list`
- Result: passed; listed 12 tests.
- Finding: `daemon` inventory is healthy when run alone.
- Fix: no code fix needed; keep subsequent inventory retries serial.

### 2026-06-13 Attempt 5 - Targeted isolated inventory

- Command: `cargo test -p eos-e2e-test --test workspace-runtime-isolated -- --list`
- Result: passed; listed 21 tests.
- Finding: `workspace-runtime-isolated` inventory is healthy when run alone.
- Fix: no code fix needed; keep inventory retries serial.

### 2026-06-13 Attempt 6 - Targeted layerstack inventory

- Command: `cargo test -p eos-e2e-test --test eos-layerstack -- --list`
- Result: passed; listed 20 tests.
- Finding: `eos-layerstack` inventory is healthy when run alone.
- Fix: no code fix needed; keep inventory retries serial.

### 2026-06-13 Attempt 7 - Targeted workspace-publish-gate inventory

- Command: `cargo test -p eos-e2e-test --test workspace-publish-gate -- --list`
- Result: passed; listed 14 tests.
- Finding: `workspace-publish-gate` inventory is healthy when run alone.
- Fix: no code fix needed; keep inventory retries serial.

### 2026-06-13 Attempt 8 - Targeted command runtime inventory

- Command: `cargo test -p eos-e2e-test --test workspace-runtime-command -- --list`
- Result: passed; listed 67 tests.
- Finding: `workspace-runtime-command` inventory is healthy when run alone,
  although startup is slower than smaller suites.
- Fix: no code fix needed; keep inventory retries serial and allow a longer
  observation window for large suites.
