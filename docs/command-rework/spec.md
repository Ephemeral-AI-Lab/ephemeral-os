# Command Rework Spec

**Status:** proposed (rev 2 — adversarial review incorporated) · **Scope:**
`sandbox-runtime/operation` (`command/`, `namespace_execution.rs`, `services.rs`,
`observability.rs`) + one generic seam in `sandbox-runtime/namespace-execution`
(`engine.rs`) + mechanical companion edits in `sandbox-daemon`
(`observability/namespace_execution.rs`, `service.rs`) for the slimmed DTOs.

The `name_space_runner` migration moved command execution onto the namespace-execution
engine but left `command/` bloated (~1258 prod LOC) with redundant id translation, a
forwarding wrapper, dual lifecycle tracking, and workspace finalization smuggled through
the runner. This spec finalizes the cleanup **and** relocates finalization to where it
belongs.

> **Rev 2** folds in the §13 adversarial review. The load-bearing change: the
> workspace-destroy admission lock is **retained** (rev 1 deleted it, which re-opened a
> destroy-while-active race — see §9 / §13). Other adopted findings: `on_complete` is
> composed into the engine's existing finalize closure (no `spawn_watcher` param);
> active observability is a command-owned method; several now-dead fields are dropped.

---

## 1. Goals

1. Make the three components mutually well-bounded:
   - **workspace** is the *substrate* shell_exec runs on (produces a `NamespaceTarget`).
   - **namespace_execution** is the *generic shell_exec runner* (consumes a `NamespaceTarget`, runs the shell, tracks liveness, produces a pure terminal result).
   - **command** is the *orchestrator + finalization owner*: resolve/create a workspace, run a command on it, and decide destroy / keep / (future) publish.
2. Migrate finalization **out of the runner** into a command-owned `on_complete` closure.
3. Aggressively remove the migration's redundancy (isomorphic pairs collapse to one side).
4. Make `read_command_lines` a pure, infallible transcript reader.
5. Keep the **two** deliberate temporal contracts explicit and minimal:
   `on_complete`-before-`resolve` (engine side) and destroy-admission across
   reserve→attach (command side). Neither is silent.

---

## 2. Architecture

```
┌─ COMMAND (operation :: command/) — orchestrator + finalization owner ─────────┐
│  CommandOperationService::{exec_command, write_command_stdin,                  │
│                            read_command_lines, with_workspace_destroy_admission}│
│  ExecCommand (pure ShellOperation) · CommandFinalization policy ·              │
│  on_complete closure (destroy/keep + trace + projection record)                │
└───────┬───────────────────────────────────────────────────┬──────────────────┘
        │ resolve/create/destroy_session                     │ run_shell_interactive(
        ▼                                                     ▼   op, target, id, on_complete)
┌──────────────────────────────┐  entry()→WorkspaceEntry  ┌────────────────────────────────────┐
│ WORKSPACE — substrate        │ ──From──▶ NamespaceTarget │ NAMESPACE_EXECUTION — shell runner  │
│  resolve/create/destroy/     │ ────────────────────────▶ │  Engine<CommandExecValue>           │
│  (publish); passive          │                           │  Registry (single liveness auth)    │
│  knows nothing of commands   │                           │  ShellOperation::finalize = PURE    │
└──────────────────────────────┘                           │  generic; knows nothing of workspace│
                                                            └────────────────────────────────────┘
```

**Dependency DAG (compile-time):** `command → workspace → namespace_execution`, and
`command → namespace_execution`. `namespace_execution` is a leaf. The finalization
callback is inversion of control (a generic closure), not a dependency edge.

**Coupling:** data coupling at both seams (`WorkspaceEntry → NamespaceTarget`,
`on_complete(&Result)`); the engine holds `CommandExecValue` opaquely. Three residual
couplings are deliberate and named:
- **(a)** command depends on concrete collaborator types (single-impl orchestrator);
- **(b)** the engine temporal contract — `on_complete` runs before `promise.resolve` —
  irreducible, made explicit in the engine API;
- **(c)** the command temporal contract — `exec_command` holds the workspace-destroy
  admission across `try_reserve`→`attach` so a concurrent `destroy_workspace_session`
  cannot observe an in-flight exec as absent during the value-less reserve window
  (registry `live_values` skips reserved-but-unattached entries). This is the
  command-local analogue of (b); see §9.

