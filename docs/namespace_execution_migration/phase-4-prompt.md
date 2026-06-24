# Agent Prompt — Author the Phase 4 Spec (Mount family onto the engine)

## Role & deliverable

You are a senior systems architect. Produce a **rigorous, implementation-ready
specification** for **Phase 4** of the namespace-execution migration — *not* the
implementation. A different agent will build strictly from your spec, so it must
be precise enough to implement without asking you a single follow-up question.

- **Write exactly one file:** `docs/namespace_execution_migration/phase-4-spec.md`.
- **Treat the rest of the tree as read-only.** Do not modify production code,
  tests, or any `Cargo.toml`. Read and run read-only commands (`rg`, `cargo
  check`, `cargo tree`, `ls`) as much as you need to ground every claim.

A spec is sophisticated not because it is long but because it has *already made
the hard decisions* — the boundary type, the failure-signaling contract, the
observability story — so the implementer makes none. Optimize for that. Phase 4
looks small (~−200 LOC), but it hides three genuinely subtle questions; most of
your effort belongs there, not in the mechanical edits.

## Method (work in this order; writing is the last step)

1. **Investigate to ground truth.** Do not trust the starting map below — verify
   and extend it. Complete the *Investigation mandate*. Tag every factual claim
   grounded (`file:line`) or *assumed* (few, listed, justified).
2. **Reconcile three sources of truth.** The design doc (`namespace-execution.md`)
   states the *end state*; the migration doc (`migration-phases.md`) states the
   *phasing*; the code states *today*. They diverge. For each capability Phase 4
   relies on, classify it **exists-today**, **Phase-2-must-add**, or
   **Phase-4-adds**, and surface every contradiction (e.g. the design doc's engine
   has "no start-ack", but it survives until Phase 6; `RunnerOutcome::payload()`
   is assumed by the design but absent from current code).
3. **Design the minimal change.** No `MountOperation` trait, no `ops.rs`, no
   `Backing`/`NsRunnerMode` enum — two `run_mount` call sites, two closures. Before
   adding any conversion or type, name the existing one it should reuse.
4. **Stress-test before writing.** Walk the *Hard problems* catalog and every
   failure path. A design that has not survived them is not ready to write down.
5. **Write** per the *Required structure*.
6. **Self-review** against the *Quality bar* and fix every gap before reporting.

## Inputs to study

- `docs/namespace-execution.md` — design of record. Internalize **"The two
  families"** (mount = two fixed `run_mount(mode_flag, parse_closure)` sites, no
  trait), **"Decoupling `shell_exec` From Workspace"** (the `NamespaceTarget`
  boundary type and the *no-`workspace`-dependency* rule for the engine crate),
  **"Finalization / Terminal Semantics"**, **"Observability Contract"**, and the
  **Crate / Dependency Graph**.
- `docs/namespace_execution_migration/migration-phases.md` — **Phase 4** section,
  **"Invariants held at every phase boundary"**, **"Cross-phase sequencing
  constraints"**. Binding: your spec refines, never silently contradicts.

## Investigation mandate (do this first; cite `file:line`)

- **Resolve `WorkspaceModeHandle` in full** (fields, where built) and its
  relationship to `WorkspaceEntry` — is one reachable from the other? This decides
  the boundary-conversion question below; do not guess.
- **Enumerate every caller** of `mount_overlay`, `remount_overlay`, `run_child`,
  `ns_runner_request`, `mount_overlay_child`, `remount_overlay_child`, and every
  test that exercises overlay mount / live remount. A deletion is safe only once
  you have shown no surviving caller.
- **Map the daemon side**: the `NsRunnerOperation` enum, `ok_result()`,
  `dispatch_runner_mode`, and exactly how a mount/remount **failure** is signaled
  today (non-zero exit? an `Err` from the syscall? a payload field?). You cannot
  spec the new failure contract without this.
- **Trace `setup_timeout_s`**: how the timeout is enforced today (`wait_for_child`
  SIGTERM-grace-then-SIGKILL polling) and where it must live in the engine model
  (per-op / in-namespace runner scope-wait).
