# Root–Request Collapse — SPEC (v2)

**Status:** Implemented — all acceptance criteria pass (`cargo check`/`clippy -D warnings`/`test` green for `eos-runtime`)
**Owner crate:** `agent-core/crates/eos-runtime`
**Scope:** `entry.rs`, `lib.rs`, `main.rs`, `tests/unit/mod.rs` (+ delete `root_agent.rs`)
**v2 changes:** inject `request_id` (identity is an input); `RequestOutcome` → `{ status, terminal }`;
single `task_store` read-back (`TaskStatus`); restore honest name `fail_unfinished_root`; explicit
dispatch/polymorphism decision; fixed Phase-3 test approach (the old "scrape ids from the store" was
infeasible — `RequestStore` has no enumeration).

## 1. Summary

Collapse the root-agent request path from a **spawn + `RequestEntryHandle` (Drop/join/shutdown)**
two-step into a single **run-to-completion** function whose **identity is injected**. The request and
its root task share one derived identity, the root agent runs **inline** through the shared engine
primitive, closure is a single framework-side guard, and the function **returns the root's terminal
outcome** directly.

This makes the code match the intended mental model: *user request → root task → run the agent →
terminal tool closes it → return the outcome*.

## 2. Canonical flow sequence

Authoritative; the diagram (§5), architecture decisions (§7–§8), phases and acceptance criteria (§10)
all trace back to it.

```
1. user request                              (caller mints request_id: RequestId)
2. run_request(request_id): create request row → root_task_id = "root-{request_id}"
        → upsert root Task(role=Root, Running)
3. run_agent INLINE (await)                  ← the shared engine primitive; NO dispatch/spawn layer
4. agent engine loop (run_query)             ← model ↔ tools ↔ sandbox; may delegate_workflow
5. terminal tool returned                    ← root calls submit_root_outcome; loop exits
6. close root task with the terminal tool's outcome
        ↳ submit_root_outcome writes task status + terminal_tool_result AND finish_request
          (this IS the close — inside step 5, before run_agent returns)
        ↳ fail_unfinished_root after the await is the idempotent backstop (no-op on the happy path)
7. return the root task outcome to the user
        ↳ run_request reads the persisted root task once and returns
          RequestOutcome { status: TaskStatus, terminal: Option<JsonObject> }
```

### Notes (corrections folded into v2)

- **Identity is an input (step 1–2).** The caller mints `request_id`; `run_request` takes it by
  reference. `root_task_id = "root-{request_id}"` is derived. Both ids are therefore caller-known
  *before* the run completes — this is what makes in-flight observation possible without a handle or
  a `RequestStore` enumeration (see §10 Phase 3). It mirrors how workflow agents already receive
  their `task_id` from outside the run (planner-authored DAG); the root is not a special case.
- **Step 3 is not a "dispatch".** No spawn / handle / dispatcher: `run_request` calls `run_agent(...)`
  (singular, the shared engine primitive) **inline with `.await`**.
- **Step 7 returns the outcome, not ids.** `RequestOutcome` carries only the genuinely-new
  information — `status` + `terminal` — read in one `task_store.get(&root_task_id)`. The ids are
  caller-known, so echoing them would be redundant.

## 3. Decisions & invariants

### Decisions adopted

| Decision | Choice |
|---|---|
| Handle layer | **Removed** (radical collapse): no `tokio::spawn`, no `RequestEntryHandle`, no `Drop`/`join`/`shutdown` |
| Identity | `request_id` is an **injected input** (`&RequestId`); `root_task_id = "root-{request_id}"` (Option A — derived, no random `Uuid::new_v4()`) |
| Root task **row** | **Kept** — load-bearing agent anchor (see invariants) |
| Closure | Single framework-side guard `fail_unfinished_root` (idempotent CAS `Running→Failed`); `submit_root_outcome` is the happy-path writer |
| Outcome contract (step 7) | `run_request -> RequestOutcome { status: TaskStatus, terminal: Option<JsonObject> }` |
| Dispatch / polymorphism | **No unifying trait** (see §7) — concrete dispatch for the closed `{root, workflow-agent}` set; `AgentRunner` stays the one `dyn` seam |

