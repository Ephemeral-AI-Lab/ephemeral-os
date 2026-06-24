# Phase 5 Spec — Remount Coordinator onto Engine Queries

Status: ready-to-implement. Authority: refines
[`migration-phases.md`](./migration-phases.md) §"Phase 5" and
[`namespace-execution.md`](../namespace-execution.md) (design of record). Consumes
the Phase-3 end state per [`phase-3-spec.md`](./phase-3-spec.md). Where this spec
diverges from a source doc, the divergence is **flagged** (search `⚠`) and argued;
nothing is silently contradicted.

Every normative requirement carries a stable id `P5-Rn` (§14 traces each to a
design element, a test, and a verify command).

Citations are tagged **grounded** (`file:line`, verified this session) or
**assumed** (listed, justified). Phase-3-end-state anchors — code that does *not
yet exist in the tree* — are tagged **(P3)** to distinguish them from present-tense
code **(now)**.

---

## 0. Reading guide — the one hard problem

Phase 5 is ≈ **−27 LOC**. Its difficulty is entirely one proof: **deleting the
per-command `remount_cancellation` mirror must not break the safety property that a
*stale resume does not cancel a command owned by a newer quiesce*** (the migration
green-gate, `migration-phases.md:224-225`). Today that safety is enforced by an
`Arc::ptr_eq` (`same_token`) check against a token mirrored onto every captured
command. Phase 5 removes the mirror. §3 re-derives the property from
**coordinator-owned state + a workspace-level serialization invariant**, and proves
the mirror was redundant. Everything else (§4–§16) is mechanical.

---

## 1. Objective & non-goals

**Objective.** Re-express the remount quiesce/resume coordinator on the engine
registry: the coordinator owns exactly **one** `RemountCancellationToken` + an
**affected-id set**, queries the registry for live interactive command executions
in a workspace, embeds `ProcessGroupInspection` into `CommandRemountInspection`,
and deletes `merge_report` and the per-command `remount_*` mirrors. Net deletion;
no new store, no second per-session map.

**In scope (the only files this spec changes):**

- `operation/src/workspace_remount/service/command/quiesce.rs`
- `operation/src/workspace_remount/service/command/coordinator.rs`
- one **wiring** addition: an `engine()` accessor on `CommandOperationService`
  (`operation/src/command/service/core.rs`) — `P5-R13`.
- one **innards** line in the driver
  `operation/src/workspace_remount/service/impls/remount_workspace_session.rs`
  (the `blocked_reason` read path moves with the embed) — `P5-R8`.

**Non-goals (explicitly out of scope; this spec must not touch them):**

- The engine, launcher, watcher thread, PTY, start-ack handshake, the mount path
  (Phase 4), and the daemon.
- The `CommandRemountCoordinator` trait **signature** (`core.rs:9-13`) — unchanged,
  so `WorkspaceRemountService` and the driver's method-call sequence are unchanged
  (`P5-R12`).
- The observability surface (`NamespaceExecutionLedger` /
  `active_namespace_executions`) — untouched (`P5-R16`).
- `command/src/process_group.rs` — the per-process-group inspection logic is
  **unchanged** (`P5-R7`); `ProcessGroupInspection` is *consumed*, not edited.
- `RemountBlockReason` strings, `RemountSwitchState`, the
  `RemountCancellationToken` value type's public method set (minus the now-dead
  `same_token`, `P5-R11`).

---

## 2. Consumed Phase-3 API (the contract) + classification

Phase 5 sits **on top of** Phase 3 (`migration-phases.md:49-51`: dep order
`… → {3,4} → 5`). The tree is mid-migration and **may not compile** until Phase 3
lands; several anchors below are Phase-3 *obligations*, not present code.

### 2.1 Engine registry — present today (Phase 2)

Grounded in `crates/sandbox-runtime/namespace-execution/src/`:

| Surface | Where | Notes |
|---|---|---|
| `ExecutionRegistry::live_values<R>(&self, f: impl Fn(&V)->Option<R>) -> Vec<R>` | `registry.rs:141-149` **(now)** | runs `f` **under the registry mutex**, filters `!terminal`, collects `Some`. Comment `registry.rs:138-140` names it "the Phase-5 hook." |
| `ExecutionRegistry::with_value<R>(&self, id, f: impl FnOnce(&V)->R) -> Option<R>` | `registry.rs:114-120` **(now)** | runs `f` under the mutex; `None` iff no entry/value. Entries are **not** removed on `complete` (`registry.rs:96-111`), so `with_value` succeeds for a just-live id even after it goes terminal. |
| `NamespaceExecutionEngine::{with_value,live_values}` forwarders | `engine.rs:81-87` **(now)** | the coordinator reaches the registry **through the engine**; no raw-registry accessor is required. |
| `PtyMaster::pgid(&self) -> Option<i32>` | `pty.rs:53-55` **(now)** | the process-group id is already retained on the PTY master. |

### 2.2 `CommandExecution` — the registry value `V` (Phase-3-adds)

`CommandExecution` is the Phase-3 registry value (`phase-3-spec.md:198-230`
**(P3)**), `command/src/command_execution.rs`. Phase 5 consumes:

| Item Phase 5 needs | `CommandExecution` exposes it? | Disposition |
|---|---|---|
| workspace association for per-workspace filtering | **yes** — `workspace_session_id(&self) -> &WorkspaceSessionId` (`phase-3-spec.md:208,219`) **(P3)** | use it; the query filters on `== ws`. |
| cancel handle | **yes** — `cancel(&self)` → `exec.cancel` → `killpg` (`phase-3-spec.md:221`; `execution.rs:70-72`→`pty.rs:109-111,167-172` **(now)**) | use it via `with_value`. |
| process-group id | **NO** ⚠ — §3.2 of phase-3-spec does **not** list a `process_group_id()` getter | **consumed-contract gap**, see §2.4. |
| workspace **root path** (for `inspect_command_process_group(pgid, &Path)`) | **NO** — `CommandExecution` carries `workspace_session_id`, not `workspace_root` | resolved once from the workspace session, see §2.5 (no new field). |

| Service wiring | State | Disposition |
|---|---|---|
| `CommandOperationService` holds `Arc<NamespaceExecutionEngine<CommandExecution>>` | **Phase-3-adds** (`phase-3-spec.md:814` **(P3)**, replacing `process_store`) | Phase 5 adds a `pub(crate) fn engine()` accessor (`P5-R13`). |

### 2.3 exists-today / Phase-3-adds / Phase-5-adds split

| Capability | Class | Anchor |
|---|---|---|
| `live_values` / `with_value` / `is_live` on the registry+engine | **exists-today** | `registry.rs:114-149`, `engine.rs:81-87` **(now)** |
| `PtyMaster::pgid()` | **exists-today** | `pty.rs:53-55` **(now)** |
| `CommandExecution` value with `workspace_session_id()` + `cancel()` | **Phase-3-adds** | `phase-3-spec.md:205-229` **(P3)** |
| `CommandOperationService.engine` field; `commands` typed view; reverse lookup via `engine.live_values` | **Phase-3-adds** | `phase-3-spec.md:814,360` **(P3)** |
| coordinator/quiesce already off `CommandProcessStore`, mirrors already dropped, cancel via `commands.with_value(id,|c| c.cancel())`, query via `engine.live_values` | **Phase-3-adds (forced)** ⚠ | `phase-3-spec.md:848-880` §7.7 **(P3)** — see §2.6 contradiction |
| `CommandExecution::process_group_id() -> Option<i32>` (+ `InteractiveExecution::pgid()` forwarder) | **Phase-5-adds (or a Phase-3 fix)** ⚠ | §2.4 |
| embed `ProcessGroupInspection`; delete `merge_report`; finalize token-on-coordinator + affected-id set; prove stale-resume safety | **Phase-5-adds** | this spec §3–§7 |