---

## 3. Design

Engine watcher ordering (`engine.rs`): `175` `op.finalize` · `175b` `on_complete` ·
`185` `registry.complete` · `186` `promise.resolve` (`is_finished()` flips) ·
`187` `observer.on_terminal`.

### A. Finalization migration (namespace_execution → command) — "engine completion hook"

- **A1.** `ShellOperation::finalize` stays a **pure** projection (`RunnerOutcome → Output`); carries nothing workspace-shaped.
- **A2.** `engine.run_shell_interactive` gains **one generic param**
  `on_complete: FnOnce(&Result<Output, NamespaceExecutionError>) + Send + 'static`.
  It is **composed into the existing finalize closure** that `run_shell_interactive`
  already hands to `spawn_watcher` (today `move |outcome| op.finalize(outcome)`), so the
  watcher runs `let result = op.finalize(outcome); on_complete(&result); result` — i.e.
  `on_complete` fires **after** `op.finalize` (`175b`) and **before**
  `registry.complete`/`promise.resolve` (`185`/`186`), inside the **same**
  `catch_unwind` guard (`finalize_outcome`). `spawn_watcher` and `run_mount` are
  **untouched** (no no-op param leaks onto the mount path). This is the **only**
  namespace-execution change (~+8 LOC). The engine never names workspace / destroy /
  publish. *(A panic in `on_complete` is caught like a finalize panic → `Finalize` error;
  `on_complete` is contractually non-panicking and routes its own errors to
  observability — see A4.)*
- **A3.** `ExecCommand` becomes **pure**: `{ command, timeout_seconds, transcript_path, started_at }`.
  Delete `workspace`, `session_disposition`, `finalization_trace`, the `SessionDisposition`
  enum, the `CommandFinalizationTrace` struct, and `finalize_session` / `apply_disposition` / `finalize_error`.
- **A4.** New `command/finalize.rs`: `enum CommandFinalization { KeepSession, DestroyOneShot(WorkspaceSessionHandler) }`
  + `build_on_complete(...)`. The enum is **closed at these two variants for this rework**
  (publish is out of scope — see §11; the runner seam is open for it, the command side is
  not yet). `build_on_complete` captures the policy, `Arc<WorkspaceSessionService>`,
  `Option<AsyncTraceSink>`, the ledger projection buffer, and the projection metadata, and
  is a **thin assembler** over three single-job units (SRP — do not fuse):
  - `CommandFinalization::apply(self, &WorkspaceSessionService) -> Result<(), WorkspaceSessionError>` — policy only (the `match` over variants; the **one** place a future variant edits);
  - `emit_finalization_trace(sink, metadata, finalizer_error)` — observability only (the two-span tree `complete_terminal_command_with_services → apply_workspace_completion_policy` + `CommandFinalizationTraceMetadata`; no trace when `origin_request_id`/sink absent);
  - `NamespaceExecutionRecord::completed(meta, &result)` — record construction only, handed to `ledger.record_completed(...)`.
  The assembler calls `apply`, maps its error into `finalizer_error`, emits the trace, then
  pushes the record. **Teardown error → observability, never the command result.**
- **A5.** **Single trigger = child completion** at `175b` (before `resolve`). The foreground
  waiter NEVER finalizes; it observes `is_finished()` (post-`186`) with teardown already
  done. Foreground (completes within `yield_time_ms`) and background (still running at
  `yield_time_ms`) share this one path; they differ only in whether a caller is parked in
  `wait_for_command_yield`.
- **A6.** Pre-spawn failure (`run_shell_interactive` Err, or entry/transcript prep Err): the
  closure never runs; command pushes a failed record directly (`record_completed`) +
  destroys the one-shot, preserving `error_kind = command_start_failed`, the sanitized
  message, and `OneShotSessionCleanupFailed`.

### B. Ledger fusion — `NamespaceExecutionLedger` → pure completed-projection buffer

- **Keep:** `pending_projection` / `recent_projected` / `partial_errors`, `drain_completed`,
  `ack_completed`, `drain_partial_errors`, error bounding.
- **Rename ingestion:** `complete_namespace_execution` → `record_completed(record)` — it now
  *ingests* a fully-built `NamespaceExecutionRecord` (constructed by `NamespaceExecutionRecord::completed`)
  rather than building one from a `CompleteNamespaceExecution` input.
