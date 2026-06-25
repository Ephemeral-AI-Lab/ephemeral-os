/goal Implement docs/command-rework/spec.md (rev 2, "Command Rework Spec") end-to-end. Treat the spec as source of truth; READ it fully first, plus README.md (boundary law) and CLAUDE.md (rules).

EXECUTE §8 build-safe migration order — 7 steps, IN ORDER. After EACH step run `cargo build` && `cargo test` (workspace) and get green before the next; never batch steps.
1) Engine seam (nsx): add `on_complete` to `run_shell_interactive`, composed into the finalize closure it already passes to spawn_watcher (`let result = op.finalize(outcome); on_complete(&result); result`) — before resolve, inside the existing catch_unwind; spawn_watcher/run_mount untouched; existing callers pass a no-op.
2) ID collapse: delete CommandSessionId + execution_id/command_session_id shims; re-key all DTOs/errors/observability/CLI to NamespaceExecutionId; de-Option CommandFinalizationTraceMetadata.workspace_session_id.
3) Wrapper→value: delete CommandExecution; add CommandExecValue (next_snapshot_offset: Cell<u64>, transcript_path non-Option, both clocks kept); Engine<CommandExecValue>.
4) Finalization: pure ExecCommand; new finalize.rs = CommandFinalization{KeepSession,DestroyOneShot} + apply() (policy only) + build_on_complete assembler + emit_finalization_trace + NamespaceExecutionRecord::completed; exec_command passes on_complete; fail_command_start keeps the pre-spawn path.
5) Ledger fusion: gut ledger → completed-buffer + record_completed; delete NamespaceExecutionLifecycle enum + dead fields (active & completed lifecycle_state, active started_at_unix_ms); engine → NoopObserver; add CommandOperationService::active_namespace_executions() (live_values + deterministic id sort); services.rs derives active from it and drops its duplicate ledger Arc; apply daemon companion edits (§10).
6) Read infallible: read_command_lines -> CommandOutput; best-effort transcript_window; delete CommandTranscriptUnavailable + validate_read_limit; clamp(1,1000).
7) Cleanup: merge/rename files (contract.rs, service/dto.rs, service/yield.rs, service/render.rs, flatten impls/); prune the 6 dead error variants; evict test-in-src (fixtures → tests/support/); collapse ctor to 1 prod + with_engine.

HARD INVARIANTS — do NOT violate:
- KEEP the workspace-destroy admission lock; exec_command holds it across try_reserve→attach (§2c/§9). The destroy-while-active test stays green.
- Preserve both temporal contracts (on_complete-before-resolve; admission across reserve→attach).
- Preserve the public surface in §9 (exec_command(_with_origin_request_id), write_command_stdin, read_command_lines, with_workspace_destroy_admission, config/new/namespace_execution_store, the DTOs, the ledger projection, SandboxRuntimeOperations).

DO NOT (rejected/deferred, §11–§13): move ledger ownership to SandboxRuntimeOperations or drop namespace_execution_store; replace the buffer with straight-through sink writes; add a CompletionSink trait (use the generic closure); add a CommandSessionId alias; drop started_at: Instant; delete the ExecutionObserver trait or the nsx required_transcript_window fn. The ONLY nsx change is the engine on_complete seam.

RULES: no inline comments in src/; no test code in src/ (fixtures in tests/); SOLID/SRP; respect README crate boundaries (namespace-execution stays workspace-agnostic); additive, localized edits.

DONE = all 7 steps landed AND, with `export PATH="$PWD/bin:$PATH"`, these pass: `cargo build`, `cargo test`, `cargo test -p sandbox-daemon`, `cargo clippy --all-targets`, `cargo fmt --check`. Resolve the §10 test fixups as you go. Finish with a per-step summary + measured LOC delta vs §7.
