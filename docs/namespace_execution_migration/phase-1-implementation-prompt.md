/goal Implement Phase 1 of the Namespace Execution Engine migration: stand up crate `sandbox-runtime-namespace-execution` and relocate `NamespaceExecutionId`. Types/skeleton only — nothing calls the engine; behavior unchanged.

SOURCE OF TRUTH: `docs/namespace_execution_migration/phase-1-spec.md` (build to §5/§7). Phase contract: `migration-phases.md` §"Phase 1". Live code wins over prose.

DO (exact):
1. Root `Cargo.toml`: add `"crates/sandbox-runtime/namespace-execution"` to `members`; add `sandbox-runtime-namespace-execution = { path = "crates/sandbox-runtime/namespace-execution" }` to `[workspace.dependencies]`.
2. New crate `crates/sandbox-runtime/namespace-execution/`:
   - `Cargo.toml`: workspace-inherited meta, `[lints] workspace = true`, ONE dep `sandbox-runtime-namespace-process.workspace = true`. No serde/serde_json/rustix/nix/libc/thiserror.
   - `src/`: `lib id error target promise execution shell observer registry`.rs per spec §5.3–5.11.
3. `operation/Cargo.toml`: add `sandbox-runtime-namespace-execution.workspace = true`.
4. `operation/src/namespace_execution.rs`: DELETE the struct def at lines 13–14; ADD `pub use sandbox_runtime_namespace_execution::NamespaceExecutionId;`. No other operation file changes.

MOVE: `NamespaceExecutionId(pub String)` into `id.rs` with the SAME 7 derives (`Debug,Clone,PartialEq,Eq,Hash,PartialOrd,Ord`) and `pub` field. `allocate_*` / `format!("namespace_execution_{n}")` STAYS on `NamespaceExecutionStore`.

SKELETON (compile-now only; defer the rest to Phase 2):
- `error.rs`: hand-rolled `NamespaceExecutionError` (`Spawn`/`Finalize`/`Admission`) + `Display`+`Error`.
- `target.rs`: `NamespaceTarget` 5 fields; `ns_fds: sandbox_runtime_namespace_process::runner::protocol::NsFds`. No workspace dep, no `From<WorkspaceEntry>`.
- `promise.rs` (pub(crate)): `CompletionPromise<T>` = `Mutex`+`Condvar`; `new/resolve/is_resolved/wait/wait_timeout(Duration)->bool`.
- `execution.rs`: `ExecutionHandle<T>{id,promise}` (`new/id/is_finished/wait`); `InteractiveExecution<T>{exec}` (`new/execution/id/is_finished/wait`). NO PTY, NO `Execution<T>` trait, NO `Deref`.
- `shell.rs`: `ShellOperation` trait + `RunnerOutcome(RunResult)` with `exit_code()->i64` only.
- `observer.rs`: `ExecutionObserver: Send+Sync` with `on_running` only.
- `registry.rs` (pub(crate)): `ExecutionRegistry{max_active}` placeholder (`new`+`max_active()`). NO maps/try_reserve.
- `lib.rs`: `mod` all 8; `pub use` ONLY id/error/target/handle+interactive/shell-trait+outcome/observer. promise+registry stay pub(crate). DO NOT `#![forbid(unsafe_code)]`.

MUST NOT (Phase 2+): NamespaceExecutionEngine, NsRunnerLauncher, watcher, PtyMaster, RunnerChild, run_shell_interactive/run_mount, spawn_pty/piped, admission, registry lookup, Store→Ledger rename, origin_request_id, on_terminal, Observer impl, observability axis change.

TESTS (inline `#[cfg(test)]`, std-only): id newtype+Hash; promise resolve-then-wait & wait_timeout-pending; handle composition+forwarding; registry capacity. No new operation test — existing `operation/tests/{namespace_execution,exec_command}.rs` prove the move via the re-export.

VERIFY (in order):
```
cargo fmt --check
cargo check  -p sandbox-runtime-namespace-execution
cargo test   -p sandbox-runtime-namespace-execution
cargo check  -p sandbox-daemon
cargo test   -p sandbox-runtime --tests
cargo test
cargo clippy -p sandbox-runtime-namespace-execution --all-targets --no-deps -- -D warnings
rg -n "pub use .*NamespaceExecutionId" crates/sandbox-runtime/operation/src
rg -n "NamespaceExecutionEngine|NsRunnerLauncher|PtyMaster|RunnerChild|spawn_p" crates/sandbox-runtime/namespace-execution/src || echo "no Phase 2 ✓"
git diff --check && git diff --numstat
```
DONE: all green (any full `cargo test` blocker proven pre-existing on `main`); id defined once; daemon+operation use the re-export; clippy clean; report LOC via numstat.