- **Delete:** `active: HashMap`, `NamespaceExecutionLifecycle::Starting`,
  `mark_namespace_execution_running`, `snapshot_active_namespace_executions`, the
  `ExecutionObserver` impl, `begin_namespace_execution` (+ the `BeginNamespaceExecution` and
  `CompleteNamespaceExecution` input structs), `find_terminal_record`,
  `allocate_namespace_execution_id` + `next_id`, `set_force_mutation_errors_for_test` +
  force-mutation machinery.
- **Active observability moves to the engine registry, behind a command-owned method:**
  `CommandOperationService::active_namespace_executions() -> Vec<RuntimeNamespaceExecutionSnapshot>`
  derives active executions from `engine.live_values` over `CommandExecValue`
  (`workspace_session_id`, `operation_name`) **and re-applies the deterministic
  `sort_by namespace_execution_id`** the deleted `snapshot_active` guaranteed (registry
  `live_values` iterates a `HashMap` — unsorted; the daemon's stable trace ids depend on
  the sort). `services.rs::observability_snapshot` calls this one method (it does **not**
  reach into the command-owned engine itself — SRP/boundary: the type that owns
  `CommandExecValue` owns its projection).
- **Single clock source** = command's `started_at_unix_ms` (stamped once at `exec_command`,
  reused for the completed record). *(The active snapshot exposes **no** start timestamp —
  the daemon stamps `sampled_at_unix_ms` at snapshot time — so "single clock" is the
  invariant for the **completed** record; see §9.)*
- **Drop now-dead fields** (the active map is gone, so there is one record source):
  - `RuntimeNamespaceExecutionSnapshot` slims to `{ namespace_execution_id, workspace_session_id, operation_name }`. `lifecycle_state` was a constant (a live entry **is** Running; daemon emits the literal `"running"`); `started_at_unix_ms` is never read by any daemon consumer.
  - `NamespaceExecutionRecord` drops `lifecycle_state` (every record is Terminal; `trace_record` never reads it).
  - With both consumers gone, **delete the `NamespaceExecutionLifecycle` enum** entirely.
  - Companion daemon edits (mechanical) tracked in §10.
- Engine observer becomes `NoopObserver` (resolves the dropped-`on_running` concern: there
  is no Starting state; a live registry entry IS Running). *(Deleting the
  `ExecutionObserver` trait outright is a deferred follow-up — §12 — because it widens
  scope into `namespace-execution` + `workspace`.)*

### C. ID unification (isomorphic collapse)

Delete `CommandSessionId`; `NamespaceExecutionId` flows through every DTO field, error
variant, `CommandFinalizationTraceMetadata`, and the admission callback. Delete the
`execution_id()` / `command_session_id()` shims. The wire field name `command_session_id`
is preserved (carries `NamespaceExecutionId.0`); the daemon reads only `.0`/`as_str`, so it
is unaffected. *(No `pub type CommandSessionId = NamespaceExecutionId;` alias — that would
keep the dual vocabulary the collapse removes; rejected, §13.)*

### D. Wrapper deletion (isomorphic collapse)

Delete `CommandExecution` (`execution.rs`). Replace with `CommandExecValue` (`exec_value.rs`)
= `{ exec: InteractiveExecution<CommandTerminalResult>, transcript_path: PathBuf,
workspace_session_id, started_at: Instant, started_at_unix_ms: i64,
operation_name: &'static str, origin_request_id: Option<String>, next_snapshot_offset: Cell<u64> }`.
The pure engine forwards (`is_finished`/`output_len`/`completion`/`write_stdin`/`cancel`/
`terminal_result`) are NOT re-created; callers reach `v.exec.*` through `engine.with_value`.
`CommandExecValue` **retains** the methods that are not on `InteractiveExecution`:
`transcript_window`, `elapsed_seconds`, and the snapshot-offset accessors.
`next_snapshot_offset` is a plain `Cell<u64>` (the registry mutex already serializes every
`with_value` access — the former `AtomicU64` + `Acquire`/`Release` advertised a cross-thread
contract that does not exist; `Cell<u64>` is `Send`, which is all `Engine<V>` requires).
`transcript_path` is no longer `Option`. Engine generic becomes `Engine<CommandExecValue>`.
Both clocks stay: `started_at: Instant` (monotonic, for `elapsed_seconds`/wall time) and
`started_at_unix_ms: i64` (wall, for the completed record). *(Realistic size ~55 LOC, not
~40 — the retained methods are real; see §7.)*