### 2.4 Consumed-contract gap — `process_group_id()` ⚠ (P5-R3)

The coordinator needs the pgid for two things it does today
(`coordinator.rs:54,60,80,84` **(now)**): (a) freeze/inspect via
`controller.inspect_command_process_group(pgid, root)` and (b) record
`held_process_group_ids` for `controller.resume_process_group_id(pgid)`. The
Phase-3 `CommandExecution` (§3.2) does not expose a pgid, yet phase-3-spec §7.7
**requires** the coordinator to do "the process-group work it already does" through
the registry — an **internal inconsistency in phase-3-spec** ⚠. The design intends
this reach (`namespace-execution.md:485-486`: "→ `InteractiveExecution`
pgid/cancel").

**Resolution (minimal, additive).** Surface the already-retained pgid:

```rust
// crates/sandbox-runtime/namespace-execution/src/execution.rs  — InteractiveExecution<T>
pub fn pgid(&self) -> Option<i32> { self.pty.pgid() }   // forwards PtyMaster::pgid() (pty.rs:53)

// crates/sandbox-runtime/command/src/command_execution.rs  — CommandExecution
pub fn process_group_id(&self) -> Option<i32> { self.exec.pgid() }
```

Two thin forwarders over an already-present field; **no behavior change**. This is
a **Phase-3 contract obligation Phase 5 depends on** — it belongs in the Phase-3
`CommandExecution` surface. If Phase 3 ships without it, Phase 5 adds these two
forwarders as unavoidable wiring (they live in the `namespace-execution` and
`command` crates, not in the two Phase-5 files). Flagged as the top consumed-gap;
acceptance: Phase 5's coordinator must be able to write
`c.process_group_id()` against the registry value.

### 2.5 workspace_root — resolve once, no new field (P5-R4)

`inspect_command_process_group` needs `workspace_root: &Path`
(`process_group.rs:115-119` **(now)**). Today the coordinator reads it per command
from `active.workspace_root` (`coordinator.rs:51` **(now)**), which was captured at
launch from the session handle (`exec_command.rs:286` **(now)**:
`handler.handle.workspace_root.clone()`).

**Resolution.** All commands the quiesce captures belong to the **one** workspace
`W` the driver is remounting, so they share one root. The coordinator resolves it
**once** from the workspace session:

```rust
let workspace_root = self
    .resolve_workspace_session(workspace_session_id.clone())   // core.rs:186-191 (now)
    .map(|handler| handler.handle.workspace_root)
    .ok();                                                      // None ⇒ defensive block (see §3.4)
```

`resolve_session` does **not** gate on remount-pending (it returns
`session.handler()` directly, `resolve_session.rs:7-18` **(now)**), so it succeeds
during the `RemountPending` window. `workspace_root` is **immutable for the session
lifetime** — `refresh_after_capture` / `refresh_after_publish` /
`refresh_from_handle` never write `handle.workspace_root`
(`model.rs:104-121` **(now)**) — so resolving once equals each captured command's
root. No `workspace_root` field is added to `CommandExecution` (prefer less; the
root is workspace state, not per-command state).

### 2.6 Contradiction: who deletes the mirrors — Phase 3 or Phase 5? ⚠

- `migration-phases.md:220-221` assigns deleting `remount_cancellation` /
  `remount_switch_state` to **Phase 5**.
- `phase-3-spec.md:848-880` §7.7 **forces** their deletion into **Phase 3**: the
  mirror *fields* live on `ActiveCommandProcess`, which is deleted with
  `CommandProcessStore` in Phase 3; the writes/reads in coordinator/quiesce would
  not compile, so Phase 3 makes "minimal edits" — route cancel via
  `commands.with_value(id, |c| c.cancel())`, query via `engine.live_values`, and
  **drop the write-only mirror writes + the `same_token` mirror read**.

**Reconciliation (this spec's stance).** Phase 5's *"today"* is the **Phase-3 end
state**: the mirror fields are already gone, the store dependency already severed,
cancel/query already on the registry. What phase-3-spec §7.7 explicitly **defers to
Phase 5** (`:877-880`) is the residual: *embed `ProcessGroupInspection`, delete
`merge_report`, and finalize+**prove** the token-on-coordinator + affected-id-set
design.* phase-3-spec §7.7 **assumes** the mirror is redundant; **Phase 5 is where
that assumption is proven** (§3) and the data model is consolidated. Net: no
contradiction in outcome, only in attribution — surfaced here, resolved by treating
§3 as the proof obligation that licenses §7.7's deletion.

### 2.7 Contradiction: "observer index" vs `live_values` ⚠

`namespace-execution.md:485-486` names *two* mechanisms ("the observer index → ids"
and `InteractiveExecution` pgid/cancel). The observer
(`NamespaceExecutionLedger`) carries `operation_name` + workspace association but
**not** pgid; the registry value carries pgid/cancel. Using the observer would force
a second lookup and risk a second per-session map. **Resolved in favor of a single
mechanism: `engine.live_values` filtered on `CommandExecution::workspace_session_id`**
(§4), consistent with phase-3-spec's "`live_values` … workspace→cmd lookup; Phase 5"
(`:360` **(P3)**). The "observer index" wording is treated as superseded.

---

## 3. The stale-resume invariant (the centerpiece) — P5-R1, P5-R2

### 3.1 Today's protection, exactly (grounded)

1. Coordinator mints one token and **mirrors** it onto every captured command:
   `update_active(|active| { …; active.remount_cancellation = Some(cancellation); … })`
   (`coordinator.rs:62-70` **(now)**).
2. `set_switch_state` writes the per-command `remount_switch_state` **only if**
   `active.remount_cancellation.same_token(self.cancellation)`
   (`quiesce.rs:138-153` **(now)**).
3. `resume_command_records` clears the mirror and cancels/marks **only if**
   `same_token` matches (`quiesce.rs:181-203` **(now)**), where `same_token` is
   `Arc::ptr_eq` (`quiesce.rs:94-97` **(now)**).

The interleaving this *claims* to defend: quiesce **A** captures `C`; later quiesce
**B** re-captures `C`, overwriting `C`'s mirror with B's token; then **A** resumes —
A's `same_token` no longer matches, so A leaves `C` to B.

### 3.2 Where the "current owner of C" lives after the mirror is deleted

**Same-coordinator (mechanical).** A single `CommandRemountQuiesce` owns one
`cancellation: RemountCancellationToken` and one captured **affected-id set**
(`affected: Vec<NamespaceExecutionId>`) for its whole lifetime.
`set_switch_state` mutates only `self.switch_state`; `resume` acts only on
`self.held_process_group_ids` and `self.affected`. Idempotency across the explicit
`finish()` path **and** `Drop` (`quiesce.rs:206-210` **(now)**) is preserved by the
**`switch_state == Finished` early-return** (`quiesce.rs:166-168` **(now)**) plus
`held_process_group_ids.drain(..)` (`:172`): the first `resume` sets `Finished`; the
second returns immediately, so cancel/SIGCONT run **at most once**. The mirror's
`same_token` was *not* what provided idempotency — the `Finished` guard already does.
(P5-R2.)

**Cross-coordinator (the proof).** "B re-captures C" requires two live quiesces for
the same workspace `W` overlapping in time. **This is impossible**, by a
workspace-level serialization that spans the *entire* remount critical section:

- The driver calls `self.workspace.begin_remount(W)?` **before**
  `begin_workspace_remount_quiesce(W)` and returns early on `Err`
  (`remount_workspace_session.rs:12-15` **(now)**).
- `begin_remount` sets `remount_state = RemountPending`, and a second
  `begin_remount` on a still-pending session returns `Err(RemountAlreadyPending)`
  (`model.rs:72-82`; `is_pending` check `:75-79` **(now)**).
- `RemountPending` is cleared **only at the very end** of the driver — via
  `apply_and_finish_remount` → `commit_remount_result` → `finish_remount`
  (success, `model.rs:84-92`) or `block_remount`/`block_remount_if_pending`
  (blocked/error, `model.rs:94-102`, `apply_and_finish_remount.rs:45-72`) **(now)**.
- `begin_workspace_remount_quiesce` has exactly **one** production caller — the
  driver (verified: `rg begin_workspace_remount_quiesce` → trait `core.rs`, impl
  `coordinator.rs`, call `remount_workspace_session.rs` only).

Therefore, between A's `begin_remount(W)` and A's terminal commit, **any** B calling
`remount_workspace_session(W)` fails at `begin_remount` and **never constructs a
quiesce for W**. A command `C` belongs to exactly one workspace (its
`workspace_session_id` is fixed at launch). Hence **at most one live token ever
targets `C`**, and the "current owner of C" is simply *the single live quiesce's
token on the coordinator* + its captured `affected` set. The per-command mirror
encoded a fact that the workspace-level guard already makes unambiguous — it was
**redundant**. (P5-R1.)

> **Admission-guard scope (do not confuse with the above).** The
> `workspace_lifecycle_admission` mutex (`core.rs:166-172` **(now)**) is acquired in
> `begin_workspace_remount_quiesce` (`coordinator.rs:17`) and bound to a **local**
> that drops when `begin_*` returns — so it serializes only the **begin/snapshot
> phase**, *not* the critical-switch window. Its real job is to serialize the
> snapshot against concurrent `exec_command` **admission** (`exec_command.rs:57,181`
> acquire the same mutex; phase-3-spec `:429` holds it across
> `run_shell_interactive + attach`), which is what makes the live-command snapshot
> consistent (§5). It is **not** the cross-coordinator serializer — `begin_remount`
> is.

### 3.3 Interleaving proof table

`W` a workspace, `C` a live command in `W`, `A`/`B` two remount attempts.

| Step | Today (mirror) | Phase 5 (no mirror) |
|---|---|---|
| A `begin_remount(W)` | `RemountPending` set | `RemountPending` set |
| A captures `C` (token A) | `C.mirror = A` | `A.affected ∋ C.id`; token A on A |
| B `begin_remount(W)` | **`Err(RemountAlreadyPending)`** → B returns; **B never captures `C`** | **`Err(RemountAlreadyPending)`** → B returns; **B never captures `C`** |
| A resumes | `same_token(C.mirror=A, A)` ⇒ true ⇒ A resumes/cancels `C` | `Finished`-guarded resume over `A.affected` ⇒ A resumes/cancels `C` |
| Outcome | A correctly owns `C` to the end | **identical** |

The mirror branch "A's `same_token` no longer matches → A leaves `C` to B" is
**unreachable** in both columns, because B is rejected at `begin_remount` before it
can re-stamp `C`. The symmetric **cancel** case (A's token is `request_cancel`-ed,
then A resumes) behaves identically: `resume` cancels exactly `A.affected` via the
registry (§6); no other quiesce exists to be affected.

> **Note (grounded):** `RemountCancellationToken::request_cancel()` has **zero
> callers** in the tree today (`rg request_cancel` → definition only,
> `quiesce.rs:85`). The live command-cancel is the **independent** Ctrl-C path in
> `write_command_stdin`, which calls `cancel_process()`/`cancel()` directly and
> never touches the token (`write_command_stdin.rs:31-42` **(now)**). So the
> token-cancel→resume branch is currently *latent* public capability. Phase 5
> **preserves** it (the green-gate "quiesce/resume still cancels/holds live
> commands") and the new unit test (§12) is what exercises it deterministically.

### 3.4 Rejected alternatives (cross-coordinator owner)

- **(B) Per-execution owner token** — one `Option<RemountCancellationToken>` on the
  registry-stored `CommandExecution`, re-stamped under the registry lock, with
  `same_token` re-derived against it. **Rejected:** it re-introduces a per-command
  remount mirror under a new name — exactly what the design deletes
  (`namespace-execution.md:471`) — to defend an interleaving that the
  `begin_remount` guard already makes impossible. Pure cost, no benefit.
- **(C) id-set only, *without* the §3.2 proof** — assert the affected-id set is
  enough but skip the impossibility argument. **Rejected:** unproven; fails the
  green-gate, which demands the property be *preserved*, not assumed.
- **(A, chosen) Serialize-by-`begin_remount` + coordinator-owned token + affected
  id-set.** The workspace `RemountPending` state is the serializer; the proof in
  §3.2 licenses deleting the mirror with zero new per-execution state.

---

## 4. The live-command query — P5-R5, P5-R6

### 4.1 Single mechanism + predicate + return shape

The coordinator's snapshot of "live interactive command executions in workspace
`W`" is **one** `engine.live_values` call, filtered on the registry value's
workspace id:

```rust
// inside begin_workspace_remount_quiesce, under the admission guard (§3.2 note)
let mut live: Vec<(NamespaceExecutionId, Option<i32>)> = self.engine().live_values(|c| {
    (c.workspace_session_id() == workspace_session_id)
        .then(|| (c.id().clone(), c.process_group_id()))
});
live.sort_by(|a, b| a.0 .0.cmp(&b.0 .0));   // deterministic order (replaces today's sorted snapshot, coordinator.rs:88-91)
```

- **Predicate:** `c.workspace_session_id() == W`. **Projection:**
  `(c.id().clone(), c.process_group_id())` — the id (for cancel-by-id and for the
  `CommandSessionId` face) and the pgid (for inspect/resume). The
  `CommandExecution` is **not** cloned out.
- **No second per-session map** (migration invariant): the registry's own
  `entries` map is the only index; the per-workspace filter is a predicate over it.

### 4.2 Lock-scope rule (P5-R6)

`live_values` runs its closure **under the registry mutex** (`registry.rs:141-149`).
The closure above touches only cheap, non-re-entrant reads —
`workspace_session_id()`, `id()`, and `process_group_id()` (a stored
`Option<i32>`); it performs **no** syscalls, **no** re-entry into the registry,
**no** blocking. Everything expensive happens **after** `live_values` returns,
outside the lock:

- `inspect_command_process_group` (SIGSTOP + `/proc` reads + up-to-500 ms freeze
  wait, `process_group.rs:167-210`) — **outside** the lock.
- `resume_process_group_id` (SIGCONT) — **outside** the lock (the controller is
  independent of the registry).
- cancel — `engine.with_value(&id, CommandExecution::cancel)` runs `cancel()`
  *under* the lock (§6); this matches today's "lock held across cancel" and is the
  one deliberate in-lock action (§13 R3).

### 4.3 Why no observer / second map is needed

Filtering on the registry value's `workspace_session_id` gives the workspace→cmd
reverse lookup directly; the observer (which lacks pgid) is not consulted. This is
the same source of truth Phase 3 uses for `with_workspace_destroy_admission`'s
reverse lookup (`phase-3-spec.md:814` "the reverse lookup now iterates
`engine.live_values`" **(P3)**), so the two reverse-lookup sites share one
mechanism.

---

## 5. Registry-query races — P5-R6 (preserved-behavior analysis)

The affected-id set is a **snapshot taken once at `begin`**, under the admission
guard — identical in spirit to today's up-front
`active_command_session_ids_for_workspace_session` (`coordinator.rs:18-20` **(now)**,
also one snapshot). Three races, and how Phase 5 behaves vs today:

| Race | Today | Phase 5 | Verdict |
|---|---|---|---|
| Command **admitted** into `W` after the snapshot | invisible to this quiesce (admission mutex held across snapshot; new `exec_command` blocks on the same mutex, phase-3-spec `:429`) | **same** — `live_values` runs under the admission guard, and `exec_command` admission holds that guard across `run+attach` | **preserved** |
| Command goes **terminal** between snapshot and inspect | re-lookup `active(id)` misses → `ActiveCommandMissing` block (`coordinator.rs:44-49`) | the live set is captured atomically in one lock; the now-dead command's group is empty → inspection yields `process_membership_changed` (`process_group.rs:177-181`) → blocks live remount | **behavior refinement** ⚠ (same *outcome*: live remount blocked; different *reason string*) |
| `with_value(id, cancel)` after the command went terminal | killpg on a dead group — harmless `ESRCH` | **same** — entries aren't removed on `complete`, so `with_value` still reaches `cancel()`; killpg on a dead pgid is a no-op | **preserved** |

**⚠ Flagged deviation (P5-R6/R15):** `RemountBlockReason::ActiveCommandMissing` had
two sources in the coordinator — the post-snapshot re-lookup miss
(`coordinator.rs:44`) and the `update_active`-returns-`None` miss
(`coordinator.rs:71-76`). Both vanish in Phase 5 (no re-lookup; no `update_active`).
The atomic `live_values` snapshot **eliminates the snapshot-then-relookup window**
that produced `ActiveCommandMissing`; a command dying mid-quiesce now surfaces as the
inspection's `process_membership_changed`. **The enum variant and its string are
unchanged** (no string churn, satisfying the invariant), but the coordinator no
longer *produces* `"active_command_missing"`. No existing test asserts that string
(verified: `rg active_command_missing crates/.../operation/tests` → none; the asserted
strings are `"process_group_unavailable"`,
`command_remount.rs` / `workspace_remount.rs:626`). Recommended: keep the variant
defined (cheap, still reachable in principle for `pgid==None`+future paths) and
document the production change. If a reviewer prefers strict parity, a defensive
`with_value(&id, |_| ()).is_none() ⇒ ActiveCommandMissing` re-check can be added per
id at negligible cost — flagged as an option, not required.

---

## 6. Cancel / resume / lifecycle path, mirror-free — P5-R9, P5-R10

### 6.1 What is deleted (all proven write-only / dead)

- **Per-command lifecycle writes** — `lifecycle_state = QuiescedForRemount`
  (`coordinator.rs:66`), `= Cancelled` / `= Running`
  (`quiesce.rs:197,199`), and `cancellation = CancellationState::None`
  (`coordinator.rs:67`). The disposition table marks `lifecycle_state` /
  `cancellation` **write-only — DELETE** (`namespace-execution.md:469`); the
  surviving lifecycle surface is the observer's `NamespaceExecutionLifecycle`
  (`namespace_execution.rs`, `Starting/Running/Terminal`), **untouched by Phase 5**
  (grounded: it is written only by the ledger's observer impl; the
  `CommandLifecycleState` enum is a *different*, command-internal type deleted with
  the store in Phase 3). (P5-R10.)
- **The `remount_switch_state` mirror write** (`quiesce.rs:149`) — write-only
  (`namespace-execution.md:470`); the live copy is `self.switch_state`.
- **The `remount_cancellation` mirror** + every `same_token` gate
  (`quiesce.rs:147,189`, `coordinator.rs:68`) — redundant per §3.
- **`RemountCancellationToken::same_token`** itself (`quiesce.rs:94-97`) — its only
  callers are the two mirror gates; with them gone it has **zero callers** (grounded:
  `rg same_token` → def + the two gates only). Delete it. (P5-R11.)

### 6.2 What replaces resume's cancel

Today (`quiesce.rs:195-200` **(now)**): under `update_active`, `if
cancellation.is_cancelled() { active.process.cancel_process(); lifecycle = Cancelled }
else { lifecycle = Running }`. Phase 5:

```rust
fn resume_affected_commands(&self) {
    if !self.cancellation.is_cancelled() {
        return;                                  // non-cancel path: SIGCONT already done; no lifecycle write
    }
    for id in &self.affected {
        self.engine.with_value(id, CommandExecution::cancel);   // → killpg; None/terminal ⇒ harmless no-op
    }
}
```

- Cancel reaches the live command's `cancel()` **by id through the registry** —
  the design-blessed path (`namespace-execution.md:486`; phase-3-spec §7.7
  `commands.with_value(id,|c| c.cancel())`).
- **No lifecycle write** (deleted, §6.1). The terminal status of a cancelled command
  is observed by the watcher when the killed child exits → the ledger's
  `Terminal`, exactly as for any other exit.
- **Idempotency** (P5-R9): guarded by `resume`'s `switch_state == Finished`
  early-return + `held_process_group_ids.drain(..)`. A second `resume` (Drop after
  explicit `finish`) returns before reaching `resume_affected_commands`, so no
  double-cancel; and `cancel()`/killpg is itself idempotent on a dead group.

### 6.3 Resume control flow (target)

```rust
pub fn resume(&mut self) -> bool {
    if self.switch_state == RemountSwitchState::Finished {
        return self.inspection.process_group.resumed;
    }
    self.switch_state = RemountSwitchState::Resuming;          // set_switch_state, now just a field set
    let had_held = !self.held_process_group_ids.is_empty();
    let mut all_resumed = true;
    for pgid in self.held_process_group_ids.drain(..) {
        all_resumed &= self.controller.resume_process_group_id(pgid);   // SIGCONT, outside any lock
    }
    self.resume_affected_commands();                           // cancel-by-id iff token cancelled
    self.switch_state = RemountSwitchState::Finished;
    self.inspection.process_group.resumed |= had_held && all_resumed;
    all_resumed
}
```

The SIGCONT-then-cancel ordering matches today (`quiesce.rs:170-177` then
`resume_command_records`): a cancelled-and-held command is first un-frozen (so the
SIGTERM is deliverable), then killed.

---

## 7. Target design — full definitions

### 7.1 `CommandRemountInspection` (embed `ProcessGroupInspection`) — P5-R7, P5-R8

```rust
use sandbox_runtime_command::process_group::ProcessGroupInspection;   // already re-exported, mod.rs

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CommandRemountInspection {
    pub active_commands: usize,                       // coordinator-only (unchanged, top-level)
    pub command_session_ids: Vec<CommandSessionId>,   // coordinator-only (unchanged, top-level)
    pub process_group_ids: Vec<i32>,                  // coordinator-only (unchanged, top-level)
    pub process_group: ProcessGroupInspection,        // the embedded accumulator (the 12 former-shared fields)
}

impl CommandRemountInspection {
    #[must_use]
    pub fn can_live_remount(&self) -> bool {
        self.active_commands > 0
            && self.process_group.blocked_reason.is_none()
            && self.process_group.inspected
            && self.process_group.quiesce_attempted
            && self.process_group.quiesced_process_count == self.process_group.process_count
    }

    #[must_use]
    pub fn blocked_reason(&self) -> Option<String> {  // P5-R8: driver reads this (was a field)
        self.process_group.blocked_reason.clone()
    }

    /// Accumulate one process group's inspection into the embedded total
    /// (replaces the cross-type `merge_report`; now a homogeneous merge).
    pub(crate) fn accumulate(&mut self, report: ProcessGroupInspection) {
        let pg = &mut self.process_group;
        pg.process_count += report.process_count;
        pg.quiesced_process_count += report.quiesced_process_count;
        pg.pinned_cwd_count += report.pinned_cwd_count;
        pg.pinned_root_count += report.pinned_root_count;
        pg.pinned_fd_count += report.pinned_fd_count;
        pg.pinned_mapped_file_count += report.pinned_mapped_file_count;
        pg.mountinfo_checked_count += report.mountinfo_checked_count;
        pg.inspected |= report.inspected;
        pg.quiesce_attempted |= report.quiesce_attempted;
        pg.resumed |= report.resumed;
        if pg.blocked_reason.is_none() { pg.blocked_reason = report.blocked_reason; }
        if pg.detail.is_none() { pg.detail = report.detail; }
    }

    pub(crate) fn block_if_clear(&mut self, reason: RemountBlockReason) {
        self.process_group
            .blocked_reason
            .get_or_insert_with(|| reason.to_string());
    }
}
```

- **Summation preserved.** `accumulate` sums across the **N** captured commands
  exactly as `merge_report` did (7 `+=`, 3 `|=`, 2 first-wins) — the design's "embed
  must preserve summation, not just hold one" (Hard problem 4). It is invoked once
  per captured pgid (§8 coordinator loop), so `process_group` is the running total.
- **`can_live_remount` boolean identical.** `active_commands` stays top-level; the
  other four operands read the embedded `process_group`, which holds the *same
  accumulated values* the flat fields held before. Algebraically unchanged. (P5-R7.)
- **Blocked-reason precedence preserved.** `block_if_clear` (coordinator-level
  reasons) writes `process_group.blocked_reason` and is called **before** each
  `accumulate` (§8), so the coordinator's reason wins (first-wins), then per-pgid
  reasons, exactly as today (`coordinator.rs:47,57,75` precede `merge_report:82`).
- **Observed shape stable.** `WorkspaceRemountOutcome` derives only
  `Debug,Clone,PartialEq,Eq` — **no `serde`** (`core.rs:16`); `CommandRemountInspection`
  likewise. So embedding changes Rust field *paths*, not any serialized bytes. Tests
  read only `command_inspection.active_commands` (top-level), `.process_group_ids`
  (top-level), and `outcome.blocked_reason` (the `WorkspaceRemountOutcome` field,
  populated by the driver via `blocked_reason()`) — all stable. (Verified:
  `rg command_inspection\.` over the tests → `.active_commands` only.)

> **Why `accumulate` lives in `quiesce.rs`, not as `ProcessGroupInspection +=`
> in the command crate.** The orphan rule forbids `operation` from implementing
> `AddAssign` for the foreign `ProcessGroupInspection`, and adding a `merge` method
> to `command/src/process_group.rs` exceeds Phase 5's scope (`process_group.rs`
> unchanged, P5-R7). `accumulate` is therefore an operation-local helper. It still
> **deletes `merge_report`'s defining property** — the *cross-type, field-name-
> mismatched* translation between two different structs — leaving a homogeneous
> merge into one embedded value. A future cleanup may push it down as
> `ProcessGroupInspection::merge`; that is a command-crate change, deferred (§13 R5).

### 7.2 `CommandRemountQuiesce` (trimmed) — P5-R9..R13

```rust
use std::sync::Arc;
use sandbox_runtime_command::process_group::{ProcessGroupController, ProcessGroupInspection};
use sandbox_runtime_namespace_execution::NamespaceExecutionId;
use crate::command::CommandExecution;                       // Phase-3 registry value (P3)
use sandbox_runtime_namespace_execution::NamespaceExecutionEngine;

pub struct CommandRemountQuiesce {
    pub(crate) inspection: CommandRemountInspection,
    pub(crate) held_process_group_ids: Vec<i32>,
    pub(crate) affected: Vec<NamespaceExecutionId>,         // the affected-id set (cancel targets; pgid present)
    pub(crate) engine: Arc<NamespaceExecutionEngine<CommandExecution>>,   // reached only via with_value(cancel)
    pub(crate) cancellation: RemountCancellationToken,      // THE one token, on the coordinator
    pub(crate) switch_state: RemountSwitchState,
    pub(crate) controller: Arc<dyn ProcessGroupController>,
}
```

Field-by-field justification against what it replaces:

| Field | Replaces | Why |
|---|---|---|
| `affected: Vec<NamespaceExecutionId>` | `command_session_ids: Vec<CommandSessionId>` (`quiesce.rs:103`) **and** the `process_store` cancel-target role | the cancel targets are the pgid-bearing ids; held as `NamespaceExecutionId` for `engine.with_value`. The *observed* `Vec<CommandSessionId>` lives in `inspection.command_session_ids` (the superset of all live ids), derived `CommandSessionId(id.0.clone())`. |
| `engine: Arc<NamespaceExecutionEngine<CommandExecution>>` | `process_store: Arc<CommandProcessStore>` (`quiesce.rs:104`) | the narrow registry surface (`with_value`) for cancel-by-id; no store. |
| *(removed)* | the per-command mirror writes | redundant (§3, §6). |

`set_switch_state` collapses to `self.switch_state = state;` (no mirror loop).
`resume` per §6.3. `Drop` unchanged (`= self.resume()`). The `Debug` impl drops the
removed fields and gains `affected` (and continues `finish_non_exhaustive`). Public
methods `inspection()`, `cancellation()`, `switch_state()`, `set_switch_state`,
`cancellation_requested()`, `finish()`, `resume()` keep their **signatures**
(P5-R12).

`RemountCancellationToken` keeps `new/request_cancel/is_cancelled/clone` (all still
public surface); only `same_token` is removed (P5-R11). `RemountSwitchState`,
`RemountBlockReason` (+strings), `can_live_remount`'s contract: **unchanged**.

### 7.3 No owner-token plumbing

Per §3.4, **no** per-execution owner token is added. `CommandExecution` gains only
the `process_group_id()` getter (§2.4) — a read of an already-stored value, not new
state.

---

## 8. Coordinator rewrite — `begin_workspace_remount_quiesce`

Target (prose; preserves the `coordinator.rs` control flow and the trait signature):

1. `let _admission = self.begin_workspace_lifecycle_admission();` — **keep**
   (serializes the snapshot vs `exec_command` admission, §3.2 note, §5).
2. Resolve `workspace_root` **once** (§2.5). On the (driver-unreachable) `None`
   case, take the empty-set branch below with a defensive
   `block_if_clear(ActiveCommandMissing)` — documented as can't-happen in the driver
   flow.
3. `let live = self.engine().live_values(predicate)` (§4.1), sorted deterministically.
4. Construct the quiesce: `cancellation = RemountCancellationToken::new()`,
   `inspection.active_commands = live.len()`, `engine = Arc::clone(self.engine())`,
   `controller = self.remount_controller()`, empty `affected` /
   `held_process_group_ids`, `switch_state = Quiescing`.
5. If `live.is_empty()` → `switch_state = ReadyToSwitch`; return (matches
   `coordinator.rs:34-37`).
6. For each `(id, pgid)` in `live`:
   - `inspection.command_session_ids.push(CommandSessionId(id.0.clone()))`;
   - `let Some(pgid) = pgid else { block_if_clear(ProcessGroupUnavailable);
     continue }`;
   - `inspection.process_group_ids.push(pgid)`; `affected.push(id)`;
   - `let report = controller.inspect_command_process_group(pgid, root)`; `let held
     = report.blocked_reason.is_none()`; `inspection.accumulate(report)`; if `held`
     → `held_process_group_ids.push(pgid)`.
   *(The `update_active`-returns-`None` → `ActiveCommandMissing` branch
   `coordinator.rs:71-76` is removed — there is no `update_active`; §5.)*
7. `inspection.command_session_ids.sort(); .dedup();`
   `inspection.process_group_ids.sort_unstable(); .dedup();`
   (matches `coordinator.rs:88-91`).
8. `self.set_switch_state(ReadyToSwitch); if !inspection.can_live_remount() {
   self.resume(); }` (matches `coordinator.rs:92-95`).

The **mirror plant** (`coordinator.rs:62-77`) is gone entirely; the per-command
`update_active` is replaced by the registry projection + the inline inspect/accumulate.

---

## 9. File-by-file change plan

Each deletion is paired with its no-surviving-reader evidence.

### 9.1 `quiesce.rs` (229 → ~205)

| Edit | Before → after | Evidence / why |
|---|---|---|
| `CommandRemountInspection` | flat 16 fields → 3 coordinator-only + `process_group: ProcessGroupInspection` (§7.1) | only external reads are top-level `active_commands`/`process_group_ids`/`command_session_ids` + the driver's `blocked_reason` (now `blocked_reason()`). |
| `can_live_remount` / `block_if_clear` | read/write embedded `process_group.*` | §7.1; boolean identical. |
| add `blocked_reason()` + `accumulate()` | — | driver read path + summation. |
| **delete `merge_report`** (`:212-229`) | gone | its only caller is `coordinator.rs:82`, rewired to `accumulate`; no other reader (`rg merge_report` → def + that one call). |
| `CommandRemountQuiesce` fields | drop `process_store`, rename `command_session_ids`→`affected` (now `Vec<NamespaceExecutionId>`), add `engine` (§7.2) | `process_store` had no reader outside quiesce/coordinator (store deleted in Phase 3). |
| `set_switch_state` (`:138-153`) | mirror loop → `self.switch_state = state;` | `remount_switch_state` write-only (`namespace-execution.md:470`). |
| `resume` (`:165-179`) | unchanged shape; reads `process_group.resumed` | §6.3. |
| **delete `resume_command_records`** (`:181-203`) | → `resume_affected_commands` (§6.2) | mirror + lifecycle writes all write-only/dead (§6.1). |
| **delete `same_token`** (`:94-97`) | gone | zero callers after mirror removal (`rg same_token`). |

### 9.2 `coordinator.rs` (98 → ~95)

| Edit | Before → after | Evidence |
|---|---|---|
| live-command source | `process_store().active_command_session_ids_for_workspace_session` (`:18-20`) → `engine().live_values(predicate)` (§4.1) | the registry is the single source (§4.3). |
| root | per-command `active.workspace_root` (`:51`) → resolve once (§2.5) | workspace_root immutable per session (`model.rs:104-121`). |
| pgid | `active.process.process_group_id()` (`:54`) → `c.process_group_id()` in the projection | §2.4 getter. |
| **delete mirror plant** (`:62-77`) | gone | write-only/redundant (§3, §6.1). |
| inspect+fold | `merge_report(&mut …, report)` (`:82`) → `inspection.accumulate(report)` | §7.1. |
| imports | drop `CommandLifecycleState`/`CancellationState`/`CommandProcessStore`; add `NamespaceExecutionId`/engine | those types are deleted in Phase 3. |

### 9.3 wiring — `command/service/core.rs` (`P5-R13`)

Add (if Phase 3 has not already):
`pub(crate) fn engine(&self) -> &Arc<NamespaceExecutionEngine<CommandExecution>> { &self.engine }`.
The `engine` field is the Phase-3 replacement for `process_store`
(`phase-3-spec.md:814` **(P3)**). No other change.

### 9.4 driver — `impls/remount_workspace_session.rs` (`P5-R8`, 1 line)

`quiesce.inspection().blocked_reason.clone()` (`:17`) →
`quiesce.inspection().blocked_reason()`. Semantics identical;
`WorkspaceRemountOutcome.blocked_reason` byte-stable. The driver's
method-call **sequence and the trait** are otherwise unchanged (P5-R12).

### 9.5 consumed-contract additions outside Phase-5 files (flag; may be Phase-3)

- `InteractiveExecution::pgid()` + `CommandExecution::process_group_id()` (§2.4) —
  two forwarders. **Belongs to the Phase-3 contract**; Phase 5 hard-depends on it.

---

## 10. Safe edit order (build green at each step)

Dependency: **Phase 5 lands after Phase 3** (`migration-phases.md:49-51`). Phase 5
**assumes the Phase-3 end state is already in-tree**: `CommandProcessStore` /
`ActiveCommandProcess` — and therefore the `remount_*` mirror fields,
`CommandLifecycleState`, `CancellationState` — are **already deleted**; the
coordinator/quiesce already reach the registry (phase-3-spec §7.7); and
`CommandOperationService.engine` exists. If Phase 3 is incomplete in-tree, Phase 5
cannot build — land Phase 3 first. Crates compile bottom-up
(`namespace-execution → command → operation`).

1. **Consumed-contract forwarders (if absent, §2.4).** Add
   `InteractiveExecution::pgid()` (engine crate), then
   `CommandExecution::process_group_id()` (command crate). Both crates build —
   additive getters, no caller yet.
2. **`core.rs` accessor (§9.3).** Add `engine()` if Phase 3 didn't. `operation`
   builds.
3. **+ 4. `quiesce.rs` data model and `coordinator.rs` rewire — one commit.**
   Reshape `CommandRemountInspection` (embed), add `accumulate`/`blocked_reason`,
   trim `CommandRemountQuiesce`, rewrite `set_switch_state`/`resume`/
   `resume_affected_commands`, delete `merge_report` + `same_token` (§9.1); and
   rewire the coordinator to `live_values` + resolve-root + `accumulate` (§8, §9.2).
   These two files reference each other's field names, so they land together;
   `operation` builds at the commit boundary.
5. **Driver 1-liner (§9.4).** `operation` builds.
6. **Tests (§12).** Update any moved field path in the Phase-3 harness; add the
   three new tests.

No dual-write/shim at any step (migration ban, `migration-phases.md:34`): the mirror
is already gone (Phase 3), so there is no window where both a mirror and the
coordinator-owned token are live.

---

## 11. Invariants preserved

| Invariant | Mechanism | Test |
|---|---|---|
| Quiesce still **holds (freezes)** and **cancels** live commands | `live_values` snapshot → `inspect_command_process_group` (SIGSTOP) → `held_process_group_ids`; resume SIGCONT; cancel via `with_value(cancel)` (§4, §6) | `:501`, `:546`, 12.2 #2 |
| **Stale resume does not cancel a newer quiesce's command** | `begin_remount` `RemountPending` serializes the per-workspace remount lifetime; one token + affected-id set on the coordinator (§3) | 12.2 #1 |
| `can_live_remount()` boolean unchanged | embed reads the same accumulated values; `active_commands` stays top-level (§7.1) | 12.2 #3, `:501` |
| Blocked-reason precedence + `RemountBlockReason` strings unchanged | `block_if_clear` precedes `accumulate` (first-wins); enum + strings untouched (§7.1) | `:600`/`:626` |
| Driver's observed `CommandRemountInspection` (counts/ids/pgids) + `WorkspaceRemountOutcome` byte-stable | top-level coordinator fields unchanged; embed has no `serde`; driver reads `blocked_reason()` (§7.1, §9.4) | all remount tests |
| Resume idempotent across explicit `finish()` + `Drop` | `switch_state == Finished` guard + `held.drain(..)` (§6) | 12.2 #2, `:546` |
| No second per-session map | single registry `entries` map + a predicate (§4) | absence greps (§13) |
| No revived `CommandProcessStore` | `quiesce`/`coordinator` hold the engine handle only (§7.2) | `rg CommandProcessStore … workspace_remount` |
| Observability surface untouched | ledger/observer never referenced by Phase-5 files (§1, §6.1) | `observability_snapshot.rs` |
| `CommandRemountCoordinator` trait + driver call sequence unchanged | trait signature fixed; only innards change (§1, §7.2) | `service_graph.rs`, all remount tests |

---

## 12. Test plan

Repo rule honored: **no inline `#[cfg(test)]` tests in `src/`**; all tests live in
`crates/sandbox-runtime/operation/tests/` (the existing
`command_remount.rs` / `workspace_remount.rs` integration suites), exercising the
**public** `WorkspaceRemountService` + `CommandRemountCoordinator` surface.

> **Phase-3 prerequisite.** `command_remount.rs` / `workspace_remount.rs` /
> `support/mod.rs` are **rewritten in Phase 3 §12** to inject a fake engine launcher
> (a `CommandExecution` with a scriptable pgid) in place of the deleted
> `CommandLaunchDriver`/`InactiveLaunchDriver` (`phase-3-spec.md:1109-1158` **(P3)**).
> Phase 5 builds on that harness; the assertions below are stated against the
> *public outcome*, which Phase 3 preserves.

### 12.1 Keep passing (behavior-stable; possibly literal field-path updates)

- `workspace_remount_no_active_command_path_succeeds_and_clears_pending`
  (`:452`) — `active_commands == 0`, `remounted`, pending cleared.
- `workspace_remount_isolated_no_active_command_path_…` (`:403`).
- `workspace_remount_live_command_success_finishes_before_resume` (`:501`) —
  `active_commands == 1`, `resumed() == vec![101]`, `resume_pending() == vec![false]`.
- `workspace_remount_cancel_during_critical_switch_still_applies_and_resumes`
  (`:546`) — the Ctrl-C path (independent of the token, §3.3 note) still kills the
  command; `remounted`, `resumed() == vec![101]`.
- `workspace_remount_blocked_inspection_marks_blocked_and_skips_resource_remount`
  (`:600`) — `blocked_reason == "process_group_unavailable"`.
- `workspace_remount_resource_failure_blocks_state_after_cleanup` (`:635`).
- `command_remount_*` pending/blocked guards (`command_remount.rs`) — unchanged
  (guard lives in `write_command_stdin`/`exec_command`, not the coordinator).
- `command_remount_waits_for_in_flight_persistent_exec_admission` — the admission
  guard still serializes snapshot vs exec admission (§3.2 note);
  `blocked_reason == "process_group_unavailable"`.

### 12.2 New tests (Phase 5)

1. **`workspace_remount_second_remount_rejected_while_first_in_flight`** — *the
   cross-coordinator stale-resume test* (P5-R1). Drive remount A into its
   critical-switch window by blocking the workspace fake's `remount_workspace`
   (reuse the `on_remount`/`notify` hook in `workspace_remount.rs`). From a second
   thread, call `remount_workspace_session(W)`; assert it returns
   `Err(WorkspaceSessionError::RemountAlreadyPending{..})` (surfaced through
   `WorkspaceRemountError`), that the workspace fake's `remount_calls()` shows
   exactly **one** call, and that A's command is resumed exactly once
   (`resumed() == vec![pgid]`, no double-cancel). This *is* the proof of §3: a newer
   quiesce cannot exist to steal `C`.
2. **`quiesce_resume_cancels_only_affected_via_registry`** — a focused test that
   drives a single quiesce through the coordinator, calls `request_cancel()` on the
   handed-out `cancellation()` token, then `finish()`, and asserts the captured
   command's `cancel()` fired exactly once (fake `CommandExecution` records killpg
   targets) and a non-captured command in another workspace was untouched. Exercises
   the otherwise-latent token-cancel→registry path (§3.3 note) and idempotency under
   `finish` + `Drop`.
3. **`quiesce_inspection_sums_across_two_commands`** — two live commands in `W` with
   distinct pgids; assert the embedded `process_group.process_count` /
   `quiesced_process_count` are the **sum**, `command_session_ids`/`process_group_ids`
   contain both (sorted/deduped), and `can_live_remount()` is the same boolean as a
   hand-computed expectation — pins the `accumulate`/embed (P5-R7).

### 12.3 Fakes needed (all under `tests/`)

- **Fake engine launcher / `CommandExecution`** exposing a scriptable
  `process_group_id()` and a recording `cancel()` — **introduced by Phase 3 §12**
  (`phase-3-spec.md:1116-1122` **(P3)**); Phase 5 reuses it and asserts on its
  cancel-record.
- **`FakeProcessGroupController`** (`workspace_remount.rs:206-271` **(now)**) —
  reused unchanged: returns a scripted `ProcessGroupInspection`, records
  `resume_process_group_id` calls, and probes `is_remount_pending` on resume.
- **`RemountWorkspaceServiceFake`** with the `on_remount` block/notify hook
  (`workspace_remount.rs` **(now)**) — reused to hold A in its critical window for
  test 1.

---

## 13. Verification

```sh
export PATH="$PWD/bin:$PATH"
cargo fmt --check
cargo test  -p sandbox-runtime --test workspace_remount --test command_remount
cargo test  -p sandbox-runtime                # whole-crate, incl. observability_snapshot
cargo clippy --all-targets --no-deps -- -D warnings

# absence greps — the cut (scoped to the remount module):
rg -n "fn merge_report|remount_cancellation|remount_switch_state|CommandProcessStore|same_token|process_store" \
   crates/sandbox-runtime/operation/src/workspace_remount || echo "remount cut ✓"
# the embed landed:
rg -n "process_group: ProcessGroupInspection|fn accumulate|fn blocked_reason" \
   crates/sandbox-runtime/operation/src/workspace_remount/service/command/quiesce.rs
# the query landed:
rg -n "live_values|process_group_id\(\)" \
   crates/sandbox-runtime/operation/src/workspace_remount/service/command/coordinator.rs
```

Legitimate residual elsewhere: `lifecycle`/`Terminal` strings persist in
`operation/src/namespace_execution.rs` (the **observer** surface, unrelated to the
deleted `CommandLifecycleState`) — expected, not a leak.

---

## 14. Requirements traceability matrix

| Id | Requirement | Design element | Test(s) | Verify |
|---|---|---|---|---|
| P5-R1 | Stale resume cannot cancel a newer quiesce's command (cross-coordinator) | §3.2/§3.3 `begin_remount` serialization + coordinator token | 12.2 #1 | `cargo test … workspace_remount` |
| P5-R2 | Same-coordinator idempotency without the mirror | §3.2 `Finished` guard + drain | 12.2 #2, `:546` cancel test | id. |
| P5-R3 | pgid reachable via `CommandExecution::process_group_id()` | §2.4 forwarders | 12.1 `:501`, 12.2 #3 | absence/presence greps |
| P5-R4 | `workspace_root` resolved once, no new field | §2.5 | 12.1 `:600` | `rg workspace_root coordinator.rs` (one resolve) |
| P5-R5 | Single live-command query via `live_values` predicate | §4.1 | 12.2 #3 | presence grep |
| P5-R6 | Lock scope correct; query-race behavior preserved (incl. flagged refinement) | §4.2/§5 | 12.1 admission test | review + `cargo test` |
| P5-R7 | Embed `ProcessGroupInspection`; `merge_report` deleted; `can_live_remount` boolean unchanged | §7.1 | 12.2 #3, 12.1 `:501`/`:600` | absence grep `fn merge_report` |
| P5-R8 | Driver `blocked_reason` read path moved; outcome byte-stable | §9.4 | 12.1 `:600`/`:626` | `cargo test` |
| P5-R9 | Cancel via registry; resume idempotent across `finish`+`Drop` | §6 | 12.2 #2, `:546` | id. |
| P5-R10 | Lifecycle/cancellation writes deleted (write-only); observer lifecycle untouched | §6.1 | observability_snapshot | absence grep |
| P5-R11 | `same_token` deleted (no caller) | §6.1 | clippy | `rg same_token` |
| P5-R12 | `CommandRemountCoordinator` trait + driver sequence unchanged | §1/§7.2 | service_graph, all remount tests | `cargo test` |
| P5-R13 | `engine()` accessor wiring | §9.3 | compiles | build |
| P5-R14 | No second per-session map; no revived store | §4/§7.2 | — | absence greps |
| P5-R15 | Block-reason strings/precedence unchanged | §5/§7.1 | `:600`/`:626` | `cargo test` |
| P5-R16 | Observability surface untouched | §1 | observability_snapshot.rs | `cargo test` |

---

## 15. Risks & open decisions (each with a recommendation)

- **R1 — the cross-coordinator owner decision (top risk).** Chosen: prove overlap
  impossible via `begin_remount` (§3.2), delete the mirror, add no per-execution
  token. **Recommendation: adopt.** Confidence is high (single driver caller; the
  `RemountPending` state spans the full critical section). Residual exposure: a
  *future* non-driver caller of `begin_workspace_remount_quiesce` that bypasses
  `begin_remount` would break the premise — **mitigation:** keep `begin_*` reachable
  only through the driver, or assert remount-pending at the top of `begin_*`
  (cheap). Flag for human sign-off.
- **R2 — `process_group_id()` consumed-gap (§2.4).** Phase 3 may ship without it.
  **Recommendation:** land the two forwarders in Phase 3; if not, Phase 5 adds them
  and the verify step greps for their presence.
- **R3 — registry lock held across `cancel()`'s 50 ms SIGTERM→SIGKILL.** Matches
  today's "lock held across cancel" (`update_active` held the active mutex across
  `terminate_process_group`), but the registry mutex is more contended (admission +
  yields). **Recommendation:** keep `with_value(cancel)` (design-blessed, SRP — the
  engine owns the signal sequence); cancel-on-resume is the rare path (token is
  currently never `request_cancel`-ed in production). If profiling shows
  contention, collect the affected pgids and signal outside the lock, or add
  `ProcessGroupController::cancel_process_group_id` symmetric to
  `resume_process_group_id`. Low severity.
- **R4 — `ActiveCommandMissing` no longer produced (§5).** Outcome preserved (live
  remount still blocks), string unchanged, no test asserts it. **Recommendation:**
  document the production change; optionally add the defensive per-id re-check for
  strict parity. Low severity, flagged.
- **R5 — `accumulate` location.** Kept operation-local to honor "process_group.rs
  unchanged" + the LOC target. **Recommendation:** accept; defer a
  `ProcessGroupInspection::merge` push-down to a later command-crate cleanup.

---

## 16. Definition of done & LOC delta

**Done when:** §13 passes; the cut greps are clean; the three new tests (§12.2) and
all kept tests (§12.1) are green; `can_live_remount` boolean, `RemountBlockReason`
strings, the `CommandRemountCoordinator` trait, and the `WorkspaceRemountOutcome`
shape are unchanged; no second per-session map and no revived `CommandProcessStore`
exist (greps); the observability surface is untouched.

**LOC (reconciled with the design scorecard `namespace-execution.md:623-625`):**

| File | Scorecard | This plan | Note |
|---|---|---|---|
| `quiesce.rs` | 229 → ~205 (−24) | ≈ −24 | delete `merge_report`(−18) + `resume_command_records` mirror/lifecycle(−~16) + `set_switch_state` mirror loop(−~12) + `same_token`(−4) + collapse 12 fields→1(−11); add `accumulate`/`blocked_reason`/`resume_affected_commands`/`affected`+`engine` fields(+~37). |
| `coordinator.rs` | 98 → ~95 (−3) | ≈ −3 | delete mirror plant(−~15); add resolve-root + projection(+~12). |
| **subtotal (Phase-5 files)** | **−27** | **≈ −27** | matches. |
| `remount_workspace_session.rs` | — | ±0 | 1-line read-path change (§9.4). |
| `command/service/core.rs` | — | +~3 | `engine()` accessor (may already exist from Phase 3 → +0). |
| `command_execution.rs` / `execution.rs` | — | +~4 | the two pgid forwarders (§2.4) — **belong to the Phase-3 contract**; if landed there, +0 here. |

Net within Phase 5's own files: **≈ −27**, as scored. The pgid forwarders and the
`engine()` accessor are consumed-contract wiring attributable to Phase 3; if Phase 3
delivers them, Phase 5 is a clean ≈ −27.
