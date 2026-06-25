# Adversarial Review Findings — `docs/command-rework/spec.md`

Produced by running `adversarial-review-prompt.md`: six independent adversarial
reviewers (1A SRP, 1B DIP/coupling, 1C OCP/boundary, 2A delete-more, 2B
roundtrips, 2C merge/slim) attacked the spec without seeing each other's output,
then one synthesis agent deduped, gated against the settled-decision baseline,
and prioritized. All findings are tied to real file:line citations and were
verified against the code on branch `consolidate-namespace-execution-types`.

---

## Verification of the decisive contested claims (code-checked)

**Destroy-race (the most important call): the spec's `live_values`-only admission
IS a real behavior regression.** The current `Mutex<()>` is held across
`exec_command`'s reserve→spawn→attach window (core.rs:137-141,
exec_command.rs:36-106). The test
`destroy_workspace_session_waits_for_existing_session_exec_until_active_insert`
(exec_command.rs:604-702) blocks the launcher mid-spawn and asserts (a) destroy
is **not finished** and (b) `fake.destroy_calls().is_empty()` during that window,
then that destroy is **rejected** with
`active_command_session_ids=["namespace_execution_1"]` once the value is attached.
Because `registry.live_values` filters out `value: None` entries
(registry.rs:110-118) and `attach` happens AFTER `run_shell_interactive` returns
(exec_command.rs:97), a `live_values`-only check sees an EMPTY active list during
reserve→attach → destroy proceeds against a workspace a command is about to attach
to. The spec's framing of this as a "mechanical test adjustment" (§10) is wrong.

**Ledger ownership (1A#4 vs 2C#2): 2C#2 wins.** Post-refactor the ledger writer is
command-owned (`build_on_complete` A4 + `fail_command_start` A6); the reader is
`SandboxRuntimeOperations` (observability_snapshot/ack, services.rs:108-149). SRO
holds a *duplicate* Arc + two ptr_eq asserts (services.rs:18,45-52). 1A#4 (move
ownership to SRO, drop `namespace_execution_store` from command surface) breaks
gate (c) — §9 explicitly preserves `namespace_execution_store`. 2C#2 keeps the
writer-owned ledger and just deletes SRO's redundant field.