### E. `read_command_lines` — pure, infallible

Signature `-> CommandOutput` (no `Result`). Reads the transcript by the deterministic path
`scratch_root/<id>/transcript.log` via the **best-effort** `transcript_window`
(`required_transcript_window` is no longer called; empty window if absent). Status best-effort
from the handle; unknown id → empty terminal output. `limit.clamp(1, 1000)`. Delete
`validate_read_limit` and `CommandTranscriptUnavailable`; read references no error variant.
*(The nsx-level `required_transcript_window` free function loses its only operation-layer
caller here; removing it from `namespace-execution` is a deferred follow-up — §12.)*

### F. File merges + dead prune + test-in-src eviction + ctor collapse

- Merge `config.rs` + `result.rs` → `command/contract.rs`; rename `command/service/contract.rs`
  → `command/service/dto.rs` (kills the sibling `contract.rs`/`contract.rs` name collision —
  `contract.rs` = substrate data, `dto.rs` = request/response vocabulary); split the
  helpers/transcript content into **two single-job files**: `command/service/yield.rs`
  (the waiter loop + running/completed output projection, from `helpers.rs`) and
  `command/service/render.rs` (the pure `CommandOutput` rendering — `command_output`,
  `render_transcript_text`, `estimate_token_count`, `command_status` — from `transcript.rs`),
  rather than fusing five jobs into one `yield.rs`. Flatten
  `service/impls/{exec_command,read_command_lines,write_command_stdin}.rs` to `service/`
  level; delete `service/impls/mod.rs`.
- Prune 6 unconstructed `CommandServiceError` variants: `CommandWorkspaceSessionMismatch`,
  `MissingLayerStackService`, `DuplicateCommandSessionId`, `CommandAdmissionLimit`,
  `ReservationStoreMismatch`, `CommandArtifactCleanupFailed`. Keep `LayerStack(Box)+From`,
  `CommandNotFound`, `CommandIo`, `CommandAlreadyCompleted`, `CommandFinalizationFailed`,
  `InvalidCommand`, `WorkspaceSession`, `OneShotSessionCleanupFailed`. Also delete
  `CommandTranscriptUnavailable` (§E).
- Delete test-in-src: `command/service/test_support.rs`, `core.rs` 2 `*_for_test`,
  `services.rs` 3 `*_for_test`, ledger `set_force_mutation_errors_for_test`. Relocate
  fixtures to `operation/tests/support/`.
- **Retain the workspace-destroy admission** (rev 1 deleted it — regression, §9/§13):
  keep `workspace_lifecycle_admission: Mutex<()>` + `begin_workspace_lifecycle_admission`;
  `with_workspace_destroy_admission` keeps its `engine.live_values` filter body but
  re-keys its callback to `&[NamespaceExecutionId]` (sorted). `exec_command` continues to
  hold the admission across `try_reserve`→`attach` (its single temporal contract, §2c).
- Collapse the 3-layer ctor (`new → new_with_async_trace_sink → from_parts`) to one
  production ctor + a `pub(crate)`/doc-hidden `with_engine` test seam. Delete
  `shares_workspace_session`, `shares_namespace_execution_store`, and
  `services.rs::new_with_namespace_execution_store` + its two ptr_eq asserts.
- **`SandboxRuntimeOperations` drops its duplicate ledger handle:** delete the
  `namespace_execution: Arc<NamespaceExecutionLedger>` field; `observability_snapshot` /
  `ack_completed_namespace_executions` route through `self.command.namespace_execution_store()`
  (the ledger is command-owned — the writer — so SRO holding a second `Arc` was pure
  duplication once the ptr_eq invariant is deleted). `namespace_execution_store` stays on
  the command surface (§9).

---

## 4. Finalization control flow (unified foreground + background)

