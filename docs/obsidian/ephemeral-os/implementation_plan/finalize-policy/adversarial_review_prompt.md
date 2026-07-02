# Adversarial Review Prompt — Workspace Session Finalize Policy

Copy everything below the line into a fresh agent session with access to this repository.

---

## Role

You are an adversarial design reviewer. Your job is to **break** the spec at
`docs/obsidian/ephemeral-os/implementation_plan/finalize-policy/spec.md`, not to validate it.
Default to refutation: for every property the spec claims, attempt to construct a
counterexample before accepting it. A finding you cannot ground in a `file:line` or a concrete
interleaving is not a finding — drop it or downgrade it to a question.

The spec redesigns workspace-session finalization in this Rust workspace: finalization policy
(`publish_then_destroy` / `destroy` / `no_op`) moves from `exec_command`'s request shape onto
the session itself; a per-session **activity ledger** of running namespace executions replaces
engine-scanning; an RAII `SessionExecutionToken` detaches on every exit path; the empty-ledger
edge triggers the policy. Read the spec fully first, then read the current code it changes:

- `crates/sandbox-runtime/operation/src/command/service/exec_command.rs` (current finalize closure)
- `crates/sandbox-runtime/operation/src/command/service/core.rs` (engine, guarded destroy)
- `crates/sandbox-runtime/operation/src/workspace_session/service/core.rs` (sessions map, gates map, lock-order doc)
- `crates/sandbox-runtime/operation/src/workspace_session/service/model.rs`
- `crates/sandbox-runtime/operation/src/workspace_session/service/impls/{create_workspace_session,destroy_session,resolve_session,run_file_op,remount_session,capture_session_changes}.rs`
- `crates/sandbox-runtime/namespace-execution/src/{engine,registry,promise}.rs` (watcher thread, entry lifecycle)
- `crates/sandbox-runtime/workspace/src/service/impls/{capture_changes,destroy_workspace}.rs`
- `crates/sandbox-runtime/operation/src/layerstack/` (publish path, `expected_base` semantics)

Workload model for all concurrency reasoning: **tens to hundreds of operations per second
sustained** — mostly bare `exec_command` (implicit `publish_then_destroy` session per call, so
create→exec→finalize→destroy churns at that rate), a few long-running commands with
progress-check riders attached to the same session, file ops and occasional remounts mixed in.
Assume multi-core, std `Mutex` (non-reentrant, no fairness guarantee), one watcher thread per
execution.

## Dimension 1 — Architecture simplicity

The spec sells itself as "move-and-generalize, net ≈ +10 LOC, one trigger, zero special
cases." Attack that claim.

1. **Mechanism count.** After the change there are three coexisting liveness/serialization
   mechanisms: the per-session admission gate, the new activity ledger, and the engine
   registry's live/terminal state. Before the change there were two. Is the ledger genuinely
   load-bearing, or could one of the existing mechanisms express the trigger? Steelman at
   least two simpler alternatives and show precisely where each fails or survives:
   (a) gate-only — synchronous ops already hold the gate; could commands hold a gate-derived
   lease instead of a ledger entry? (b) engine-scan-only — keep today's
   `live_values` scan but move it behind a `SessionActivityProbe` trait injected into
   `WorkspaceSessionService`. If an alternative survives your own attack, the spec's ledger is
   accidental complexity — say so.
2. **Token uniformity on synchronous ops.** File ops and remounts already hold the session
   gate for their entire duration; the spec adds tokens to them "for uniformity." Is that
   redundancy dressed as uniformity? What concretely breaks if sync ops take no token?
3. **Dependency rotation.** Publish moves from `CommandOperationService` into
   `WorkspaceSessionService` (which gains `Arc<LayerStackService>`). Draw the before/after
   dependency graphs. Is the new edge `workspace_session → layerstack` better or worse than
   the old `command → layerstack`, given README's boundary law and that `workspace_session`
   is also depended on by the file service? Does any cycle or near-cycle appear?