**Contract files (1A#5 vs 2A#5): merge into one (2A#5).** Resolve the name
collision by keeping `command/contract.rs` (data) and renaming
`command/service/contract.rs` → `command/service/dto.rs`.

**Confirmed estimate-corrections (these ADD LOC / are NEGATIVE — spec is right):**
- 2A#7: `RunnerOutcome` is NOT Clone and owns a `serde_json::Value`
  (shell.rs:16-19); the promise needs `T: Clone`. `CommandTerminalResult` cannot
  collapse into it. Keep separate.
- 2C#7: `exec_value.rs` cannot be a pure forward — must keep
  `transcript_window`/`elapsed_seconds`/snapshot-offset methods
  (helpers.rs:94-105). ~55 LOC, not 40.
- 2A#6/8: `estimate_token_count`/`render_transcript_text` and
  `write_command_stdin` earn keep — do not delete.

---

## 1. Deduped recommendations table

| title | category | verdict | rationale | est_loc_delta | behavior_risk | public_surface_impact | contradicts_settled | sources |
|---|---|---|---|---|---|---|---|---|
| **Attach value/liveness marker at reserve time (inside `run_shell_interactive`), under the reserve lock** | DIP/roundtrip/race | **adopt** | Only correct fix for the destroy-race. Closes reserve→attach window so `live_values` sees the command immediately; lets the Mutex actually be deleted. Pass a `build_value`/placeholder carrying `workspace_session_id`. | +12 | HIGH (fixes a regression) | none (internal nsx API) | none — *enables* §F's Mutex deletion safely | 1B#3, 2B#1, 2B#2, 1B#6 |
| Keep ledger in command; delete SRO's duplicate Arc + 2 ptr_eq asserts, use `self.command.namespace_execution_store()` | DIP/slim | **adopt** | Removes dup field + `new_with_namespace_execution_store` + asserts. Writer stays command, reader SRO. | −12 | low | none (keeps `namespace_execution_store`) | none | 2C#2 (rejects 1A#4) |
| Delete `RuntimeNamespaceExecutionSnapshot`; active = `Vec<NamespaceExecutionRecord>` | delete-more | **reject→partial** | Daemon `snapshot_record` reads only `operation_name`+`lifecycle_state`+ids from active; record carries 12 fields. Keep the slim DTO as the `live_values` projection type; drop its dead fields instead. | 0 | low | DTO consumed by daemon | none | 2A#1 |
| Drop dead `started_at_unix_ms` from active snapshot (daemon uses `sampled_at_unix_ms`) | delete-more | **adopt-with-care** | service.rs:303-309 passes `sampled_at_unix_ms`; never reads snapshot's `started_at_unix_ms`. | −8 | low (daemon tests construct literals) | active DTO field | weakens §9 "consistency" framing (cosmetic) | 2C#4, 2A#4, 1B#5 |
| Drop `lifecycle_state` from active snapshot OR hardcode "running"; constant once Starting dies | OCP/delete | **adopt** | Daemon re-maps a constant. | −10 | med (daemon tests build DTO) | active DTO field | none | 1C#4, 2A#2 |
| Drop `lifecycle_state` from `NamespaceExecutionRecord` (completed); `trace_record` never reads it | slim | **adopt** | Dead in completed path (namespace_execution.rs:33-58). | −6 | low | record DTO field | none | 2C#3 |
| `next_snapshot_offset: AtomicU64` → plain `u64`/`Cell`; drop Acquire/Release | DIP | **adopt** | `with_value` runs `f` under registry mutex; take+window+advance are one `with_value` call. Atomic is dead sync. | −6 | none | none | none | 1B#2 |
| Split `build_on_complete` into thin assembler + `apply_policy`/`emit_trace`/`record::completed` | SRP | **adopt-with-care** | Real SRP win (4 jobs). Fold the split into initial authoring of finalize.rs, not a post-hoc refactor. | +12 | low | none | none | 1A#1, 1C#6 |
| Move pure DTO render out of yield.rs into a render module | SRP | **adopt-with-care** | yield.rs would fuse 5 jobs. Worth a `render.rs`; grows file count vs "16→13" headline; net ~0 LOC. | +8 / ~0 | low | none | none | 1A#2 |
| Invoke `on_complete` inside a `run_shell_interactive`-specific finalize wrapper, not a `spawn_watcher` generic param | OCP | **adopt** | `spawn_watcher` is shared with `run_mount`. A generic param forces a no-op on the mount path. Preserve 175b-before-186 + `catch_unwind`. | +8 vs +10 | low | none (internal nsx) | none | 1C#1 |
| Active-observability → `CommandOperationService::active_namespace_executions()` (owns live_values + deterministic sort) | SRP/OCP | **adopt** | services.rs would otherwise read command-owned `CommandExecValue` through an engine it doesn't own AND lose the id sort. Daemon stable trace ids depend on order. | +6 net | MED if sort dropped → none if method added | none | none | 1A#6, 1C#2 |
| Rename `command/service/contract.rs` → `dto.rs` (resolve collision); not two renamed data files | SRP/merge | **adopt** | One data `contract.rs` (spec intent) + service `dto.rs`. | −12 | low | none | none | 1A#5(rej), 2A#5 |
| Keep `pub type CommandSessionId = NamespaceExecutionId;` alias instead of hard-deleting | OCP | **defer** | Lower churn but re-introduces the dual vocabulary §C removes; wire field name already preserved. | +1 vs −4 | low | DTO field name preserved either way | re-opens §C collapse | 1C#5 |
| Delete `ExecutionObserver` trait + `on_running`/`on_terminal`/`observer` field | merge | **defer** | Real −55, but widens scope into nsx + workspace (engine ctor at workspace/namespace/mod.rs:111). Spec scopes nsx to "one generic seam." Follow-up. | −55 | MED (loses 187 step; on_running timing shifts to 175b) | nsx engine ctor (cross-crate, not gate-c) | partially re-opens dropped-on_running | 2C#1 |
| Delete `required_transcript_window` (dead after §E) + `is_completed` shim | slim | **adopt** | `required_transcript_window` dead once read is best-effort; confirm `is_completed` only feeds the deleted test shim. | −20 / −12 | low | none | none | 2C#5 |
| Fold pure `ExecCommand` (exec.rs ~40 LOC) into exec_command.rs | merge | **defer** | §11 wants sub-responsibilities in separate files; exec_command.rs is already largest. | −6 | low | none | tension with §11 | 2C#6 |
| `drain_snapshot()` returning active+completed+errors under one ledger lock (or document non-atomicity) | roundtrip | **defer** | 3 locks over 2 mutexes; active/completed can straddle a completion → transient double-count. Observability is best-effort; document. | −6 | med (transient double-count) | none | none | 2B#4 |
| Fold `wait_for_command_yield` settle-path double-lock into top-of-iteration `with_value`; drop line-49 re-check | roundtrip | **adopt** | 3 lock acquisitions where 2 suffice (helpers.rs:27-60). | −18 | low | none | none | 2B#3 |
| De-Option `CommandFinalizationTraceMetadata.workspace_session_id` (always Some) | slim | **adopt-with-care** | Simplifies daemon mapping; consumed metadata DTO so it's a signature change. | −8 | low | trace metadata DTO | none | 2A#3 |
| Drop redundant `Instant started_at` OR derive elapsed from `unix_ms` | slim | **reject** | `elapsed_seconds()` needs a monotonic clock; `unix_ms` is wall-clock (jitter). Keeping both is correct. | −4..−6 | med if adopted | none | re-opens single-clock invariant | 1B#5, 2A#4, 2C#4 |
| `ShellRunner` trait to delete `with_engine` test seam + PTY plumbing | DIP | **defer** | −25 net but a new abstraction for a single impl; spec already keeps `with_engine` as a doc-hidden seam. Premature DIP. | −25 | med | none | none | 1B#1 |
| `CommandFinalization` "extensible to Publish" framing is false (no LayerStackService) | OCP/boundary | **adopt** | build_on_complete captures only WorkspaceSessionService. Drop the framing or mark §11 publish as a hard prereq. No code change. | 0 | none | none | none | 1C#3 |
| `CompletionSink` trait to localize the on_complete closure | DIP | **reject** | Reshapes the settled generic-closure decision into a trait. Gate (a). | +12 | low | none | reverses Option-2 closure decision | 1B#4 |
| Write trace straight through the sink, delete pending/recent/ack buffer | roundtrip | **reject** | Deletes the ledger projection consumed by observability_snapshot+ack+daemon trace_record. Gate (c), HIGH risk. | −120/0 | HIGH | breaks ledger projection | gate c | 2B#5 |
| by-value `From`/`build_request` to avoid two structural copies | roundtrip | **defer** | −4, low value, touches the resolve→entry→From seam. | −4 | low | none | none | 2B#6 |

---

## 2. Conflicts resolved

1. **1A#4 vs 2C#2 (ledger ownership) → 2C#2.** Writer is command; reader is SRO.
   1A#4 deletes `namespace_execution_store` from the command's public surface,
   which §9 preserves → gate (c). Adopt 2C#2: keep the ledger command-owned,
   delete SRO's duplicate Arc + `new_with_namespace_execution_store` + both ptr_eq
   asserts, route SRO through `self.command.namespace_execution_store()`.

2. **1A#5 vs 2A#5 (contract files) → 2A's single data file.** Spec already merges
   config+result into `command/contract.rs`. Resolve the collision by renaming the
   *service*-level file → `command/service/dto.rs`.

3. **Destroy-race cluster (1B#3, 2B#1, 2B#2, 1B#6) → the spec's `live_values`-only
   admission is a genuine regression; §10 does not handle it.** **MUST adopt** the
   reserve-time attach: have `run_shell_interactive` attach the `CommandExecValue`
   (or a minimal projection carrying `workspace_session_id`) under the *same*
   registry lock as `try_reserve`. Only then is deleting the Mutex (§F) safe.

4. **Two-clocks (1B#5/2A#4/2C#4) → split the verdict.** Dropping `Instant` is
   **rejected** (elapsed needs a monotonic clock). Dropping the *active snapshot's*
   `started_at_unix_ms` (which the daemon never reads) is **adopted**.

5. **2C#1 (delete ExecutionObserver) vs spec's NoopObserver → defer to follow-up.**
   Correct in principle (−55) but widens scope into nsx + workspace and shifts
   on_running timing. Land `NoopObserver` now (settled), delete the trait separately.

---

## 3. Additional LOC reduction beyond −719

Adopt-bucket cuts: ledger dup Arc (−12), active `started_at_unix_ms` (−8), active
`lifecycle_state` (−10), completed `lifecycle_state` (−6), `next_snapshot_offset`
atomic (−6), `required_transcript_window` + `is_completed` (−20..−32), yield
double-lock (−18), de-Option trace metadata (−8).

Adds/corrections the spec under-counted: reserve-time attach (+12), `exec_value.rs`
correction (+15 vs 40), build_on_complete split (+12), `active_namespace_executions()`
(+6); on_complete-in-wrapper (≈ −2 vs spec).

**Net beyond −719:** roughly **−88 to −100 of new cuts**, offset by **~+38 of
corrections/splits**. **Realistic landing: −760 to −785 total** (~40–65 LOC deeper
than −719, NOT the optimistic extra-hundreds). The `command/` headline −381 is
optimistic by ~15 (exec_value).

---

## 4. Top next cuts (ordered: highest value / lowest risk first)

1. **Reserve-time attach (close destroy-race) + delete the Mutex.** The gate that
   makes §F's Mutex deletion correct. Do before §F lands. (1B#3/2B#1/2B#2)
2. **Delete SRO's duplicate ledger Arc + ptr_eq asserts + `new_with_namespace_execution_store`.** −12, zero risk. (2C#2)
3. **`next_snapshot_offset` AtomicU64 → plain `u64`.** −6, provably dead sync. (1B#2)
4. **Drop `lifecycle_state` from both active snapshot and completed record.** −16. (1C#4, 2C#3)
5. **Drop active snapshot `started_at_unix_ms`.** −8, daemon never reads it. (2C#4)
6. **Fold yield settle-path double-lock.** −18, local roundtrip cut. (2B#3)
7. **Delete `required_transcript_window` + `is_completed` shim.** −20..−32, dead after §E/§F. (2C#5)
8. **`on_complete` via shell-specific finalize wrapper, not a `spawn_watcher` generic param.** Keeps `run_mount` untouched. (1C#1)
9. **`CommandOperationService::active_namespace_executions()` owning live_values + sort.** Restores deterministic id ordering for daemon trace ids. (1A#6/1C#2)
10. **Rename `service/contract.rs` → `dto.rs`** to kill the sibling name collision. (2A#5)

---

## 5. Rejected

- **1A#4 — move ledger ownership to SandboxRuntimeOperations.** Gate **(c)**: §9
  preserves `namespace_execution_store`. Superseded by 2C#2.
- **2B#5 — write trace straight through the sink, delete pending/recent/ack.** Gate
  **(c)**: deletes the ledger projection consumed by `observability_snapshot` +
  `ack` + daemon `trace_record`. Also HIGH risk.
- **1B#4 — `CompletionSink` trait for the on_complete closure.** Gate **(a)**:
  reshapes the settled Option-2 generic-closure / on_complete-hook decision.
- **1C#5 — keep `CommandSessionId` as a type alias.** Re-opens the settled §C
  isomorphic collapse; the wire field name is already preserved without it.
- **Drop `Instant started_at` / derive elapsed from `unix_ms` (strong form).**
  Re-opens the single-clock framing AND computes elapsed from a non-monotonic wall
  clock. The `Instant` is load-bearing; only the active snapshot's unread
  `started_at_unix_ms` is genuinely dead (adopted separately).

**Deferred (correct but out-of-scope/premature):** 2C#1 (delete ExecutionObserver),
1B#1 (`ShellRunner` trait), 2C#6 (fold ExecCommand into caller), 2B#4
(`drain_snapshot` atomicity — document instead), 2B#6 (by-value From).

**Confirmed NEGATIVE findings the spec already gets right (no action):** 2A#7
(`RunnerOutcome` not Clone → keep `CommandTerminalResult`), 2A#6
(`estimate_token_count`/`render_transcript_text` earn keep), 2A#8
(`write_command_stdin` correctly delegates), 2C#7 (`exec_value.rs` ~55 LOC not 40).