```
[caller] exec_command → take destroy-admission → resolve/create workspace → allocate id
         → stamp started_at_unix_ms → prepare transcript → op = ExecCommand (pure)
         → run_shell_interactive(op, target, id, on_complete)   [try_reserve … spawn]
         → attach(CommandExecValue) → drop(admission) → wait_for_command_yield: loop on is_finished
                                                          │
                          [watcher] child.wait_completion()
                          [watcher] 175  op.finalize → CommandTerminalResult (PURE)
                          [watcher] 175b on_complete(&result):  destroy one-shot / keep · trace · push record
                          [watcher] 185/186  registry.complete · promise.resolve  (is_finished := true)
[caller] ◀── is_finished observed (teardown already done) → completed output     (foreground)
         OR  yield deadline first → running output + id; watcher still runs 175b later   (background)
```

The destroy-admission is held from before `try_reserve` (inside `run_shell_interactive`)
through `attach`, then dropped — so a concurrent `destroy_workspace_session` either sees the
exec via `live_values` (post-attach) or blocks on the admission (during the value-less
window), never tearing down a workspace an in-flight exec is about to attach to. Same `175b`
step fires foreground and background; the only difference is whether a caller is parked in
the yield loop. Pre-spawn failure (A6) is the lone separate path (no process ⇒ no `on_complete`).

---

## 5. File / folder structure (16 → 14 command/ files)

| Action | File | LOC |
|---|---|---|
| delete | `command/config.rs`, `command/result.rs` | 0 |
| new | `command/contract.rs` (CommandConfig + CommandTerminalResult) | 22 |
| delete | `command/execution.rs` | 0 |
| new | `command/exec_value.rs` (CommandExecValue) | 55 |
| new | `command/finalize.rs` (CommandFinalization + apply + build_on_complete + emit_finalization_trace) | 92 |
| shrink | `command/error.rs` | 95→58 |
| shrink | `command/mod.rs` / `command/service.rs` | 15→14 / 14→13 |
| rename+shrink | `command/service/dto.rs` (was `service/contract.rs`) | 66→60 |
| shrink | `command/service/core.rs` (admission retained) | 192→128 |
| shrink | `command/service/exec.rs` (pure ExecCommand) | 121→40 |
| new | `command/service/yield.rs` (waiter + output projection, from helpers) | 120 |
| new | `command/service/render.rs` (pure CommandOutput rendering, from transcript) | 40 |
| shrink | `command/service/exec_command.rs` (flattened) | 230→150 |
| rewrite | `command/service/read_command_lines.rs` (infallible) | 86→50 |
| keep | `command/service/write_command_stdin.rs` (re-key) | 76 |
| delete | `service/test_support.rs`, `service/impls/mod.rs`, `service/helpers.rs`, `service/transcript.rs` | 0 |
| shrink | `operation/src/namespace_execution.rs` (ledger → buffer; drop enum) | 433→195 |
| shrink | `operation/src/services.rs` (drop dup ledger Arc) | 228→165 |
| grow | `namespace-execution/src/engine.rs` (`on_complete` composed) | 256→264 |
| rekey | `operation/src/observability.rs`, both `cli_definition/*` | net 0 |
| companion | `sandbox-daemon` observability (slim DTOs) | net ~0 |

---

## 6. Class / field / method changes

**Finalization migration**

| Unit | Change |
|---|---|
| `ExecCommand` (exec.rs) | reshaped → pure; remove `workspace`/`session_disposition`/`finalization_trace` + `finalize_session`/`apply_disposition`/`finalize_error`; `finalize` = pure projection |
| `SessionDisposition`, `CommandFinalizationTrace` (exec.rs) | deleted |
| `CommandFinalization` (finalize.rs) | new enum `{ KeepSession, DestroyOneShot(WorkspaceSessionHandler) }` + `apply()` (policy only) |
| `build_on_complete` (finalize.rs) | new — thin assembler over `apply` + `emit_finalization_trace` + `NamespaceExecutionRecord::completed` |
| `engine.run_shell_interactive` | + generic `on_complete`, **composed into the existing finalize closure** (no `spawn_watcher`/`run_mount` change) |

**Isomorphic collapses**

| Unit | Change |
|---|---|
| `CommandSessionId` (service/contract.rs → dto.rs) | deleted (no alias) |
| `execution_id` / `command_session_id` (core.rs) | deleted |
| `CommandExecution` (execution.rs) | deleted → `CommandExecValue` (`next_snapshot_offset: Cell<u64>`) |
| DTOs + `CommandFinalizationTraceMetadata` | rekeyed `command_session_id: NamespaceExecutionId`; `workspace_session_id: WorkspaceSessionId` (de-`Option` — always `Some`) |