### Invariants preserved (must not break)

1. **Root Task row stays.** `Task` is the persisted agent interface: `agent_run.task_id`
   (required `TaskId`) and `workflow.parent_task_id` (required `TaskId`) key off it;
   `submit_root_outcome` writes the task row.
2. **GC-eos-runtime-01:** the root runs **directly** through the engine (`run_agent`), never the
   workflow starter.
3. **GC-eos-runtime-03:** a delegated workflow closing must **not** mutate the parent root task.
4. **Persisted-state contracts** (`eos-state` DTOs, `eos-db` columns/SQL) are unchanged — the
   `requests.root_task_id` column stays (Option A keeps it; column removal is out of scope).
5. **Delegation runtime wiring** (`supervisor`, `notifier`, context engine, orchestrator registry,
   composer, `plan_submission`, `workflow_control` via `OnceLock`, `attempt_deps → starter`) stays.
6. **Crash / exit-without-terminal persists a `Failed` status** via `fail_unfinished_root`.

### Out of scope

- Removing the root Task **concept/row** (special-cases the root across 4–5 crates — rejected).
- Dropping the `requests.root_task_id` **column** (persisted-state contract change).
- Any change to `eos-engine`, `eos-tools`, `eos-state`, `eos-db`, `eos-workflow` (beyond the stale
  doc-comment note in §11).

## 4. Simplifications (audited)

### Adopted reductions (verified safe against §3 invariants)

| Reduction | Removes |
|---|---|
| Inject `request_id`; `RequestOutcome` → `{ status, terminal }` | the ids as outputs + the infeasible store-scrape |
| Dissolve `RootAgentParams` → plain locals (spawn was its only reason to exist) | 1 struct (8 fields) |
| Build `attempt_deps` as a local, **move** it into `WorkflowStarter::new` (no clone), never return it | 1 `AttemptDeps` clone + 1 dead field |
| Drop the dedicated `workflow_control_handle` clone; the inline guard uses the in-scope `workflow_control` | 1 clone + 1 field |
| Call `fail_unfinished_root` **unconditionally** (delete the `if run.error \|\| no-terminal` gate) | 1 branch |

### Guardrails (look cuttable, but MUST stay)

| Keep | Reason |
|---|---|
| Heartbeat as a **separate concurrent spawn** | concurrent producer into the shared `NotificationService` the loop drains mid-run (§7 instance identity); inlining breaks background-completion delivery |
| Post-await `supervisor.cancel_for_parent_exit(None, …)` | `None` sweeps **all** still-`Running` background records (request-scoped); the run-scoped path only handles the root's own `agent_run_id` |
| `OnceLock` late-binding of `workflow_control` | irreducible given the `starter → attempt_deps → runner` construction cycle; feeds the runner's `workflow_depth`/`find_outstanding` hooks |

> The ~70-line delegation wiring is kept **inline** (audit-confirmed irreducible). It is **not**
> wrapped in a single-use `RequestRuntime` struct — that would be construction ceremony, not
> simplification. At most one private `wire_*` helper if readability demands it.

## 5. Workflow diagram