- **Find the engine construction/ownership wiring**: who will hold the
  `Arc<NamespaceExecutionEngine>` the mount path calls, and whether it is the same
  instance the command service uses. This drives the observability question.

## Consumed Phase 2 API (the contract — Phase 2 does not exist yet)

The crate is a Phase-1 skeleton behind `test-support`; none of this is built. Pin
the exact surface and flag any item whose current signature differs:

```rust
NamespaceExecutionEngine::run_mount<O: Send + 'static>(
    &self, mode_flag: &'static str, target: NamespaceTarget, id: NamespaceExecutionId,
    parse: impl FnOnce(RunnerOutcome) -> Result<O, NamespaceExecutionError> + Send + 'static,
) -> Result<ExecutionHandle<O>, NamespaceExecutionError>;
NamespaceExecutionEngine::allocate_id() -> NamespaceExecutionId;
// ExecutionHandle<O>::wait(self) -> Result<O, NamespaceExecutionError>   (sync session-lifecycle callers .wait())
// RunnerOutcome::payload() -> &serde_json::Value, exit_code() -> i64, status() -> NamespaceExecutionTerminalStatus
// NamespaceTarget { workspace_root, layer_paths, upperdir: Option, workdir: Option, ns_fds }   (already exists)
```

State that Phase 4 does not touch the `--start-ack-fd` plumbing (Phase 6 owns its
removal), even though Phase 4 edits `daemon/src/runner.rs`.

## Current-state starting map (verify and extend — do not treat as complete)

`workspace/src/namespace/setns_runner.rs`: `mount_overlay` (~:43) →
`ns_runner_request` → `mount_overlay_child` (`"--mount-overlay"`);
`remount_overlay` (~:61) → `RemountOverlayResult` from the child payload;
`ns_runner_request(handle, request, args, layer_paths)` (~:134) builds
`NamespaceRunnerRequest` and holds the **`isolated-{request}-{workspace_id}`** id
format (~:141); `mount_overlay_child` (~:153), `remount_overlay_child` (~:171);
**`run_child(request, mode_arg, setup_timeout_s)` (~:194)** = pipes → spawn
`current_exe ns-runner {mode} --request-fd --result-fd` → write → wait → read →
`Output`; `wait_for_child` (~:235), `terminate_child` (~:268), `read_pipe` (~:278).

`workspace/src/model.rs`: `WorkspaceEntry` (~:295) `{ workspace_root, layer_paths,
upperdir: PathBuf, workdir: PathBuf, ns_fds: WorkspaceEntryFds }`;
`WorkspaceEntryFds` (~:313) and `From<WorkspaceEntryFds> for NsFds` (~:333).

`workspace/src/lifecycle/remount/result.rs`: `RemountOverlayResult { mount_verified,
failure_summary }`, `from_payload(&Value)` (~:17).

`sandbox-daemon/src/runner.rs`: `dispatch_runner_mode` (~:37); `MountOverlay` arm
(~:53) → `setns_overlay_mount` then `ok_result()`, failure `?`-propagates;
`RemountOverlay` arm (~:43) already returns its report in `RunResult.payload`;
`RunResult { exit_code: i32, payload: Value }`.

`namespace-process/.../protocol.rs`: `NamespaceRunnerRequest { request_id, args,
workspace_root, layer_paths, upperdir, workdir, ns_fds, timeout_seconds }`, `NsFds`.
**Unchanged** by Phase 4 — request construction moves into the engine launcher.

## Hard problems the spec MUST resolve (this is where Phase 4 is hard)