**Ledger / observability**

| Unit | Change |
|---|---|
| `NamespaceExecutionLedger` | reshaped → buffer; rename `complete_namespace_execution` → `record_completed(record)` + add `NamespaceExecutionRecord::completed` ctor; remove active map / `next_id` / force-mutation / `Starting` / `allocate_*` / `begin_*` / `mark_running` / `snapshot_active` / `ExecutionObserver` impl / `find_terminal_record` / `BeginNamespaceExecution` / `CompleteNamespaceExecution` |
| `NamespaceExecutionLifecycle` enum | deleted (no surviving reader) |
| `NamespaceExecutionRecord` | drop `lifecycle_state` |
| `RuntimeNamespaceExecutionSnapshot` | slim to `{ namespace_execution_id, workspace_session_id, operation_name }` |
| engine observer | → `NoopObserver` |
| `CommandOperationService::active_namespace_executions` | new — `engine.live_values` → snapshot + deterministic id sort |
| `SandboxRuntimeOperations` (services.rs) | drop `new_with_namespace_execution_store` + 2 ptr_eq asserts + 3 `*_for_test` + the duplicate `namespace_execution` field; active via `command.active_namespace_executions()`; completed/ack via `command.namespace_execution_store()` |

**command service**

| Unit | Change |
|---|---|
| `CommandOperationService` | **keep** `Mutex<()>` admission + `begin_workspace_lifecycle_admission`; drop `shares_*` / `from_parts` / `new_with_async_trace_sink` (→ 1 ctor + `with_engine`) + 2 `*_for_test`; engine `<CommandExecValue>` |
| `with_workspace_destroy_admission` | callback `&[NamespaceExecutionId]`; body = `engine.live_values` filter under the admission lock |
| `read_command_lines` (+ `validate_read_limit`) | reshaped → infallible `CommandOutput`; best-effort transcript; clamp limit; delete `validate_read_limit` |
| `CommandServiceError` | prune 6 dead variants + `CommandTranscriptUnavailable` |

---

## 7. Expected LOC reduction

| Scope | Current | Projected | Δ |
|---|---|---|---|
| **command/ only** | 1258 | ~918 | **≈ −340 (−27%)** |
| **command/ + glue** (ledger 195 + services 165 + engine 264) | 2175 | ~1542 | **≈ −633 (−29%)** |

Both rows are summed directly from the §5 per-file projections — command/ rows = 918; glue =
195 + 165 + 264 = 624 — so the headline and the table agree. Rev 2 lands ~86 LOC *shallower*
than rev 1's optimistic −719 (which itself did not reconcile with its own per-row table),
because rev 1 under-counted: the retained admission lock (~+8), the honest `exec_value.rs`
size (55, not 40, ~+15), the `build_on_complete` SRP split (~+12), the `render.rs` split
(~+4), and `active_namespace_executions` (~+6) all cost LOC rev 1 omitted — only partially
offset by rev 2's extra cuts (deleting the `NamespaceExecutionLifecycle` enum + dead fields
~−22, the SRO duplicate ledger `Arc` ~−12, `Cell` over `AtomicU64` ~−2, de-`Option` metadata
~−8). `engine.rs` grows ~+8 (composed `on_complete`). All *current* numbers are measured
(`wc -l`); *projected* numbers are estimates for the locked behavior (the ledger is a real
buffer ~195, not ~0). The daemon companion edits net ~0 and are excluded from the headline.

---

## 8. Build-safe migration order

Each step compiles and tests green on its own.

1. **Engine seam** (nsx): add `on_complete` to `run_shell_interactive`, **compose it into
   the finalize closure** (`op.finalize` then `on_complete`, before `resolve`, inside the
   existing `catch_unwind`); `spawn_watcher`/`run_mount` untouched; existing caller passes a
   no-op. Build + test nsx.
2. **ID collapse**: delete `CommandSessionId` + shims; re-key DTOs/errors/observability/CLI
   to `NamespaceExecutionId`; de-`Option` the trace-metadata `workspace_session_id`.
3. **Wrapper → value**: delete `CommandExecution`; add `CommandExecValue` (`Cell<u64>`);
   engine generic `<CommandExecValue>`; fix call sites.