```
 user request  ──(caller mints request_id: RequestId)──┐
                                                        ▼
 run_request(state, &request_id, prompt, sandbox_id, on_event) -> Result<RequestOutcome>
   │
   │  [2] BOOTSTRAP
   ├─ provision sandbox → create_request(&request_id, …)
   ├─ wire delegation runtime (locals: supervisor, notifier, context engine, orchestrator
   │     registry, composer, plan_submission, workflow_control via OnceLock, attempt_deps → starter)
   ├─ root_task_id = root_task_id_for(&request_id)           ← "root-{request_id}" (Option A)
   ├─ upsert root Task(role=Root, Running) → set_root_task_id
   ├─ spawn heartbeat ───────────────┐  (concurrent; feeds the shared notifier)   [GUARDRAIL]
   ├─ resolve "root" def + build metadata (locals, no RootAgentParams)
   │                                  │
   │  [3][4][5] RUN                   │
   ├─ run_agent(root).await ◄─────────┘     INLINE — engine query loop; may delegate_workflow
   │     │
   │     └─ loop: model ↔ tools ↔ sandbox
   │         └─ root calls submit_root_outcome  ──┐
   │             • set_task_status(Done|Failed) + terminal_tool_result   │  [6] CLOSE
   │             • finish_request(Done|Failed)                           │  (inside the loop)
   │     returns AgentRunResult { terminal_result, error }  ◄────────────┘
   │
   │  [6] FINALIZE / CLEANUP
   ├─ heartbeat.abort()
   ├─ supervisor.cancel_for_parent_exit(None, …)            ← request-scoped sweep   [GUARDRAIL]
   ├─ fail_unfinished_root(state, &request_id, &root_task_id, summary)  ← ONE guard, called always
   │     • happy path: task already off Running → no-op (terminal_tool_result already written)
   │     • sad path:   task still Running → mark Failed + finish_request(Failed),
   │                   terminal_tool_result = { fail_reason, summary }
   ├─ flush audit
   │
   │  [7] RETURN OUTCOME
   ├─ task = task_store.get(&root_task_id)                  ← single read-back
   │
   ▼
 Ok(RequestOutcome { status: task.status, terminal: task.terminal_tool_result })   → return to user
```

## 6. Resulting file / folder structure

```
agent-core/crates/eos-runtime/src/
├── lib.rs              # mod list (− root_agent); pub use entry::{run_request, RequestOutcome}
├── main.rs             # let id = RequestId::new_v4(); run_request(&state, &id, …).await?;
├── entry.rs            # run_request + RequestOutcome + fail_unfinished_root + root_task_id_for
│                       #   ◄── absorbs root_agent.rs
├── root_agent.rs       # ❌ DELETED
├── agent_runner.rs     # RuntimeAgentRunner (workflow agents) — UNCHANGED (the dyn AgentRunner impl)
├── app_state.rs        # AppState graph — UNCHANGED
├── tool_context.rs     # build_metadata + MetadataParams — UNCHANGED
├── isolated_workspace.rs
├── plugin_tools.rs
└── observability.rs

agent-core/crates/eos-runtime/tests/unit/
├── mod.rs              # reworked (see §10 Phase 3)
└── app_state_test_seams.rs
```

Net: **8 source files → 7** (one deleted). Production changes confined to `entry.rs` + `lib.rs` +
`main.rs`; test changes to `tests/unit/mod.rs`.

## 7. Architecture & OOP decisions

### 7.1 Dispatch strategy / polymorphism — **decline the unifying trait** (deliberate)

`{root-agent, workflow-agent}` is a **closed set**, so dispatch is **concrete**, not trait-based:

- The **root** launch is a plain `async fn run_request` (no trait). It needs no substitution — there
  is exactly one production caller and the test seam is the event-source factory, not the launcher.
- The **workflow** launch keeps `dyn AgentRunner` (`RuntimeAgentRunner` in prod, fakes in tests) —
  that trait stays because it has **real alternate implementations** (runtime-selected test doubles),
  which is the only justification for `dyn` per the Rust OOP posture.
- Both already funnel into the shared engine primitive `run_agent`. A unifying `AgentLauncher` trait
  would wrap a one-line call while forcing role-specific fields to be `Option`-ed — a net negative
  and an abstract-base-style anti-pattern. **Not added.**

This is the OOP answer: name the dispatch strategy (concrete for the closed set; `dyn` only where
substitution is load-bearing) and resist adding abstraction.

### 7.2 Public surface (`entry.rs`)