1. **Observability + the dependency-cycle hazard.** The engine drives
   `on_running`/`on_terminal` by id, but `begin` (which carries
   `workspace_session_id` + `operation_name`) lives in the **operation layer**,
   while the mount path lives in **`workspace`, *below* operation**. So `workspace`
   cannot call the operation-layer ledger without inverting the dependency graph.
   Decide and justify: **do mount executions appear in
   `active_namespace_executions` at all?** The migration invariant is "observability
   surface unchanged" — argue whether that means mount stays *absent* from the
   observable list (tracked only in the engine registry, no `begin`) or is added
   (and if so, by what mechanism that does not create a `workspace → operation`
   cycle). State what `on_running`/`on_terminal` do for an id that was never
   `begin`'d (must be a safe no-op) — or whether the mount path uses an engine
   wired with a no-op observer.
2. **Shared vs. per-crate engine.** Is there one `Arc<NamespaceExecutionEngine>`
   shared by the command service and the mount path, or a separate workspace-local
   engine? Resolve, with consequences spelled out: a shared `max_active` admission
   pool (could a burst of remounts starve commands, or vice versa?); the observer
   wiring (ledger observer vs. no-op); and Phase 5's registry queries (which must
   see live *command* executions). Surface the construction-site obligation this
   places on Phase 2/3 wiring as an explicit assumption if it is not yet decided.
3. **The mount failure-signaling contract.** Today mount failure `?`-propagates;
   Phase 4 routes failure text into `RunResult.payload`. Specify the *exact*
   contract for **both** modes, end to end (daemon arm → `RunResult{exit_code,
   payload}` → `RunnerOutcome::status()` → parse closure):
   - `--mount-overlay` (`parse = |_| Ok(())`): does a failed mount set
     `exit_code != 0` (so the engine yields a terminal error *before* the closure
     runs) or `exit_code == 0` + failure payload (so the closure must inspect and
     return `Err`)? With a no-op closure, only the exit-code path can fail — make
     this consistent.
   - `--remount-overlay` (`parse = |o| Ok(RemountOverlayResult::from_payload(o.payload()))`):
     today a *not-verified* remount returns `Ok(RemountOverlayResult{ mount_verified:
     false, .. })`, **not** an error — the caller inspects the flag. Preserve that
     exact semantic: a remount whose verification fails must remain a successful
     `wait()` returning `mount_verified=false`, distinct from an *engine/spawn*
     failure which is an `Err`. Pin which conditions map to `Err` vs. `Ok(false)`.