4. **Finalization**: pure `ExecCommand`; add `finalize.rs` (enum + `apply` + assembler +
   `emit_finalization_trace`); `exec_command` passes `on_complete`; `fail_command_start`
   keeps the pre-spawn path.
5. **Ledger fusion**: gut ledger → buffer + `record_completed`; delete the
   `NamespaceExecutionLifecycle` enum + dead DTO fields; engine → `NoopObserver`; add
   `CommandOperationService::active_namespace_executions` (sorted); `services.rs` derives
   active from it and drops its duplicate ledger `Arc`; companion daemon edits for the slim
   DTOs. Drop `begin`.
6. **read infallible**: `-> CommandOutput`; best-effort `transcript_window`; delete
   `CommandTranscriptUnavailable` + `validate_read_limit`; dispatch Ok-only.
7. **Cleanup**: merge/rename files (`contract.rs`, `dto.rs`, `yield.rs`, `render.rs`,
   flatten impls), prune variants, evict test-in-src (relocate fixtures to
   `tests/support/`), collapse ctor. **Keep the admission lock.** Build + test + clippy + fmt.

---

## 9. Behavior preservation

Verified **consistent-with-fixes**; the prior blocking findings are resolved:

- **Race (double-finalize)** — single finalization trigger at `175b` before `resolve`; no
  double-finalize between child completion and the foreground waiter.
- **Race (destroy-while-active)** — the workspace-destroy admission lock is **retained**
  (rev 1's `live_values`-only check was a regression: registry `live_values` skips
  reserved-but-unattached entries, so during `try_reserve`→`attach` a concurrent destroy
  would see zero active execs and tear down a live workspace). `exec_command` holds the
  admission across that window; `with_workspace_destroy_admission` takes the same lock
  before its `live_values` read. The existing
  `destroy_workspace_session_waits_for_existing_session_exec_until_active_insert` test
  encodes exactly this invariant and stays valid. *(Why not "attach at reserve" instead?
  The full `CommandExecValue` cannot exist before spawn — its `exec: InteractiveExecution`
  needs the spawned PTY — and pushing `workspace_session_id` into the generic registry to
  expose reserved entries would violate the namespace-execution boundary law. The narrow
  lock is the minimal, boundary-clean fix.)*
- **Dropped `on_running`** — no Starting state; active executions derive from the registry,
  where a live entry IS Running.
- **Record clock divergence** — the **completed** record uses one clock
  (`started_at_unix_ms`); the active snapshot exposes no start time (the daemon stamps
  `sampled_at_unix_ms` at snapshot), so there is no begin-vs-elapsed mismatch to diverge.

Public surface preserved: `exec_command(_with_origin_request_id)`, `write_command_stdin`,
`read_command_lines` (now `-> CommandOutput`), `with_workspace_destroy_admission`,
`config`/`new`/`namespace_execution_store`; the DTOs (re-keyed, slimmed — daemon companion
edits in §10); the ledger projection consumed by `observability_snapshot` + `ack` + the
daemon `trace_record`; `SandboxRuntimeOperations` (minus the redundant internal field).

---

## 10. Open issues (test-side + daemon companion fixups)

- `sandbox-daemon` tests consume `services.rs::{begin,complete}_namespace_execution_for_test`
  (deleted) → replace via `record_completed` or a `tests/support` helper.
- `tests/namespace_execution.rs` uses `allocate_namespace_execution_id` / `begin` /
  `set_force_mutation_errors_for_test` (deleted) → rewrite/remove (subjects gone).
- `observability_snapshot.rs` `#[should_panic]` on the ptr_eq guard
  (`runtime_operations_enforce_shared_namespace_execution_store`) → delete (guard removed).
- **Daemon DTO companions (mechanical):** `observability/namespace_execution.rs`
  `snapshot_record` emits the literal `"running"` (active `lifecycle_state` field gone) and
  no longer reads the active snapshot's `started_at_unix_ms`; daemon tests that construct
  `RuntimeNamespaceExecutionSnapshot` / `NamespaceExecutionRecord` literals drop the removed
  fields; `service.rs` trace-metadata mapping drops the `Option` on `workspace_session_id`.