```rust
/// The terminal outcome of a completed top-level request — the root's outcome (step 7).
/// The ids are caller-known (request_id is injected, root_task_id is derived), so they are not
/// echoed here; this struct holds only what the run produces.
#[non_exhaustive]
pub struct RequestOutcome {
    /// Authoritative final status of the root task. `Done` | `Failed`.
    pub status:   TaskStatus,
    /// The root's persisted terminal payload (`Task.terminal_tool_result`): the agent's submitted
    /// outcome on success, or `{ fail_reason, summary }` written by the guard on failure.
    /// `Some(_)` on every normal-or-guarded completion.
    pub terminal: Option<JsonObject>,
}

/// Run a top-level request to completion and return the root's outcome.
/// `request_id` is minted by the caller (identity is an input, not an output).
pub async fn run_request(
    state:      &AppState,
    request_id: &RequestId,
    prompt:     impl Into<String>,
    sandbox_id: Option<&str>,
    on_event:   Option<EventCallback>,
) -> anyhow::Result<RequestOutcome>;

/// The single source of truth for the root task id derivation (Option A).
/// Used by `run_request` to mint the row and by tests to know the id before completion.
pub(crate) fn root_task_id_for(request_id: &RequestId) -> TaskId; // TaskId("root-{request_id}")
```

API notes (Rust conventions): borrowed inputs (`&RequestId`, `impl Into<String>`, `Option<&str>`);
`#[non_exhaustive]` value object so fields can evolve; `anyhow::Result` is allowed here (the runtime
crate is the binary/orchestration edge).

### 7.3 Private surface (`entry.rs`)

```rust
/// Fail the root **iff** it is still Running (idempotent CAS) + finish_request(Failed).
/// Called unconditionally after the inline run; a no-op once submit_root_outcome has closed the task.
/// Honest name retained from the original (it acts only when the root is unfinished).
async fn fail_unfinished_root(
    state:        &AppState,
    request_id:   &RequestId,
    root_task_id: &TaskId,
    summary:      &str,
);
```

`run_request`'s in-flight values are **plain locals**, not structs/fields:
`binding`, `supervisor`, `notifier`, `heartbeat` (JoinHandle), `workflow_control` (+ `OnceLock` cell),
`attempt_deps` (local → moved into `WorkflowStarter`), `metadata`, `run` (`AgentRunResult`).

### 7.4 Deleted types & functions

| Deleted | Was in | Fields / role |
|---|---|---|
| `RequestEntryHandle` | `entry.rs` | `request_id, root_task_id, attempt_deps, root_agent_task, supervisor, workflow_control, heartbeat, state, finished` + `Drop`/`join`/`shutdown` |
| `RootAgentParams` | `root_agent.rs` | `request_id, root_task_id, prompt, sandbox_id, workflow_control, background_supervisor, command_session_supervisor, notifier, on_event` |
| `run_root_agent` | `root_agent.rs` | folded inline into `run_request` |
| `start_request` (return-handle form) | `entry.rs` | replaced by `run_request` |

### 7.5 Unchanged collaborators (constructed as locals, not new types)

`MetadataParams`/`build_metadata` (`tool_context.rs`), `AttemptDeps`, `WorkflowStarter`,
`WorkflowControlAdapter`, `RuntimeAgentRunner` (`agent_runner.rs`), and from `eos-engine`:
`run_agent`, `AgentRunInput`, `AgentRunResult`, `EngineRunHandles`.

## 8. `lib.rs` / `main.rs` deltas

- `lib.rs`: delete `mod root_agent;`; change `pub use entry::{start_request, RequestEntryHandle};`
  → `pub use entry::{run_request, RequestOutcome};`.
- `main.rs`:
  ```rust
  let request_id = eos_types::RequestId::new_v4();
  let outcome = run_request(&state, &request_id, prompt, None, None).await?;
  tracing::info!(request_id = %request_id, status = ?outcome.status, "request finished");
  ```
  (caller mints the id; no `handle.join()`).

## 9. Naming conventions (resulting)

| Name | Kind | Why |
|---|---|---|
| `run_request` | `pub async fn` | run-to-completion verb; not `start_*` (no longer returns-while-running) |
| `RequestOutcome` | `pub struct` (value object) | what the run produces; `#[non_exhaustive]` for evolution |
| `root_task_id_for` | `pub(crate) fn` | single source of truth for the `root-{request_id}` derivation; `_for` reads as "the id for this request" |
| `fail_unfinished_root` | `async fn` | honest: it acts **only** when the root is unfinished (kept from the original; not `finalize_root`, which would overstate its role) |
| `status` / `terminal` | `RequestOutcome` fields | the two pieces of new information; mirror `Task.status` / `Task.terminal_tool_result` |

