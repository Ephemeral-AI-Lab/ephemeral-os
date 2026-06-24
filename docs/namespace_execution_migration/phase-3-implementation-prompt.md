/goal Implement Phase 3 of the Namespace Execution Engine migration — re-express the three command APIs on `NamespaceExecutionEngine` + its registry and delete `CommandProcessStore` + satellites.

## Contract
Build to green against `docs/namespace_execution_migration/phase-3-spec.md` (authoritative; `migration-phases.md` §"Phase 3", `namespace-execution.md` design of record). Phase 2 is the consumed contract but is NOT YET implemented — spec §2 pins it and flags the Phase-2 gaps you must add. Implement as written; **read §15 (deviations needing sign-off) first**.

## Scope (spec §7)
- ENGINE `namespace-execution/` (§7.1, in scope despite the migration table): generic `ExecutionRegistry<V>` + `NamespaceExecutionEngine<V=()>` (launcher stays `Box<dyn>`); add `allocate_id`, registry value accessors (`attach`/`with_value`/`live_values`), promise `wait_timeout(Duration)->bool` + non-consuming `resolved()` (`T:Clone`), `ShellOperation::transcript_path` + file-backed PtyMaster, cancel override in `RunnerOutcome::status()`, watcher `catch_unwind`, `test-support` fake launcher + `with_launcher`.
- COMMAND `command/` (§7.2): ADD `exec.rs` (`ExecCommand: ShellOperation` + `SessionDisposition`) and `command_execution.rs` (`CommandExecution`); MOVE trimmed `CommandTerminalResult` into `contract.rs`; leave `process.rs`/`pty.rs` untouched (dead-but-pub+tested till Phase 6).
- OPERATION `operation/src/command` (§7.3): rewrite `core.rs` (hold `Arc<NamespaceExecutionEngine<CommandExecution>>` + typed `commands` view; drop store/finalizer), the 3 impls, `helpers.rs` (condvar yield), `transcript.rs`; merge the three output DTOs → `CommandOutput`; drop `CommandCompletionWaitOutcome`. DELETE `process_store.rs`,`completion.rs`,`finalize.rs`,`launch.rs`,`status_lookup.rs`.
- LEDGER `namespace_execution.rs` (§9): `Store`→`Ledger`, `impl ExecutionObserver`, `request_id`→`origin_request_id`; fix `services.rs`/`lib.rs`. WORKSPACE: `From<WorkspaceEntry> for NamespaceTarget` (§10.1). DAEMON: one read rename, keep EMITTED name (§7.6). REMOUNT minimal (§7.7): sever quiesce/coordinator from the store via the registry view — NOT the Phase-5 rewrite. RENAMES: `CommandWorkspaceOwnership`→`SessionDisposition`, `OneShotWorkspaceCleanupFailed`→`OneShotSessionCleanupFailed`.

## Load-bearing (spec §5)
1. Registry HOLDS `CommandExecution` (generic `V`); NO second per-session map.
2. Watcher: finalize → `complete(id,status,exit)` BEFORE `promise.resolve` → `on_terminal`. resolved ⟹ terminal entry exists, so yield drops `wait_for_completed_record`; `complete` never touches `V` (race-free vs late `attach`).
3. `is_finished()` decides read mode: running = file `transcript_window`+`Running`; terminal = non-consuming `resolved()` + file.
4. Yield = condvar `wait_timeout` + 50ms transcript re-check (NO 5ms poll).
5. Cancel = `killpg` from caller, NO lock held; sets `cancelled` flag → `status()` override Cancelled/130. Drop ≠ cancel.
6. One id from `allocate_id()` ("namespace_execution_N"); `CommandSessionId(id.0)` public face; `cmd_N` gone (update ~13 `"cmd_1"` test literals).
7. File-backed transcript: reader appends to `transcript_path`; reads keep `transcript_window`/1MiB window.
8. Observability serialized surface byte-for-byte unchanged (only INTERNAL field renamed); finalization trace preserved via `ExecCommand` carrying sink+ids.
9. Test seam: `CommandLaunchDriver`→engine fake launcher (test-support); rewrite `test_support.rs` + the 5 command/remount test files. No inline tests in prod.

## MUST NOT
`run_mount`/`setns_runner`/`run_child` (Phase 4, except the `From`); full remount rewrite (Phase 5); delete `command/{process,pty}.rs` or start-ack (Phase 6); fully delete publish-family types (live CLI readers, §15-D11); `execution_kind`/`backing`; dual-write shim; engine→`workspace` dep.

## Verify (full block §13)
```
cargo fmt --check && cargo test -p sandbox-runtime-namespace-execution -p sandbox-runtime-command && cargo test -p sandbox-runtime --tests && cargo check -p sandbox-daemon
cargo clippy --all-targets --no-deps -- -D warnings   # the 4 touched crates
rg -n "CommandProcessStore|CommandLaunchDriver" crates/sandbox-runtime/operation/src/command || echo "gone ✓"
```
Done when spec §16 DoD + §14 matrix hold + `git diff --check`. Report LOC via `git diff --numstat`.
