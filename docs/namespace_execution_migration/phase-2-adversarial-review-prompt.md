/goal Adversarially review the **completeness and correctness** of Phase 2 of the Namespace Execution Engine migration. Assume the implementation is wrong until proven right; your job is to *refute* it, not confirm it.

## Contract
The authoritative target is `docs/namespace_execution_migration/phase-2-spec.md` (esp. §7 Acceptance Criteria and §2 Resolved Design Decisions). The phase boundary is `migration-phases.md` §"Phase 2"; `namespace-execution.md` shows the FINAL (Phase 3–6) shapes — anything matching those that Phase 2 was told NOT to build is a defect, not a feature. The live implementation is the `sandbox-runtime-namespace-execution` crate plus the one re-export shim in `operation/src/namespace_execution.rs`. Read the code as it is on disk; do not trust commit messages, comments, or this prompt's summaries over the source.

This is a **review-only** task: produce findings, do not fix. (A separate cleanup pass handles edits.) The only writes you make are the review report.

## What Phase 2 claims to deliver (verify each claim independently)
- `NamespaceExecutionEngine` with two entry points: `run_shell_interactive` → `InteractiveExecution<T>` (PTY) and `run_mount(mode_flag, target, id, parse)` → `ExecutionHandle<T>` (pipe).
- One Template-Method dispatch: `try_reserve` → build request (`namespace_execution_id` IS the runner `request_id`) → spawn → `attach` pgid → `on_running` → watcher{ `wait_completion` → `RunnerOutcome::new` → `finalize`(shell)/`parse`(mount) → `registry.complete` **before** `promise.resolve` → `on_terminal` }. Exactly 2 threads per interactive exec (PTY reader + watcher), 1 for mount; **zero poll loops**.
- Launcher Bridge seam: `pub(crate) trait NsRunnerLauncher` (`spawn_pty`/`spawn_piped`) + `pub(crate) trait RunnerChild` (`wait_completion`), a real `ForkRunnerLauncher`/`ForkRunnerChild` (compile-coverage on darwin), with `--start-ack-fd` + the ack byte KEPT (Phase 6 removes it).
- `PtyMaster` over a real `openpt` pair (in-memory transcript), `ExecutionRegistry` (live + completed + admission), `CompletionPromise` shared as `Arc`, `RunnerOutcome::{new,status,payload}`, `ExecutionObserver::on_terminal`, and the relocation of `NamespaceExecutionTerminalStatus` into `src/status.rs` (re-exported by `operation`).