Removed names (and why they go): `start_request`/`RequestEntryHandle` (no handle), `RootAgentParams`
(fields are now locals), `run_root_agent` (folded into `run_request`).

## 10. Phases & acceptance criteria

> Phase 1 is independently green. Phase 2 leaves the **lib** green and tests red (expected; the
> `#[cfg(test)]` module is excluded from `--lib`). Phase 3 returns the full suite to green. Phase 4
> finalizes.

### Phase 1 — Identity derivation (Option A)
- **Do:** add `pub(crate) fn root_task_id_for(&RequestId) -> TaskId`; use it where the root task id
  is minted; remove the random `Uuid::new_v4()` root-id mint. (`start_request`/handle still present.)
- **AC1.1:** no `Uuid::new_v4()` for the root task id; id is `"root-{request_id}"`, deterministic.
- **AC1.2:** `requests.root_task_id` column/DTO untouched (Invariant 4).
- **AC1.3:** all existing `eos-runtime` lib tests pass unchanged (literal `"root-1"` ids elsewhere are
  unaffected; `TaskId::from_str` only rejects empty, so the dashed-uuid form parses).
- **Verify:** `cargo test -p eos-runtime --lib`.

### Phase 2 — Collapse + injected identity + outcome contract
- **Do:** introduce `run_request(state, &request_id, …) -> RequestOutcome { status, terminal }`;
  inline `run_agent(...).await`; add `fail_unfinished_root` (unconditional idempotent CAS); read the
  root task once for the return; dissolve `RootAgentParams` to locals; `attempt_deps` local + move;
  drop `workflow_control_handle`; delete `RequestEntryHandle` + `Drop`/`join`/`shutdown` + the
  `tokio::spawn`; keep heartbeat spawn + `cancel_for_parent_exit(None,…)` + `OnceLock` (Guardrails);
  update `lib.rs` exports + `main.rs`; **delete `root_agent.rs`** + `mod root_agent;`.
- **AC2.1:** `root_agent.rs` deleted; `mod root_agent;` removed; no references to `RequestEntryHandle`,
  `RootAgentParams`, `run_root_agent`, `start_request` remain.
- **AC2.2:** exactly **one framework-side** closure call-site (`fail_unfinished_root`); the happy-path
  writer is `submit_root_outcome` (two writers by design — do not claim a single writer).
- **AC2.3:** `RequestOutcome { status: TaskStatus, terminal: Option<JsonObject> }`, populated by one
  `task_store.get(&root_task_id)` after the guard.
- **AC2.4:** all three Guardrails present (heartbeat concurrent spawn; `cancel_for_parent_exit(None,…)`;
  `OnceLock` late-bind).
- **AC2.5:** Invariants 1–6 hold.
- **AC2.6:** no unifying launcher trait introduced (§7.1); `AgentRunner` unchanged.
- **Verify:** `cargo check -p eos-runtime --lib` green (tests red expected at this phase).

### Phase 3 — Test rework
- **Do:** convert mechanical sites (`handle` → minted `request_id` + `root_task_id_for`; assert on
  `outcome`/stores; drop `join()`); **delete** `join_error_marks_unfinished_root_failed`
  (AC-eos-runtime-03b), the two shutdown tests (AC-eos-runtime-08b), and
  `dropped_handle_cancels_background_and_fails_running_root`; **rewrite** the spawn-observe tests
  (`delegate_workflow_leaves_parent_running` / AC-eos-runtime-05 and the D5 test) as:
  ```rust
  let request_id = RequestId::new_v4();
  let root_task_id = root_task_id_for(&request_id);            // id known BEFORE the run
  let h = tokio::spawn({ let s = state.clone(); let id = request_id.clone();
                         async move { run_request(&s, &id, …).await } });
  // poll list_for_parent_task(&root_task_id); assert parent stays Running; ...
  h.abort();                                                   // teardown only
  ```
  **restructure** the supervisor-access background-session test to observe via stores/notifications.
