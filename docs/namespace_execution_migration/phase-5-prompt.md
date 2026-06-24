# Agent Prompt — Author the Phase 5 Spec (Remount coordinator onto engine queries)

## Role & deliverable

You are a senior systems architect. Produce a **rigorous, implementation-ready
specification** for **Phase 5** of the namespace-execution migration — *not* the
implementation. A different agent will build strictly from your spec, so it must
be precise enough to implement without asking you a single follow-up question.

- **Write exactly one file:** `docs/namespace_execution_migration/phase-5-spec.md`.
- **Treat the rest of the tree as read-only.** Do not modify production code,
  tests, or any `Cargo.toml`. Read and run read-only commands (`rg`, `cargo
  check`, `cargo tree`, `ls`) as much as you need to ground every claim.

A spec is sophisticated not because it is long but because it has *already made
the hard decisions* — here, the **concurrency-safety invariant** that survives
deleting the per-command remount mirror. Phase 5 is small in LOC (≈ **−27**:
`quiesce` 229→~205, `coordinator` 98→~95 per the design doc's scorecard) but it
removes the *single piece of state* that today makes overlapping quiesce/resume
safe. Almost all of your effort belongs in re-deriving that safety from
coordinator-owned state + engine-registry queries — not in the mechanical edits.

## Method (work in this order; writing is the last step)

1. **Investigate to ground truth.** Do not trust the starting map below — verify
   and extend it. Complete the *Investigation mandate*. Tag every factual claim
   grounded (`file:line`) or *assumed* (few, listed, justified).
2. **Reconcile three sources of truth.** The design doc (`namespace-execution.md`)
   states the *end state*; the migration doc (`migration-phases.md`) states the
   *phasing*; the code states *today*. They diverge. For each capability Phase 5
   relies on, classify it **exists-today**, **Phase-3-must-add**, or
   **Phase-5-adds**, and surface every contradiction. Phase 5 is unusual: it sits
   *on top of* Phase 3 (the registry must already hold live command executions —
   `migration-phases.md` dependency order `1 → 2 → {3,4} → 5 → 6`), so its "today"
   is partly the *Phase-3 end state*, which does not yet exist in code (the tree is
   mid-migration and may not compile). Be explicit about which of your cited
   anchors are present-tense code vs. Phase-3 obligations you are consuming.
3. **Design the minimal change.** No new store, no second per-session map, no
   revived `CommandProcessStore`. The coordinator owns exactly **one**
   `RemountCancellationToken` + an affected-id set; the live-command set comes from
   the engine registry. Before adding any field or type, name the existing one it
   should reuse.
4. **Stress-test before writing.** Walk the *Hard problems* catalog and every
   interleaving (overlapping quiesces, a command going terminal mid-quiesce, a
   command admitted after the query, Drop-resume). A design that has not survived
   the stale-resume race is not ready to write down.
5. **Write** per the *Required structure*.
6. **Self-review** against the *Quality bar* and fix every gap before reporting.

## Inputs to study

- `docs/namespace-execution.md` — design of record. Internalize the
  **"CommandProcessStore Disposition"** table rows for `remount_switch_state` and
  `remount_cancellation` (`~:470-471` — both **DELETE the mirror**; "the coordinator
  owns one `RemountCancellationToken` + an affected-id set … the token lives once,
  on the coordinator"); the **remount-coordination paragraph** (`~:484-488` — "asks
  the engine registry for live interactive executions in a workspace (via the
  observer index → ids → `InteractiveExecution` pgid/cancel) … `command::process_group`
  inspection stays, embedded into `CommandRemountInspection` (deleting the
  field-by-field `merge_report`)"); the **Observability Contract**; and the
  **scorecard** rows for `coordinator.rs`/`quiesce.rs` (`~:623-625`).
- `docs/namespace_execution_migration/migration-phases.md` — the **Phase 5**
  section, **"Invariants held at every phase boundary"**, and **"Cross-phase
  sequencing constraints"**. Binding: your spec refines, never silently
  contradicts. Note the green-gate phrasing: *"a stale resume does not cancel a
  command owned by a newer quiesce (the token-on-coordinator + id-set preserves
  this)."* This sentence is your acceptance criterion — prove it.
- `docs/namespace_execution_migration/phase-3-spec.md` (if present) — its
  `CommandExecution`/registry shape is the contract Phase 5 queries. The phase-3
  spec was required to *not preclude* "the registry can return live interactive
  executions per workspace." Confirm what it actually exposes; if it under-delivers,
  that is a contradiction to surface, not to paper over.

## Investigation mandate (do this first; cite `file:line`)

- **Enumerate every reader** of the two mirrors being deleted —
  `remount_cancellation` and `remount_switch_state` on `ActiveCommandProcess` — and
  of `merge_report`, `update_active`, `active(&id)`,
  `active_command_session_ids_for_workspace_session`. The migration doc's line
  numbers are stale; produce the *live* set with `rg`. A deletion is safe only once
  you have shown no surviving reader **outside** the coordinator/quiesce pair (and
  for the mirrors: that they are written by quiesce and read only by quiesce's
  `same_token` checks — i.e. the design's "write-only mirror" claim holds).
- **Reconstruct today's stale-resume protection exactly.** Walk
  `quiesce.rs`: `set_switch_state` (`~:138-153`) writes `remount_switch_state` into
  each active **only if** `active.remount_cancellation.same_token(self.cancellation)`;
  `resume_command_records` (`~:181-203`) clears the mirror and
  cancels/resumes **only if** `same_token` matches; `coordinator.rs:63-70` plants
  `remount_cancellation = Some(token)` under `update_active`. State the precise
  interleaving this defends against: quiesce A captures command C, quiesce B later
  re-captures C (overwriting C's mirror token with B's), then A resumes — A's
  `same_token` no longer matches, so A leaves C to B. **Your replacement must
  reproduce this exact outcome without the mirror.** Pin where the "current owner of
  C" now lives.
- **Trace the driver** `service/impls/remount_workspace_session.rs` end to end:
  which `CommandRemountQuiesce` methods it calls and in what order
  (`begin_workspace_remount_quiesce` → `inspection()`/`blocked_reason` →
  `cancellation_requested()` → `finish()` on the blocked path; else
  `set_switch_state(CriticalSwitch)` → apply remount → `set_switch_state(Resuming)`
  → `finish()`). This public surface and its semantics are **preserved**; your spec
  refactors the *innards*, not the driver contract. List the methods the
  `CommandRemountCoordinator` trait (`service/core.rs:9-13`) and the driver depend
  on, and confirm none change signature.
- **Pin the admission-guard scope.** `coordinator.rs:17` takes
  `_admission_guard = self.begin_workspace_lifecycle_admission()` but binds it to a
  local that drops at the end of `begin_workspace_remount_quiesce`. Determine
  whether this serializes only the *begin* phase or the whole quiesce lifetime
  (read `begin_workspace_lifecycle_admission`). This decides whether two quiesces
  for the same workspace can overlap across the critical-switch window — which is
  the entire premise of the stale-resume problem.
- **Resolve the Phase-3 `CommandExecution` surface** the coordinator will query
  (the registry value `V`): does it expose (a) the **workspace association** needed
  to filter per-workspace (a `WorkspaceSessionId`, or a `workspace_root`), (b) a
  **process-group id** (today `active.process.process_group_id() -> Option<i32>`),
  and (c) a **cancel** handle (today `active.process.cancel_process()`; Phase 3's
  `InteractiveExecution::cancel()` → `killpg`)? Cite where each lives now and where
  Phase 3 moves it. Any item Phase 3 does not expose is a **consumed-contract gap**
  you must flag (and, if unavoidable, specify the minimal Phase-3 addition Phase 5
  needs — but prefer reusing what `CommandExecution` already carries).
- **Map the two inspection structs.** Diff `CommandRemountInspection`
  (`quiesce.rs:7-24`) against `ProcessGroupInspection`
  (`command/src/process_group.rs:16-29`): the seven count fields +
  `inspected`/`quiesce_attempted`/`resumed`/`blocked_reason`/`detail` are shared
  and summed by `merge_report` (`quiesce.rs:212-229`); only `active_commands`,
  `command_session_ids`, `process_group_ids` are coordinator-only. This diff drives
  the "embed `ProcessGroupInspection`, delete `merge_report`" decision and whether
  `can_live_remount()` (`quiesce.rs:50-57`) and the inspection's serialized/observed
  shape can be preserved through the embed.

## Consumed Phase 3 API (the contract — Phase 3's end state, not today's code)

Phase 5 builds on Phase 3. The engine registry already exists (Phase 2); the
*command* value it retains is Phase 3's. Pin the exact surface Phase 5 consumes so
this spec doubles as an acceptance check on Phase 3, and flag any item whose
current/Phase-3 signature differs:

```rust
// Phase 2 — present in code today (crates/sandbox-runtime/namespace-execution/src/registry.rs):
ExecutionRegistry<V>::live_values<R>(&self, f: impl Fn(&V) -> Option<R>) -> Vec<R>; // :141 — the documented Phase-5 hook
ExecutionRegistry<V>::with_value<R>(&self, id, f: impl FnOnce(&V) -> R) -> Option<R>; // :114
ExecutionRegistry<V>::is_live(&self, id) -> bool;                                     // :123

// Phase 3 — the retained value V = CommandExecution (does not exist yet):
// CommandExecution must expose, for Phase 5:
//   - workspace association: WorkspaceSessionId (or workspace_root) for per-workspace filtering
//   - process_group_id() -> Option<i32>     (today: CommandProcess::process_group_id)
//   - cancel()                              (today: CommandProcess::cancel_process → killpg)
// and the command service holds Arc<NamespaceExecutionEngine> (so the registry is reachable
// from CommandOperationService — the CommandRemountCoordinator impl).
```

State plainly that Phase 5 **does not** touch: the engine/launcher, the watcher
thread, start-ack, the mount path (Phase 4), or the daemon. It is confined to
`operation/src/workspace_remount/service/command/{coordinator,quiesce}.rs` (+ the
unavoidable wiring to reach the registry from `CommandOperationService`).

## Current-state starting map (verify and extend — do not treat as complete)

`operation/src/workspace_remount/service/command/coordinator.rs` (98 lines):
`impl CommandRemountCoordinator for CommandOperationService` — sole method
`begin_workspace_remount_quiesce` (`:13`). Today it: holds the admission guard
(`:17`); pulls `active_command_session_ids_for_workspace_session` from
`process_store()` (`:18-20`); mints a `RemountCancellationToken` (`:21`); for each
id reads `active.process`/`active.workspace_root` (`:50-51`), gets
`process.process_group_id()` (`:54`), then `update_active(|active| …)` to plant
`lifecycle_state = QuiescedForRemount`, `cancellation = None`,
`remount_cancellation = Some(token)`, `remount_switch_state = Some(Quiescing)`
(`:63-70`); inspects via `controller.inspect_command_process_group(pgid, root)`
(`:78-80`) and folds the result with `merge_report` (`:82`); finally
`set_switch_state(ReadyToSwitch)` and `resume()` if `!can_live_remount()`
(`:92-95`).

`operation/src/workspace_remount/service/command/quiesce.rs` (229 lines):
`CommandRemountInspection` (`:7-24`, derives `Debug,Clone,Default,PartialEq,Eq`),
`can_live_remount` (`:50-57`); `RemountBlockReason` (`:26-47`); `RemountSwitchState`
(`:65-72`); `RemountCancellationToken { cancelled: Arc<AtomicBool> }` (`:74-98`)
with `same_token` = `Arc::ptr_eq` (`:94-97`); `CommandRemountQuiesce` (`:100-108`)
— fields `inspection`, `held_process_group_ids`, `command_session_ids`,
`process_store: Arc<CommandProcessStore>`, `cancellation`, `switch_state`,
`controller: Arc<dyn ProcessGroupController>`. `set_switch_state` (`:138-153`)
writes `remount_switch_state` per command **gated by `same_token`**; `resume`
(`:165-179`) → `resume_command_records` (`:181-203`) clears the mirror and (gated
by `same_token`) either `process.cancel_process()` + `lifecycle_state = Cancelled`
or `lifecycle_state = Running`; `Drop` calls `resume` (`:206-210`); `merge_report`
sums `ProcessGroupInspection` into `CommandRemountInspection` field-by-field
(`:212-229`).

`operation/src/workspace_remount/service/command/mod.rs` (`:1-9`) — re-exports the
public types and `ProcessGroupController`/`ProcProcessGroupController` from
`sandbox-runtime-command`.

`operation/src/workspace_remount/service/core.rs` — `CommandRemountCoordinator`
trait (`:9-13`); `WorkspaceRemountService` holds `command: Arc<dyn
CommandRemountCoordinator>` (`:27,34`).
`operation/src/workspace_remount/service/impls/remount_workspace_session.rs`
(`:7-57`) — the driver; consumes `inspection()`/`cancellation_requested()`/
`set_switch_state`/`finish` only.

`command/src/process_group.rs` — `ProcessGroupInspection` (`:16-29`),
`ProcessGroupController` (`:114-122`), `ProcProcessGroupController` (`:125-139`).
**Unchanged** by Phase 5; the inspection per process group still happens here.

`namespace-execution/src/registry.rs` — `ExecutionRegistry<V>` with `live_values`
(`:141`, comment at `:138-140` names it the Phase-5 hook), `with_value` (`:114`),
`is_live` (`:123`). **Present today.**

## Hard problems the spec MUST resolve (this is where Phase 5 is hard)

1. **The stale-resume invariant without the mirror.** Today the per-command
   `remount_cancellation` mirror + `same_token` is what makes overlapping
   quiesce/resume safe: a later quiesce re-stamps a command's mirror token, so the
   earlier quiesce's resume becomes a no-op for that command (it no longer owns it).
   Phase 5 deletes the mirror. Specify **where the "current remount owner of command
   C" now lives** and prove the same outcome. Decompose and resolve **both**
   sub-cases — do not conflate them:
   - **Same-coordinator** lifecycle: one `CommandRemountQuiesce` owns one token +
     its captured affected-id set for its whole lifetime; `set_switch_state`/`resume`
     act only on that captured set. This is mechanical — specify it.
   - **Cross-coordinator** supersession: quiesce A and quiesce B both target
     workspace W and overlap. If the admission guard does **not** serialize the full
     lifetime (see Investigation), two live tokens can target command C at once. The
     coordinator-owned id-set alone cannot tell A "B now owns C" — that fact lived in
     C's mirror. Resolve this concretely: either (a) **prove overlap is impossible**
     (the admission guard or `begin_remount` serializes per-workspace remounts for
     the whole critical section — cite the mechanism and its scope), or (b) **carry a
     per-execution owner token** somewhere that survives mirror deletion (e.g. a
     single `Option<RemountCancellationToken>` on the registry-stored
     `CommandExecution`, re-stamped under the registry lock — note this is *not* a
     revived store, it is one field on the existing value) and re-derive `same_token`
     against it. Pick one, justify, and reject the other. **This is the heart of
     Phase 5; an unproven answer fails the green-gate.**
2. **Per-workspace live-command query.** The design names two mechanisms — "the
   observer index → ids" *and* `registry.live_values` (`registry.rs:138-149`).
   Reconcile them into one source of truth for "live interactive executions in
   workspace W" and justify it: the `NamespaceExecutionLedger` observer
   (operation-layer) carries `operation_name` + workspace association but not pgid;
   the registry carries the `CommandExecution` (pgid/cancel) but filtering by
   workspace requires `CommandExecution` to expose its workspace id. Specify the
   exact query: predicate on `live_values`, returning `(NamespaceExecutionId, pgid,
   cancel-handle)` (or the borrow shape that avoids cloning the execution), **with no
   second per-session map** (migration invariant). Address the borrow/lock question:
   `live_values` runs its closure under the registry mutex — what may the closure
   touch, and does inspecting/cancelling happen inside or outside the lock?
3. **Cancel + resume application path, mirror-free.** Today resume does
   `active.process.cancel_process()` + `lifecycle_state = Cancelled` (or `Running`)
   under `update_active` (`quiesce.rs:195-200`). Phase 3 deletes
   `CommandLifecycleState`/`CancellationState`/`update_active`/`CommandProcessStore`.
   Specify the replacement: cancel via the `CommandExecution`/`InteractiveExecution`
   `cancel()` reached through the registry by id; lifecycle state is gone (it was
   write-only per the disposition table — confirm, and confirm the observer's
   `NamespaceExecutionLifecycle` is the only surviving lifecycle surface, unchanged
   by Phase 5). Pin idempotency: `resume()` is callable from the explicit `finish()`
   path *and* from `Drop` (`:206-210`) — a double resume must not double-cancel or
   panic.
4. **The `ProcessGroupInspection` embed; delete `merge_report`.** "Embed" can mean
   *contain a field* (`pg: ProcessGroupInspection`) or *accumulate into one*. The
   current `merge_report` **sums** across multiple process groups
   (`+=` on counts, `|=` on bools, first-wins on `blocked_reason`/`detail`), so the
   embed must preserve summation across N commands, not just hold one. Specify the
   exact target shape of `CommandRemountInspection`, how the per-pgid
   `ProcessGroupInspection`s accumulate without `merge_report`, and prove
   `can_live_remount()` (`:50-57`) computes the **same boolean** as today. If
   `CommandRemountInspection` is serialized or compared in any test/observability
   path, the embed must keep that shape stable (or you must update the readers — list
   them).
5. **Registry-query races.** Between "list live commands in W" and acting on each:
   a command may go **terminal** (watcher completes it — `is_live` flips), or a new
   command may be **admitted** into W after the snapshot. Specify the semantics: is
   the affected-id set a snapshot taken once at `begin` (today's behavior — it
   iterates a `Vec` of ids captured up front), and what happens to a command that
   completes between capture and freeze (the freeze/`inspect_command_process_group`
   on a dead pgid), or to a newly-admitted command the quiesce never saw? Argue this
   matches today's observable behavior (today the up-front `active_command_session_ids…`
   snapshot has the same property) — or, if it changes, flag it loudly as a
   preserved-behavior deviation.
6. **Coordinator → registry wiring.** `begin_workspace_remount_quiesce` is
   `impl … for CommandOperationService`. After Phase 3 that service holds
   `Arc<NamespaceExecutionEngine>` (and through it the registry) instead of
   `process_store()`. Specify the accessor the coordinator uses, confirm
   `CommandRemountQuiesce` no longer stores `Arc<CommandProcessStore>` (it holds the
   registry/engine handle or just the captured id-set + a way to reach `cancel()`),
   and confirm the `CommandRemountCoordinator` trait signature
   (`core.rs:9-13`) is unchanged so the driver and `WorkspaceRemountService` are
   untouched.

## Required structure of `phase-5-spec.md` (assign every normative requirement a stable id `P5-Rn`)

1. **Objective & non-goals** (explicitly: engine, launcher, start-ack, mount path,
   daemon, and the `CommandRemountCoordinator` trait signature are out of scope).
2. **Consumed Phase 3 API** + the exists-today / Phase-3-adds / Phase-5-adds
   classification table, with every divergence from current code flagged and every
   Phase-3 obligation Phase 5 depends on (workspace-id on `CommandExecution`, pgid,
   `cancel()`) called out as a contract.
3. **The stale-resume invariant** — the centerpiece. Resolve Hard problem 1 for
   *both* same-coordinator and cross-coordinator cases, with a **Rejected
   alternatives** note (id-set-only vs. per-execution owner token vs.
   serialize-by-admission). Include a short interleaving proof/table:
   `A captures C → B captures C → A resumes` must leave C owned by B; and the
   symmetric cancel case.
4. **The live-command query** — the chosen single mechanism (Hard problem 2), the
   exact `live_values` predicate + return shape, the lock-scope rule (what runs
   under the registry mutex), and why no second per-session map is needed.
5. **Target design** — full Rust definitions of the reshaped
   `CommandRemountInspection` (with `ProcessGroupInspection` embedded), the trimmed
   `CommandRemountQuiesce` (fields after dropping `process_store` and the
   mirror-write paths), and any owner-token plumbing. Each item justified against the
   one it replaces; `merge_report` shown deleted with its accumulation folded in.
6. **Cancel/resume/lifecycle path** — resolve Hard problem 3: cancel via registry
   `cancel()`, lifecycle-state writes deleted (confirmed write-only), resume
   idempotency across explicit `finish()` and `Drop`.
7. **File-by-file change plan** — every Edit / Delete with before→after and why:
   - `quiesce.rs`: embed `ProcessGroupInspection`, delete `merge_report`, drop the
     `process_store`/mirror-write code from `set_switch_state`/`resume_command_records`,
     rewire cancel/resume to the registry.
   - `coordinator.rs`: replace `process_store()` calls with the registry query;
     remove the `update_active` mirror plant; own the token + affected-id set.
   - wiring edit to reach the registry from `CommandOperationService` (name it).
   Pair each deletion (`merge_report`, the two mirror fields' writes/reads) with
   evidence it has no surviving reader.
8. **Safe edit order** — the sequence that keeps the crate building at each step,
   accounting for the fact that the mirror fields themselves are deleted by **Phase
   3** (state the dependency: Phase 5 lands after Phase 3; if Phase 3 is incomplete
   in-tree, say what Phase 5 assumes is already gone).
9. **Invariants preserved** — table: invariant → mechanism → test. Must include:
   quiesce still holds/cancels live commands; **stale resume does not cancel a
   newer quiesce's command**; `can_live_remount()` boolean unchanged; blocked-reason
   precedence + `RemountBlockReason` strings unchanged; the driver's observed
   `CommandRemountInspection` (counts, ids, pgids) byte-stable; no second per-session
   map; no revived `CommandProcessStore`; observability surface untouched.
10. **Test plan** — which existing quiesce/resume tests keep passing, which move,
    which are new (especially a **direct test of the cross-coordinator stale-resume
    race** — two overlapping quiesces on one command). Honor the repo rule: **no
    inline tests in production sources**; unit tests live in integration suites
    (this repo recently relocated them — match that). Name the fakes needed (a fake
    registry / fake `CommandExecution` exposing pgid+cancel, a fake
    `ProcessGroupController`) and where they live under `tests/`.
11. **Verification** — exact commands: `cargo fmt --check`; `cargo test -p
    sandbox-runtime` (the remount/quiesce suites); `cargo clippy --all-targets
    --no-deps -- -D warnings`; absence-greps proving the cut, e.g.
    `rg -n "fn merge_report|remount_cancellation|remount_switch_state|CommandProcessStore"
    crates/sandbox-runtime/operation/src/workspace_remount` → gone (scoped to the
    remount module; note any legitimate residual elsewhere).
12. **Requirements traceability matrix** — `P5-Rn` → design element → test → verify
    command.
13. **Risks & open decisions** — with a recommended resolution each (top risk: the
    cross-coordinator owner-token decision in §3).
14. **Definition of done & LOC delta** (target ≈ −27; `quiesce` −24, `coordinator`
    −3 per the design scorecard — reconcile with your actual plan).

## Design constraints the spec must honor (from `CLAUDE.md`)

- **SRP/SOLID;** the coordinator depends on the engine registry's narrow query
  surface (`live_values`/`with_value`/`cancel`), never its internals; remount
  scratch state lives on the coordinator, not smeared back into per-command records.
- **Prefer less** — Phase 5 is a net deletion: no new store, no second per-session
  map, no field an engine/registry type already carries. If you must add a
  per-execution owner token, it is **one** `Option<RemountCancellationToken>` on the
  existing `CommandExecution`, justified explicitly — not a new structure.
- **No re-complication** (migration invariant): no revived `CommandProcessStore`
  source of truth, no `execution_kind`/`backing` axis, no shims/aliases/dual-write,
  no observer-surface change.
- **No inline comments in production code;** `///` on public items only. The spec
  may show illustrative code, but the design it prescribes must obey this.

## Quality bar (apply as a self-review gate before reporting; fix every miss)

- Could a competent implementer build this with **zero** clarifying questions? If
  not, name the underspecified section and fix it.
- Is the stale-resume invariant (Hard problem 1) resolved for **both**
  same-coordinator and cross-coordinator cases, with a concrete owner-of-C location
  and an interleaving proof — not an aspiration?
- Is the per-workspace query specified down to the `live_values` predicate, return
  shape, and lock scope, with no second per-session map?
- Is every deletion (`merge_report`, the two mirrors, the lifecycle/cancellation
  writes) proven to have no surviving reader?
- Is every claim tagged grounded (`file:line`) or assumed, and is each "Phase-3
  end state" anchor clearly distinguished from present-tense code?
- Does anything contradict the design or migration docs (especially the
  "token-on-coordinator + id-set preserves this" green-gate)? Deviations argued and
  flagged for human review?

## Report back

Return: the path written; a 10–15 line section outline; your resolution of the
stale-resume invariant (same- and cross-coordinator) in a few sentences, naming
where the per-execution owner now lives; the chosen live-command query mechanism;
the exists-today / Phase-3-adds / Phase-5-adds split for the consumed registry +
`CommandExecution` surface; the top 3 risks/open decisions; and every contradiction
or ambiguity you found across the three sources (design vs. migration vs. code, and
any Phase-3 obligation not yet met). Do not commit or push.
