/goal Implement Phase 2 of the Namespace Execution Engine migration — launcher + engine dispatch + watcher — making the engine functional end to end against a fake launcher, fully unit-tested before any real caller depends on it.

## Contract
Build to green against `docs/namespace_execution_migration/phase-2-spec.md` (authoritative). Phase contract: `migration-phases.md` §"Phase 2". `namespace-execution.md` shows FINAL (Phase 3-6) shapes — do not pull them forward. Build ON the live Phase 1 skeleton in `crates/sandbox-runtime/namespace-execution/`; live code wins over the design doc on conflict. Spec-only decisions are already settled — implement as written, don't re-derive.

## Scope — touch ONLY the engine crate + one re-export shim
Add `engine.rs`, `launcher.rs`, `pty.rs`, `status.rs`. Fill in `registry.rs`, `execution.rs`, `shell.rs`, `observer.rs`, `promise.rs`, `lib.rs`, `Cargo.toml`. Edit exactly ONE operation file: `operation/src/namespace_execution.rs` (terminal-status relocation).

## Load-bearing (compiler-verified in the spec)
1. Fake seam: `pub(crate) trait NsRunnerLauncher` (spawn_pty/spawn_piped) + `pub(crate) trait RunnerChild` (wait_completion). Engine is NON-generic, holds `Box<dyn NsRunnerLauncher>`. The generic `<L = ForkRunnerLauncher>` form FAILS `-D warnings` (private_interfaces/private_bounds) — never use it.
2. Relocate `NamespaceExecutionTerminalStatus` → engine `src/status.rs`; `operation` re-exports it (mirrors the Phase 1 id move). One operation file changes; daemon/tests resolve via the re-export.

## Build
- Dispatch (both entry points): reserve → build request (namespace_execution_id IS request_id) → spawn → attach pgid → on_running → watcher{ wait_completion → finalize(shell)/parse(mount) → registry.complete BEFORE promise.resolve → on_terminal }. 2 threads/exec (PTY reader + watcher), 0 poll loops.
- `run_shell_interactive` → spawn_pty → `InteractiveExecution<T>`; `run_mount(flag, target, id, parse)` → spawn_piped → `ExecutionHandle<T>`.
- Launcher: real `ForkRunnerLauncher` + `ForkRunnerChild` (`child.wait()` + inline result-fd read; NO reader thread, NO poll). KEEP `--start-ack-fd` + the ack byte (Phase 6 removes it).
- `PtyMaster`: real openpt pair, in-memory transcript buffer; write_stdin/read_output_since/output_len/cancel. Cancel = boxed action (killpg for fork; trips the fake signal). Promise shared as `Arc`. `RunnerOutcome::{new,status,payload}` (status = pure payload parse).
- Registry: live+completed maps, `try_reserve`/`attach`/`abort`/`complete`/lookups; generic completed (`{status, exit_code}`, NO command types).
- Deps: add `serde_json`, `rustix{pty,event,pipe}`, `nix{signal}` only (NOT serde/libc/thiserror). No unsafe.
- Tests: fake launcher/child/observer in `engine.rs` `#[cfg(test)]`; openpt-loopback test in `pty.rs`. Fake path is the authoritative signal (darwin host; fork path is compile-coverage).

## MUST NOT (Phase 3-6)
No ExecCommand/CommandExecution/command-service migration; no Store→Ledger rename / observer impl / origin_request_id; no From<WorkspaceEntry> / setns_runner rewrite / run_child deletion; no command/workspace/daemon source edits; no start-ack removal; no execution_kind/backing axis; no `workspace` dep; no `wait_timeout Option<&T>` peek; no cancel-override in status().

## Verify (in order)
```
cargo fmt --check
cargo test  -p sandbox-runtime-namespace-execution
cargo clippy -p sandbox-runtime-namespace-execution --all-targets --no-deps -- -D warnings
cargo test  -p sandbox-runtime --tests
cargo check -p sandbox-daemon
rg -n "ExecCommand|CommandExecution|run_child|From<WorkspaceEntry>|NamespaceExecutionLedger|origin_request_id" crates/sandbox-runtime/namespace-execution/src || echo "no leak ✓"
rg -n "start[-_]ack" crates/sandbox-runtime/namespace-execution/src
git diff --check && git diff --numstat
```
Done when every spec §7 Acceptance Criterion passes and the leak/start-ack greps hold. Report actual LOC via `git diff --numstat`.