4. **Type surface.** `CreateSessionRequest` duplicates `CreateWorkspaceRequest` plus one
   field; `WorkspaceSessionHandler` gains `finalize_policy`; `CommandOutput` gains
   `workspace_session_id`. Audit each against the repo's "prefer less" rule: which fields are
   actually read by anyone, and where could an existing type carry the responsibility?
5. **Policy count.** Is the `destroy` (discard) policy justified by a real caller today, or is
   it speculative generality? What is the cost of shipping only `publish_then_destroy` +
   `no_op` and adding `destroy` when demanded?
6. **Token ↔ service reference shape.** `SessionExecutionToken` holds
   `Arc<WorkspaceSessionService>`; the service transitively reaches engine values whose
   `on_complete` closures own tokens. Trace the ownership graph for cycles and for shutdown:
   what happens to tokens dropped during daemon teardown, and can a token's detach run against
   a service that is mid-drop?
7. **Deletion audit.** The spec claims `fail_command_start`, `finalize_one_shot`,
   `ResolvedExecWorkspace`, and the one-shot helpers are deleted rather than moved. Verify
   each deletion is real (the responsibility ceased to exist) versus relocated under a new
   name — relocation is fine but must not be counted as simplification.

## Dimension 2 — Correctness, locking, concurrency at rate

Hunt for deadlocks, lost updates, and throughput collapse. For every claimed race or
deadlock, produce an explicit interleaving (thread A / thread B step schedule with the locks
each step holds). For every throughput claim, identify the serialization point and estimate
its critical-section contents (in-memory vs syscall vs disk-walk vs layerstack IO).

Mandatory traces — walk each of these end to end against the spec's §2.3 protocol before
inventing your own:

1. **Reentrancy on the sync paths.** `run_file_op` and `remount_session` hold the session
   gate for their full duration *and* now hold a token whose `Drop` calls
   `detach_execution`, which the spec says locks the same gate. std `Mutex` is non-reentrant.
   Exact drop site of the token relative to the gate guard — deadlock or not? If the spec is
   silent on drop ordering, that silence is a finding.
2. **Ledger locking vs finalize re-entry.** The ledger lives inside `WorkspaceSession`
   entries, which live inside the global `sessions: Mutex<HashMap<…>>`. `detach_execution`
   must lock the sessions map to mutate the ledger; the policy runner then calls
   `capture_session_changes` and `destroy_session`, which **both internally lock the sessions
   map** (`capture_session_changes.rs:16`, `destroy_session.rs:16`). Non-reentrant mutex:
   show whether the spec's "run policy while still holding the gate" is implementable without
   self-deadlock, and what the required lock-release discipline is. The spec's lock order is
   `gate → sessions map → storage writer` — does the detach→finalize path violate it?
3. **Global map contention at rate.** Every admit, detach, create, capture, and destroy locks
   the single global sessions map. `capture_session_changes` holds it **across the upperdir
   walk** and `destroy_session` across workspace teardown (verify against current code). At
   hundreds of bare exec_commands/sec, every completion runs capture+publish+destroy. Compute
   the consequence: does one slow capture (large upperdir, cold page cache) stall admissions
   for *all* sessions? Is this pre-existing, and does the redesign amplify it (finalize
   frequency goes from "every one-shot command" to — same? more?)? Propose the minimal fix
   the spec should mandate (e.g., clone handle out, drop map lock before IO).
4. **Publish CAS contention and silent loss.** `publish_changes` takes
   `expected_base: LayerStackRevision` — a compare-and-swap against the layerstack head. At N
   concurrent finalizes/sec, publishes race; losers get `InvalidBaseRevision`. Today's code
   swallows this (`let _ … .ok()`), and the spec keeps destroy-on-publish-failure with only an
   observability event. At rate, what fraction of bare exec_command changes is silently
   discarded? Is there a retry-with-rebase path (`base_manifest` recapture) or is data loss by
   design? If by design, the spec must say so in §2.5 in those words — check whether it does.
