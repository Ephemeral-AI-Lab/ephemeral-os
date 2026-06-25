# Adversarial Review Prompt — `docs/command-rework/spec.md`

Reusable prompt for an adversarial subagent review of the command rework spec.
Launch six reviewers in parallel (three per aspect) plus one synthesis agent.

---

## ROLE

You are an adversarial reviewer. Your job is to ATTACK the spec, not validate it.
A finding that merely agrees with the spec is worthless. Every finding must be
concrete (cite a spec section AND the real file/line), must include a proposal,
an estimated LOC delta, and a behavior-risk note.

## INPUTS (read before writing anything)

- `docs/command-rework/spec.md` (the spec under review)
- The real code it references, at minimum:
  - `crates/sandbox-runtime/operation/src/command/**.rs`
  - `crates/sandbox-runtime/operation/src/{namespace_execution.rs, services.rs, observability.rs}`
  - `crates/sandbox-runtime/operation/src/cli_definition/{command_operations.rs, workspace_session_operations.rs}`
  - `crates/sandbox-runtime/namespace-execution/src/{engine.rs, registry.rs, execution.rs, shell.rs, types.rs}`
  - `crates/sandbox-daemon/src/observability/namespace_execution.rs`
  - `crates/sandbox-runtime/operation/tests/**` (to know what behavior is asserted)

## BASELINE — settled decisions are the floor, not the ceiling

You may propose going FURTHER, but you MUST flag any proposal that:

- **(a)** reverses a settled decision (Option 2 finalization via the engine `on_complete`
  hook; single id = `NamespaceExecutionId`; `read_command_lines` infallible);
- **(b)** re-opens a resolved finding (the `175b`-before-`resolve` race; dropped
  `on_running`; the active/completed single-clock invariant);
- **(c)** breaks the public surface (`exec_command`, `write_command_stdin`,
  `read_command_lines`, `with_workspace_destroy_admission`, the DTOs, the ledger
  projection consumed by `observability_snapshot` + `ack` + the daemon `trace_record`,
  `SandboxRuntimeOperations`).

A proposal that does (a)/(b)/(c) is still allowed, but say so explicitly and justify it.

## HOW TO RUN

Launch reviewers in parallel — three per aspect, each a distinct lens below — then a
single synthesis agent that dedups, gates, and prioritizes. Reviewers do NOT see each
other's output; the synthesis sees all six.

---

## ASPECT 1 — SOLID / SRP / DECOUPLING

### Lens 1A — SRP / god-object
For every post-rework unit, name its ONE responsibility in a single sentence; if you
can't, it violates SRP. Scrutinize specifically:

- `CommandOperationService` (core.rs): orchestrator, or accreting engine + workspace +
  ledger + trace + admission + id concerns into one type?
- `finalize.rs` `build_on_complete`: it applies policy AND emits trace AND pushes the
  projection record AND destroys the workspace — one responsibility or four?
- `service/yield.rs` (merge of helpers+transcript): does it mix the waiter loop, the
  running/completed output projection, token estimation, and text rendering?
- the ledger-as-buffer (`namespace_execution.rs`): is "projection buffer" one job, or
  buffer + dedupe + partial-error capture + bounding?

Propose splits where a unit has >1 job; propose merges where two units share one job.

### Lens 1B — DIP / abstraction / hidden coupling
The spec notes command depends on CONCRETE `WorkspaceSessionService` and `Engine`.
Challenge that:

- Should command depend on trait seams (e.g. `ShellRunner` / `WorkspaceSubstrate`) so it
  is testable without the real engine and the `with_engine` test seam disappears?
- Is `on_complete` (a closure) the right abstraction, or should it be a trait the engine
  owns? Is `CommandExecValue` leaking engine/runtime internals into command?
- Hunt for hidden/temporal coupling beyond the documented `on_complete`-before-`resolve`:
  anything where correctness silently depends on call ordering or shared mutable state.

### Lens 1C — OCP / boundary law
Check the README boundary law (namespace-execution must never own workspace lifecycle;
runtime operation dispatch belongs to operation, etc.).

- Does the `on_complete` seam keep namespace-execution truly workspace-agnostic, or does
  the closure type/signature leak command concepts upward?
- Is `CommandFinalization` open for extension (publish) without modifying the runner?
- Does any responsibility land in the wrong crate after the rework?

---

## ASPECT 2 — MORE AGGRESSIVE REMOVAL / DECOUPLING / REMOVE ROUNDTRIPS

### Lens 2A — delete more
The spec stops at −719 LOC. Find what it still KEEPS that could go. Probe at least:

- `CommandTerminalResult` vs `RunnerOutcome` — is the projection struct needed, or can the
  engine's outcome flow through?
- `CompleteNamespaceExecution` / `RuntimeNamespaceExecutionSnapshot` /
  `NamespaceExecutionRecord` — three record-ish shapes; can they collapse?
- Duplicate timestamps: `started_at: Instant` AND `started_at_unix_ms: i64` in
  `CommandExecValue`.
- Two contract files (`command/contract.rs` root + `command/service/contract.rs`) — merge?
- `estimate_token_count` / `render_transcript_text` — earn their keep?
- `CommandFinalizationTraceMetadata` in observability.rs — redundant with what the closure
  already holds?
- Does `write_command_stdin` duplicate the transcript+status projection that
  `yield.rs`/read already build?

### Lens 2B — remove roundtrips
Trace the data/control hops per operation and collapse them:

- `exec_command` lifecycle: `allocate_id` → `run_shell_interactive` →
  `attach(CommandExecValue)` → later repeated `engine.with_value(...)` calls. How many lock
  acquisitions per op? Can attach happen at spawn so there's no allocate/attach gap?
- resolve workspace → `handle.entry()` → `From<WorkspaceEntry> for NamespaceTarget` →
  `build_request` — is each hop load-bearing?
- the projection record: built in the closure → pushed to ledger → drained → acked →
  re-mapped in the daemon `trace_record`. Which hops are removable?
- `observability_snapshot` crossing into `engine.live_values` AND `ledger.drain` — one pass?
- `wait_for_command_yield` polling vs the completion waiter — unnecessary wakeups?

### Lens 2C — merge/flatten & data-model slimming
Push for the smallest correct end-state.

- Should the ledger be a separate module at all, or fold into `services.rs` / the engine
  registry so there is no `NamespaceExecutionLedger` type?
- Can `CommandOutput` be slimmed (fields no consumer reads)?
- Can the 13 command/ files go lower without hurting SRP?
- Is there a structurally smaller decomposition the spec missed?

---

## SYNTHESIS (after the six reviewers)

Dedup across reviewers. For each distinct recommendation output:

```
{ title, category, verdict: adopt | adopt-with-care | defer | reject,
  rationale, est_loc_delta, behavior_risk, public_surface_impact,
  contradicts_settled (which, if any), sources }
```

Then:

- the **additional** LOC reduction beyond the spec's −719 if all "adopt" items land;
- the **top next cuts** (highest value, lowest risk) in order;
- any proposal you REJECT because it re-opens a resolved finding or breaks the public
  surface (say which).

## OUTPUT (each reviewer)

```
{ reviewer, aspect, summary,
  findings: [{ title, category, severity: must|should|nice, location, problem,
               proposal, est_loc_delta, behavior_risk, contradicts_settled, confidence }] }
```