## Sanctioned deviations from the spec (judge whether each is SOUND, not whether it differs)
The implementer settled two live conflicts with the user; do not report these as "spec violations" unless you can show they break behavior, the phase contract, or a gate:
1. **Tests live in `tests/`, not inline.** The repo forbids inline `#[cfg(test)]`/test scaffolding in `src/` (xtask `check-inline-tests`; CLAUDE.md "No test code in `src/`"). Fakes live in `tests/support/mod.rs`; the `pub(crate)` seam is surfaced to tests through a `#[cfg(feature = "test-support")] pub mod test_support` re-export facade. Verify the facade contains **no test logic** (re-exports only) and that `src/` is genuinely production-only.
2. **Public-API widening to keep `-D warnings` clean.** `ExecutionRegistry`, `CompletedExecution` are root-exported and `RunnerOutcome::new` is `pub` (so carry-only fields are API-exempt from `dead_code` and `tests/support` can build outcomes). Confirm this does not leak Phase-3 responsibilities or violate the boundary law, and that nothing else widened silently.
3. `spawn_piped(mode_flag, request)` carries the mode flag (spec §2.1's trait box omits it; §2.9 requires it to reach the runner). Confirm shell passes no mode flag (Run default) and mount passes `--mount-overlay`/`--remount-overlay`.
4. Transcript reader drains **raw** bytes (no timestamp prefix) because `time` is not an approved dependency (§2.10). Confirm this is the only consequence and the in-memory sink is correct.
5. `CompletionPromise::wait_timeout` is gated behind `test-support` (prod-unused in Phase 2). Confirm the bool form matches §2.5 and the gate causes no breakage.

## Attack surface — try hardest to break these
- **The `complete`-before-`resolve` invariant.** Prove or disprove that *promise-resolved ⟹ the completed entry already exists* on every path (shell Ok/Err, mount Ok/Err, `wait_completion` Err, cancel). Find any ordering where `wait()` can return before `registry.complete` ran — the admission readmission test depends on this.
- **Concurrency / races.** Watcher thread vs. caller: `is_finished`/`wait` vs `resolve`; `on_terminal` firing *after* `resolve` (does any assertion or caller observe terminal state too early?). Admission TOCTOU under concurrent `run_*`. Lock ordering across registry/promise/observer — can two locks be held in conflicting orders? Is the registry's single `Mutex` ever held across a blocking call?
- **Cancel correctness.** Is cancel truly independent of the watcher (no watcher mediation)? Does `InteractiveExecution::cancel` → `PtyMaster::cancel` → boxed action actually unblock a watcher blocked in `wait_completion`? In the fork backing, does `killpg` reach the child's own process group? Is there a cancel-after-completion or double-cancel hazard?
- **Resource/FD discipline in the launcher.** Are request/result/start-ack pipe ends closed on the correct side, with correct CLOEXEC flags, matching the daemon child's expectations (`daemon/src/runner.rs`)? On `release_start_ack` write failure or spawn failure, is the child orphaned or the slot leaked (`registry.abort`)? Does `wait_completion` read the result fd correctly after `wait()` (EOF semantics), and is `synthesize_result` correct for code/signal/absent-result cases? Any fd left non-CLOEXEC that leaks into the child?
- **`RunnerOutcome::status()` mapping.** Exhaust the payload space: each of `ok/error/timed_out/cancelled`, missing `status`, non-string `status`, non-object payload, unknown string → all must map per §2.6 (default `Error`), with no panic.
- **PTY substrate on darwin.** `open_pty_pair` `cfg` branches, non-blocking master, reader EOF/hangup exit, `write_stdin` backpressure deadline, `read_output_since`/`output_len` bounds (offset past end, multibyte UTF-8 split). Does the reader thread leak or busy-spin?
- **Boundary law & phase leakage.** Zero `workspace` dependency. No `ExecCommand`/`CommandExecution`/`From<WorkspaceEntry>`/`NamespaceExecutionLedger`/`origin_request_id`/`run_child`. No `execution_kind`/`backing` axis. Start-ack still present. The relocated `NamespaceExecutionTerminalStatus` variants/`as_str()` strings are byte-for-byte unchanged so daemon/observability rows are unaffected.
- **Test quality.** Are the `tests/` assertions actually load-bearing, or do they pass vacuously (e.g., `await_terminal` masking a missing `on_terminal`, a race that only passes on fast hosts, a fake that always returns Ok hiding the Err path)? Is every §7 acceptance criterion covered by a test that would FAIL if the behavior regressed? Name any criterion with weak or missing coverage.

## Method
Work adversarially and verify findings before reporting. For each candidate defect: state the claim, give file:line evidence, construct a concrete trigger (input/interleaving), and predict the observable failure. Then try to REFUTE your own finding (is it actually reachable in Phase 2? is it guarded elsewhere? is it an intentional sanctioned deviation?). Only report findings that survive. Prefer a small number of high-confidence, reproducible findings over a long speculative list. Distinguish: **Correctness bug** (wrong behavior) vs **Completeness gap** (untested/unimplemented criterion) vs **Boundary/phase leak** vs **Risk/smell** (works today, fragile).

## Verify (use these to ground findings; do not change code)
```
export PATH="$PWD/bin:$PATH"
cargo test  -p sandbox-runtime-namespace-execution
cargo clippy -p sandbox-runtime-namespace-execution --all-targets --no-deps -- -D warnings
cargo clippy -p sandbox-runtime-namespace-execution --no-deps -- -D warnings   # pure lib (no test-support)
cargo test  -p sandbox-runtime --tests        # relocation regression
cargo check -p sandbox-daemon
cargo run -q -p xtask -- check-inline-tests && cargo run -q -p xtask -- check-cfg
rg -n "ExecCommand|CommandExecution|run_child|From<WorkspaceEntry>|NamespaceExecutionLedger|origin_request_id" crates/sandbox-runtime/namespace-execution/src
rg -n "start[-_]ack" crates/sandbox-runtime/namespace-execution/src
```
If you suspect a race, try to surface it (`cargo test ... -- --test-threads=1` vs default; loop a flaky test; reason about the interleaving) rather than asserting it abstractly.

## Output (the only artifact you write)
A findings report (e.g. `docs/namespace_execution_migration/phase-2-review-findings.md`) with: an executive verdict (is Phase 2 complete + correct per §7? yes/no/qualified), then per finding — title, severity (Critical/High/Medium/Low), category, evidence (`file:line`), concrete trigger, why it matters for the phase contract, and a recommended fix direction (not a patch). End with a §7 acceptance-criterion-by-criterion coverage table (Covered / Weak / Missing, with the test that proves each). Call out explicitly anything you could NOT verify and why.

Done when every §7 criterion has a verdict backed by evidence, the attack-surface list above is each either cleared or has a surviving finding, and the report distinguishes real defects from sanctioned deviations.