- **AC3.1:** full suite compiles and passes.
- **AC3.2:** GC-eos-runtime-03 still asserted: a delegated workflow closes while the parent root task
  stays `Running` (driven via the test-side spawn).
- **AC3.3:** rewritten spawn-observe tests assert only **pre-abort** state (the aborted future runs no
  finalizer — there is no `Drop` guard); no surviving test asserts post-abort `Failed`.
- **AC3.4:** new test count = previous − 3 deleted; deletions are only the capability-removed tests.
- **Verify:** `cargo test -p eos-runtime --all-targets`.

### Phase 4 — Verify & finalize
- **Do:** clippy clean; confirm `entry.rs` LOC within the 300–600 norm; update the `lib.rs` module
  doc and the stale `eos-types ids.rs:102` convention comment (root id is now `root-{request_id}`).
- **AC4.1:** `cargo check -p eos-runtime --all-targets` clean.
- **AC4.2:** `cargo clippy -p eos-runtime --all-targets -- -D warnings` clean.
- **AC4.3:** `cargo test -p eos-runtime` green.
- **AC4.4:** `entry.rs` cohesive request-lifecycle, within LOC norm.
- **Verify:** full ladder above.

## 11. Progress tracker

| Phase | Task | Status | Verification |
|---|---|---|---|
| 1 | `root_task_id_for` + derive root id (Option A) | ☑ Done | `cargo test -p eos-runtime --lib` ✅ |
| 2 | `run_request(&request_id) -> RequestOutcome{status,terminal}` + read-back | ☑ Done | `cargo check -p eos-runtime --lib` ✅ |
| 2 | Inline `run_agent().await`; delete spawn + `RequestEntryHandle` + `start_request` | ☑ Done | ✅ |
| 2 | `fail_unfinished_root` unconditional; adopted reductions | ☑ Done | ✅ |
| 2 | Delete `root_agent.rs` + `mod root_agent;`; update `lib.rs` + `main.rs` | ☑ Done | ✅ |
| 3 | Mechanical test conversions (`handle`→ minted id) | ☑ Done | `cargo test -p eos-runtime --all-targets` ✅ |
| 3 | Delete AC-03b + 2× AC-08b + Drop tests | ☑ Done | 3 deleted; 21→18 tests ✅ |
| 3 | Rewrite AC-05 + D5 spawn-observe tests (injected ids + abort) | ☑ Done | ✅ |
| 3 | Restructure supervisor-access background-session test | ☑ Done | observes via task/request stores ✅ |
| 4 | clippy + final count + doc/`ids.rs:102` refresh | ☑ Done | full ladder ✅ |

Status legend: ☐ Not started · ◐ In progress · ☑ Done · ⚠ Blocked

## 12. Verification ladder (cumulative)

```
cargo check  -p eos-runtime --all-targets
cargo clippy -p eos-runtime --all-targets -- -D warnings
cargo test   -p eos-runtime                # lib (incl. #[path] tests/unit) + integration
```

## 13. Risks

| Risk | Mitigation |
|---|---|
| In-flight observation needs the ids before completion | **injected `request_id`** + `root_task_id_for` derivation — ids are caller-known; no `RequestStore` enumeration needed (the old "scrape from the store" was infeasible) |
| Removing `shutdown`/`Drop` deletes AC-eos-runtime-08b/03b behavior | accepted decision; tests deleted, not weakened to pass |
| A root-agent panic now propagates out of `run_request` (inline `.await`, no `JoinError`) | acceptable for the binary path; `fail_unfinished_root` still persists Failed on non-panic exits |
| Aborted spawn-observe test future runs no finalizer | AC3.3 — those tests assert only pre-abort state |
| Over-trimming a Guardrail | §4 Guardrails are explicit must-keeps; AC2.4 enforces their presence |
| Derived id deviates from `root-<hex[:16]>` convention | cosmetic; prefix is never parsed; update the `ids.rs:102` doc comment in Phase 4 |
| Parallel-agent churn in neighboring crates | verify per-phase from the `eos-runtime` workspace; report unrelated breakage, don't fix it |