- **Daemon trace-metadata rekey:** daemon tests that construct
  `CommandFinalizationTraceMetadata` literals rekey `command_session_id` from
  `CommandSessionId` to `NamespaceExecutionId` and drop the now-deleted `CommandSessionId` /
  `NamespaceExecutionLifecycle` / `BeginNamespaceExecution` / `CompleteNamespaceExecution`
  imports (consistent with §3.C and §6).
- The rev-1 `exec_command.rs` "adjust destroy-while-active race test to `live_values`
  semantics" item is **withdrawn** — the admission lock is retained, so the existing test is
  correct as-is.

---

## 11. Risks

- **Engine temporal contract**: command's finalization correctness depends on the engine
  invoking `on_complete` before `promise.resolve`. Documented in the engine API
  (composed into the finalize closure); re-check if the watcher is ever reordered.
- **Command temporal contract**: destroy correctness depends on `exec_command` holding the
  admission across `try_reserve`→`attach`. Keep these adjacent; if attach ever moves, the
  window widens.
- **`publish` axis (future)**: `CommandFinalization` is **closed** at `{KeepSession,
  DestroyOneShot}` for this rework. A future `PublishThenKeep/Destroy` is **not** a
  drop-in: the engine seam is open for it, but the command side has no `LayerStackService`
  collaborator (`build_on_complete` captures only `WorkspaceSessionService`), so publish is
  a **hard prerequisite** — thread an `Option<Arc<LayerStackService>>` into the command ctor
  + closure first. Also decide its sync/async story: running publish before `resolve` would
  hold the completion signal for the publish duration.
- **command hub**: as policy grows (publish, retries, quotas), keep each sub-responsibility
  in its own file (`exec_command.rs`, `finalize.rs`, `yield.rs`, `render.rs`) to prevent a
  god-object.

---

## 12. Deferred follow-ups (correct, but out of this rework's scope)

- **Delete the `ExecutionObserver` trait** (engine `observer` field + `on_running`/
  `on_terminal`): every production engine is `NoopObserver` after §B, but the trait + the
  `NamespaceExecutionEngine::new` signature are consumed by `namespace-execution` tests and
  the `workspace` crate — a cross-crate change beyond "one generic seam." (~−55)
- **Delete the nsx `required_transcript_window` free function**: loses its only
  operation-layer caller in §E; confirm no other `namespace-execution` caller first.
- **`run_shell_interactive` attach-at-spawn** (`build_value` closure): removes the caller's
  separate `attach` lock acquisition. A roundtrip win, **not** a race fix (does not close
  the reserve→spawn window), so it does not replace the admission lock.
- **`ShellRunner` trait seam**: would delete the `with_engine` test seam + PTY plumbing in
  pure-logic tests, but introduces an abstraction with a single impl — premature.
- **Single-lock `drain_snapshot`** for `observability_snapshot` (active+completed+errors in
  one pass): observability is best-effort; the transient active/completed straddle is
  tolerable — document rather than restructure.

---

## 13. Adversarial review disposition

Six-lens adversarial review (`adversarial-review-prompt.md`; full record in
`adversarial-review-findings.md`). Net effect on this spec:

- **Fixed (rev 1 → rev 2):** retained the destroy admission lock (the one regression);
  `on_complete` composed into the finalize closure (no `spawn_watcher` param leaking onto
  the mount path); `active_namespace_executions` as a command-owned, sorted method; split
  `yield.rs`/`render.rs` and `build_on_complete` for SRP; dropped the dead
  `NamespaceExecutionLifecycle` enum + DTO fields; `Cell<u64>` over `AtomicU64`; SRO sheds
  its duplicate ledger `Arc`; de-`Option` the trace metadata; `service/contract.rs` →
  `dto.rs`; honest `exec_value.rs` size; dropped the false "extensible-to-publish" claim.
- **Rejected (gate violations / correctness):** move ledger ownership to SRO (breaks the
  preserved `namespace_execution_store` surface); write traces straight through the sink and
  delete the buffer (breaks the ledger projection the daemon consumes); a `CompletionSink`
  trait (reverses the settled generic-closure decision); a `CommandSessionId` alias
  (re-opens the id collapse); dropping `started_at: Instant` (elapsed needs a monotonic
  clock).
- **Confirmed already-correct (no change):** `CommandTerminalResult` stays distinct from
  `RunnerOutcome` (the latter is not `Clone` and owns the full payload); the render/token
  helpers and `write_command_stdin` earn their keep.