4. **Blocking `.wait()` + the watcher thread.** Session-lifecycle callers block on
   `run_mount(...).wait()` (today's behavior). Yet the engine still spawns a watcher
   thread + promise. Justify the uniformity (or propose a synchronous fast path),
   and specify the registry lifecycle for a mount execution: does it take admission
   (`try_reserve`)? does it enter the live map and then complete? since `.wait(self)`
   consumes the handle, is anything retained after completion, and is the watcher
   thread joined/cleaned with no leak?
5. **Timeout & termination parity.** Specify how the mount setup-timeout is enforced
   now (per-op timeout on the request / in-namespace scope-wait vs. an engine-side
   wait) and whether the SIGTERM-grace-then-SIGKILL escalation in `wait_for_child`
   is preserved, moved, or intentionally dropped. If behavior changes, say so
   loudly — this is a preserved-behavior item.
6. **The boundary conversion.** Resolve how the mount path obtains a
   `NamespaceTarget`: reuse `From<WorkspaceEntry>` (if a `WorkspaceEntry` is
   reachable from `WorkspaceModeHandle`) or add `From<&WorkspaceModeHandle>` / an
   inline builder — **without duplicating** the existing `From<WorkspaceEntryFds>
   for NsFds` mapping, and honoring the orphan rule (the impl must live in
   `workspace`). Note `WorkspaceEntry.upperdir/workdir` are `PathBuf` but
   `NamespaceTarget`'s are `Option<PathBuf>`. Pick the smaller option and justify.

## Required structure of `phase-4-spec.md` (assign every normative requirement a stable id `P4-Rn`)

1. **Objective & non-goals.**
2. **Consumed Phase 2 API** + the exists-today / Phase-2-adds / Phase-4-adds
   classification, with divergences from current code flagged.
3. **The three subtle decisions** — dedicated sections resolving Hard problems 1
   (observability/cycle), 2 (engine sharing), and 3 (failure-signaling contract),
   each with a **Rejected alternatives** note.
4. **The boundary conversion** — the chosen `NamespaceTarget` sourcing, fully
   specified, with orphan-rule and `Option`-wrapping reasoning.
5. **File-by-file change plan** — every Edit / Delete with before→after and why:
   - `setns_runner.rs`: replace `run_child`/`wait_for_child`/`terminate_child`/
     `read_pipe`/`ns_runner_request` with the two `run_mount(...).wait()` sites;
     id from `engine.allocate_id()`; delete the `isolated-{mode}-{id}` format.
   - `model.rs`: the `From` impl(s) per the decision.
   - `daemon/src/runner.rs`: `MountOverlay` failure → `RunResult.payload` per the
     contract in §3; rename the `dispatch_runner_mode` parameter.
   Pair each deletion with evidence it has no surviving caller.
6. **Safe edit order** — the edit sequence that keeps `cargo build` green at each
   step (note `daemon` and `workspace` are different crates; order accordingly).
7. **Cross-phase coordination** — `From<WorkspaceEntry> for NamespaceTarget` is the
   single edit shared with Phase 3; state ownership so they don't collide.
8. **Invariants preserved** — table: invariant → mechanism → test (overlay mount &
   live remount succeed via `engine.run_mount`; remount report parses; failure →
   terminal error vs. `Ok(false)` per §3; timeout/termination parity per Hard
   problem 5; no `execution_kind`/`backing`; engine crate keeps **zero**
   `workspace` dependency).
9. **Test plan** — workspace/daemon tests that keep passing, move, or are new.
   Honor the repo rule: **no inline tests in production sources**; unit tests in
   integration suites.
10. **Verification** — exact commands (fmt, `cargo test -p sandbox-runtime-workspace`,
    `cargo build -p sandbox-daemon`, clippy `-D warnings`, absence-greps:
    `fn run_child|fn ns_runner_request|fn wait_for_child|fn terminate_child|fn read_pipe`
    gone, `isolated-` gone).
11. **Requirements traceability matrix** — `P4-Rn` → design element → test → verify
    command.
12. **Risks & open decisions** — recommended resolution each.
13. **Definition of done & LOC delta.**

## Design constraints the spec must honor (from `CLAUDE.md`)

- **SRP/SOLID;** `workspace` depends only on the engine's narrow `run_mount`, never
  its internals; the engine crate keeps **zero** `workspace` dependency (this is
  what lets it sit *below* `workspace`).
- **Prefer less** — net deletion; no `MountOperation` trait, no `ops.rs`, no
  `Backing`/`NsRunnerMode` enum; two call sites, two closures.
- **No re-complication:** mount stays behind the `NsRunnerLauncher` seam; no second
  spawn/wait/pipe path; no shims/aliases/dual-write.
- **No inline comments in production code;** `///` on public items only. The
  prescribed design must obey this even where the spec shows illustrative code.

## Quality bar (apply as a self-review gate before reporting; fix every miss)

- Could a competent implementer build this with **zero** clarifying questions? If
  not, name the underspecified section and fix it.
- Are Hard problems 1–6 each resolved with a concrete decision, a rationale, and
  the rejected alternatives — not deferred?
- Is the failure-signaling contract (§3) specified end to end for *both* modes,
  with the `Err` vs. `Ok(mount_verified=false)` boundary pinned?
- Is every deletion proven safe (no live callers shown)?
- Is every claim tagged grounded (`file:line`) or assumed, assumptions minimized?
- Does anything contradict the design or migration docs? Deviations argued and
  flagged for human review?

## Report back

Return: the path written; a 10–15 line section outline; your resolution of Hard
problems 1 (observability/cycle), 2 (engine sharing), and 3 (failure contract) in
a few sentences each; the chosen boundary conversion; the top 3 risks/open
decisions; and every contradiction or ambiguity you found across the three
sources. Do not commit or push.