5. **Gate lifecycle races.** `session_gate` lazily creates gate entries and
   `drop_session_gate` removes them (`workspace_session/service/core.rs:46-60`). Construct
   the interleavings around finalize: (a) thread B clones the gate Arc, finalize destroys the
   session and drops the gate from the map, B locks the orphaned gate and resolves — clean
   `not_found`? (b) B calls `session_gate` *after* the drop — a fresh gate is created for a
   dead session id; can two threads ever hold *different* gates for the same session id while
   both proceed past resolve? (c) same id recreated later by `create_workspace_session` — can
   a stale token from the old incarnation detach against the new session's ledger? (Ids come
   from the workspace manager — check whether ids are ever reused.)
6. **Admission/finalize/destroy triangle.** Three actors on one session: a rider calling
   `admit_execution`, the watcher thread detaching the last execution, an operator calling
   guarded destroy. Enumerate all orderings and verify each terminates in a consistent state
   (session either alive-with-ledger-entry or fully destroyed; no half-finalized session, no
   finalize running twice, no publish after destroy).
7. **Yield/response ordering.** In the current engine, `promise.resolve` fires *after*
   `on_complete` returns (`engine.rs:245-270`), so a caller seeing terminal status implies
   finalize (publish) completed — read-your-writes for pipelines that exec, wait, then read
   the layerstack. Verify the redesign preserves this (detach runs inside `on_complete`), and
   check the inverse hazard: does a slow publish inside `on_complete` now block the watcher
   thread and therefore delay `promise.resolve`, `registry.complete`, and transcript
   availability at rate?
8. **Unbounded growth.** `ExecutionRegistry::complete` marks terminal but never removes
   entries (`registry.rs:71-84`); only `abort` removes. At hundreds of executions/sec,
   sustained: registry map growth, per-command scratch/transcript dirs
   (`prepare_transcript_path` — cleaned only on launch failure), gates map, watcher-thread
   spawn rate (one OS thread per execution). Which of these is a real leak at the stated
   throughput, which is bounded by something you can point at (e.g., `MAX_ACTIVE_COMMANDS =
   256` bounds concurrency but not entry count), and does the redesign change any of them?
9. **Poisoning policy.** Gate and sessions locks use `unwrap_or_else(PoisonError::into_inner)`;
   the registry uses `.expect` (panics). A finalize that panics mid-policy (e.g., inside
   capture) poisons what, and what does the next admission observe — a session that can never
   finalize again ("stuck ledger"), or a clean recovery? Does the RAII token make this better
   or worse (detach runs during unwind)?
10. **MAX_ACTIVE admission at rate.** Bursts above 256 concurrent executions hit
    `NamespaceExecutionError::Admission` — after the redesign, does a rejected reserve leak a
    ledger entry or a created-but-never-admitted session? (Order of operations in the new
    `exec_command`: create session → admit → reserve/spawn — check every failure boundary.)

## Output format

Produce a single markdown report:

1. **Verdict per dimension** — `sound` / `sound with required changes` / `unsound`, one
   paragraph each, no hedging.
2. **Findings table** — columns: ID, severity (`blocker` / `major` / `minor` / `question`),
   dimension, one-line title.
3. **Finding details** — for each: the spec section it attacks, code evidence (`file:line`),
   and for concurrency findings the explicit interleaving; for simplicity findings the
   surviving simpler alternative. Then the minimal spec amendment that resolves it.
4. **Steelman summary** — the strongest version of the spec's own argument, stated fairly,
   with what it gets right.
5. **Top 3 required changes** ranked by risk, each ≤ 3 sentences.

Rules of engagement: verify against code, not against the spec's description of the code —
the spec may misdescribe current behavior; that itself is a finding. Distinguish pre-existing
defects the redesign inherits from defects it introduces or amplifies — both are reportable,
labeled differently. Do not propose full rewrites; the review's job is to make *this* design
safe and simple, or prove it cannot be.
