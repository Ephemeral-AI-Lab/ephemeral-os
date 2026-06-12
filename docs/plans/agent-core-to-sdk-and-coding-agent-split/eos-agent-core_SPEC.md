# eos-agent-core — Agent SDK Specification

- **Status:** Draft for review
- **Date:** 2026-06-13
- **Scope:** Re-shape `eos-agent-core` from "runtime + pursuit + profiles" into a
  mechanism-only **agent SDK**. Pursuit, profiles, all tools, and all host policy move to
  `eos-coding-agent` (see `eos-coding-agent_SPEC.md`, which depends on this document).
- **Related:** `docs/plans/agent-core-rust-to-typescript-migration/note/pursuit-loop-engineering/`
  (the PURSUIT operator's manual; its loop/gate vocabulary is used throughout),
  `phase-04.10-text-termination-mode_SPEC.md`, `phase-05.3-pursuit_leg_attempt_SPEC.md`.

## 1. Summary

`eos-agent-core` becomes an SDK with **one construction function, one method, two
run-scoped capabilities, one background-task contract, and zero tools**. It knows exactly
five nouns: **agent, run, tool, background task, notification**. It does not know that
advisors, subagents, planners, workers, workflows, pursuits, or sandboxes exist — those are
all host (`eos-coding-agent`) vocabulary.

The design rule that produced this surface, and the rule for keeping it stable:

> The SDK ships **capabilities**; the host ships **every tool**. When someone wants a
> built-in tool "because the SDK already has the data," the answer is to expose the missing
> capability on the call context and let the host write the tool.

## 2. Goals / Non-goals

**Goals**

- A consumer (initially `eos-coding-agent`, later other hosts) can build a complete agent
  product using only the public surface in §3.
- Loop invariants (termination gates, totality, owed-completion, single-mutator submission)
  are enforced by mechanism inside the SDK, never by host discipline.
- Terminal outcome handling is caller-injected (`agentOutcomeFn` + `onSubmit`), so a host
  state machine (pursuit) can mutate its own store transactionally at submission time with
  in-run rejection.

**Non-goals**

- Cross-language / wire transport for any contract (deferred until a non-TS consumer exists;
  the tool-call boundary is the seam that would carry it).
- Workflow hub, provider registry, MCP-style anything (host-side; see coding-agent spec).
- Sandbox integration, profile/config file formats, persistence beyond run records.
- Renaming the workspace. The SDK keeps the name **eos-agent-core**.

## 3. Public contract (complete)

This is the entire public surface. Anything not listed here is internal.

```ts
// ── construction ────────────────────────────────────────────────
export function createAgentSdk(config: AgentSdkConfig): AgentSdk;

interface AgentSdkConfig {
  llmClients: LlmClientConfig;        // provider credentials/model profiles, as objects
  hooks?: HookConfigEntry[];          // global hook entries (validated by exported schema)
  notificationRules?: TriggerRuleEntry[];
  recordsDir?: string;                // SDK writes <recordsDir>/<runId>/{events,messages}.jsonl
  taskCompletionTimeoutMs?: number;   // bounds each BackgroundTask.onCompletion; default 30_000
}

interface AgentSdk {
  createAgent(spec: AgentSpec): Agent;          // the only method
}

// ── agents & runs ───────────────────────────────────────────────
interface AgentSpec {
  name: string;
  llm: LlmRef;                        // resolves against AgentSdkConfig.llmClients
  systemPrompt: string;
  tools: ToolDefinition[];            // ALL tools arrive here — the SDK ships none
  agentOutcomeFn?: AgentOutcomeFn;    // absent → text termination mode
  maxTurns?: number;                  // default 32
  hooks?: HookConfigEntry[];          // per-agent extension of the globals
}

interface Agent {
  start_agent_run(input: { messages: UserMessage[] }): AgentRunHandle;
}

interface AgentRunHandle {
  runId: AgentRunId;
  steer(message: string): void;
  interrupt(): void;
  wait_for_agent_outcome(): Promise<AgentOutcome>;  // totality: always resolves, never rejects;
                                                    // memoized — callable any number of times,
                                                    // before or after the run finishes
  events(opts?: { afterSeq?: number }): AsyncIterable<AgentEvent>;
  backgroundTaskSupervisor: BackgroundTaskSupervisor;     // per-run, created at start
  notifier: Notifier;                                     // per-run, created at start
}

type AgentOutcome =
  | { status: "completed"; outcome: unknown }   // terminal-tool payload, or final text in text mode
  | { status: "failed"; error: { kind: "max_turns" | "provider_error" | "internal"; message: string } }
  | { status: "cancelled" };

// ── run-scoped capabilities (same instances on handle and tool ctx) ──
interface BackgroundTaskSupervisor {
  register(task: BackgroundTask): { taskId: BackgroundTaskId };
  list(): BackgroundTaskRow[];        // live tasks only — completed tasks are removed (§4.4)
  count(): number;                    // registry size = running + settling (§4.4)
  cancel(taskId: BackgroundTaskId): Promise<boolean>;
}

interface Notifier {
  publish(message: string, opts?: { key?: string }): void;   // drains at next turn boundary
}

interface ToolCallContext {
  runId: AgentRunId;
  llm_messages: readonly Message[];               // read-only snapshot of the conversation so far
  display_messages: readonly DisplayedMessage[];  // read-only snapshot of the display projection
  backgroundTaskSupervisor: BackgroundTaskSupervisor;
  notifier: Notifier;
}

// ── the background-task contract (SDK-owned) ────────────────────
interface BackgroundTask {
  toolName: string;                   // provenance, e.g. "exec_command", "run_subagent", "workflow:pursuit"
  title: string;                      // list row / human description
  cancel(): void | Promise<void>;     // idempotent; no-op after completion
  done: Promise<BackgroundTaskOutcome>;
  onCompletion?: (
    outcome: BackgroundTaskOutcome,
    ctx: { notifier: Notifier; runId: AgentRunId; taskId: BackgroundTaskId },
  ) => void | Promise<void>;
}

type BackgroundTaskOutcome = { status: "success" | "failed" | "cancelled"; outcome: string };
type BackgroundTaskRow = {
  taskId: BackgroundTaskId; toolName: string; title: string;
  startedAt: number;                  // epoch ms — no status field: a listed task IS running
};

// ── authoring ───────────────────────────────────────────────────
export function defineTool(init: ToolDefinitionInit): ToolDefinition;  // NO behavior field
export function createAgentOutcomeFn<T>(spec: {
  schema: ZodSchema<T>;
  description?: string;               // the submit tool's docstring; absent → derived from schema
  onSubmit?: (payload: T, ctx: SubmitCtx) => Promise<{ accept: T } | { reject: string }>;
}): AgentOutcomeFn;                   // default onSubmit: accept(payload) — the trivial validator
export function toSSE(events: AsyncIterable<AgentEvent>): ReadableStream;

interface SubmitCtx { runId: AgentRunId; submissionId: string /* stable = tool_use_id */ }

// ── exported types & schemas (no values) ────────────────────────
// AgentEvent (carries seq; kinds incl. turn_started, tool_execution_started/completed,
//   task_registered, task_settled, run_finished) · AgentOutcome · ToolDefinition ·
// ToolCallContext · SubmitCtx · UserMessage · Message · DisplayedMessage · AgentRunId ·
// BackgroundTaskId · BackgroundTaskRow · BackgroundTaskOutcome ·
// HookConfigEntrySchema · TriggerRuleEntrySchema
```

Notes on the shape:

- `AgentSdk` has exactly one method and no run accessors — **the handle is the only
  capability for a live run**; hosts that need by-id access keep their own
  `Map<runId, handle>`.
- `wait_for_agent_outcome()` is the only run-end channel on the handle. There is no run-end
  callback — `.then()` is the callback. The name `onSubmit` is reserved for the terminal
  handler in `createAgentOutcomeFn`, which is a different event with different powers: it
  fires at *submission time*, inside the run, and can `reject` back to a live model;
  run end is observation-only.
- "Advisor" is not an SDK concept. The outcome tool's docstring is caller-supplied via
  `description`; advisory enforcement (e.g. an advisor agent vetting submissions) is a host
  hook pattern (see coding-agent spec §7).

## 4. Semantics

### 4.1 Run lifecycle and termination

The loop is the existing `agent-loop.ts` eleven-step structure, unchanged in spirit:

1. **Terminal-tool mode** (`agentOutcomeFn` present): a tool result flagged terminal
   finishes the run with the accepted submission.
2. **Text mode** (`agentOutcomeFn` absent, Phase 04.10): a bare-text assistant turn finishes
   the run when the gate is open —
   `calls == 0 ∧ no pending steers ∧ backgroundTaskSupervisor.count() == 0` —
   with `outcome = assistantText(final_message)`.
3. **Gate parity:** a terminal submission attempted while
   `backgroundTaskSupervisor.count() > 0` is denied in-run (internal submission gate; same
   predicate as the text-exit gate). This gate is SDK-internal mechanism, not a configurable
   hook — hosts tune nothing here.
4. **Park:** `calls == 0 ∧ backgroundTaskSupervisor.count() > 0` → the run parks
   (auto-wait). Wake sources: any inbox publish **and any task removal** (completion wakes
   the loop even if the host publishes nothing).
5. **Backstops:** `maxTurns` → `failed {kind: "max_turns"}`; `interrupt()` → `cancelled`;
   provider/internal errors → `failed`.
6. **Totality:** `wait_for_agent_outcome()` always resolves. A crashed agent yields a
   synthesized `failed`; there is no path that leaves a caller hanging.

### 4.2 Terminal contract (`createAgentOutcomeFn`)

- The terminal tool is an ordinary tool whose successful result ends the run. The SDK
  validates the payload against `schema`; shape errors return `{ok:false}` to the model
  in-run.
- `onSubmit` is the caller-owned submission handler, invoked **at submission time, before
  the run finishes**:
  - `{accept: payload'}` → transaction committed by the host inside the handler; the run
    finishes with `payload'`. The accepted value and host state cannot disagree.
  - `{reject: reason}` → returned to the live model as a tool error; the run continues.
    **A rejection costs the host nothing** (no attempt burned, no state mutated).
- `ctx.submissionId` is stable per submission attempt (the tool_use_id). Hosts MUST key
  transactional transitions on it so handler retries are idempotent.
- Default `onSubmit` is `accept(payload)` — the trivial schema-validate-and-echo case
  requires zero host code.
- Death/cancel never invoke `onSubmit` (there is no payload); hosts observe those at the
  `wait_for_agent_outcome()` boundary.

### 4.3 Tools and hooks

- The SDK ships the authoring contract (`defineTool`, `ToolDefinition`, `ToolCallContext`),
  the batch executor, and the hook engine. It ships **zero tool implementations** — no
  subagent, advisor, transcript, background-task, or workflow tools.
- There is **no `behavior` field**. Foreground / background / yield are runtime patterns of
  `execute`, not declared metadata, and the engine never branches on them:
  - *foreground:* do the work, resolve the final output;
  - *background:* start the work, `register(...)` a task, resolve `{taskId}` immediately;
  - *yield:* run to a yield point (timeout / quiet period / output cap); if finished,
    resolve the final output; if still running, register and resolve partial output + taskId.
  The unifying rule: **a tool call resolves exactly one turn-result and may leave behind at
  most one registered background task.** `executeBatch` is untouched by any of this.
- `llm_messages` / `display_messages` on `ToolCallContext` are **read-only snapshots** taken
  at batch start. They grant every tool read access to the full conversation — a deliberate
  capability (compaction, transcript-aware tools); hosts that run third-party tools should
  treat it as part of their trust decision.
- Hooks: engine and protocol (pre/post tool-call, deny/passthrough, callback or subprocess
  command via the JSON-on-stdin mechanics) are SDK; entries are host-supplied
  (`AgentSdkConfig.hooks` global, `AgentSpec.hooks` per-agent). Hook matching is by tool
  name. Hook command failures follow the existing protocol (non-zero exit conventions); the
  engine never lets a broken hook wedge a batch.

### 4.4 Background tasks

- One `BackgroundTaskSupervisor` and one `Notifier` are created per run at start. The
  **same instances** appear on the handle (host side) and on every `ToolCallContext`
  (tool side). Handles are per-run scoped: a tool can only see and cancel its own run's
  tasks.
- Public supervisor surface is exactly `register / list / count / cancel`. The park/exit
  gates read the same registry internally and are **not** on the interface — no host code
  can put the loop in a state the gates can't see.
- **Registry lifecycle — the registry IS the open set:**

  ```
  register → running → (done resolves) → settling → removed
                                          │
                                          └─ onCompletion invoked once, awaited,
                                             bounded by taskCompletionTimeoutMs
  ```

  A task is **removed from the registry the moment its `onCompletion` finishes** (returns,
  throws, or times out). Removal wakes the loop. `count()` is the registry size
  (running + settling); `list()` shows live tasks only. There is no status field and no
  settled rows — **history lives in the event stream** (`task_registered`,
  `task_settled`), not the registry.
- **Settlement → completion handler, never → notifier.** On `done` resolving, the
  supervisor does exactly one thing: invoke `onCompletion(outcome, {notifier, runId, taskId})`
  once, awaited. The supervisor itself **never publishes**. No `onCompletion` → the task is
  removed immediately and silently.
- **Silent completions leave no model-visible trace.** A removed task is absent from
  `list()` and produced no notification — the model cannot discover it at all. The
  convention is therefore hard: **any task the model is expected to await MUST publish in
  its `onCompletion`; silence is strictly for fire-and-forget work.**
- A throwing or timed-out `onCompletion` must not wedge the run: the SDK removes the task,
  writes the error to the events stream and records, and does nothing else (no fallback
  notification — silence is the configured default). The bound is
  `taskCompletionTimeoutMs` (default 30s).
- `cancel(taskId)`: returns `true` iff it transitioned a running task to cancelling;
  returns `false` when the task is not found (already completed and removed) or already
  settling. Cancellation loses the race to completion by design. Cancelled tasks flow
  through the same `onCompletion` with `status: "cancelled"` — one completion path, no
  special cases.
- **Run-end disposal:** a clean exit already guarantees an empty registry (the exit gate),
  but `interrupt()` and `failed` runs can terminate with live tasks. On any terminal run
  outcome the supervisor cancels still-running tasks, invokes their `onCompletion` with
  `status: "cancelled"` (for side-effect cleanup — publishes after run end are no-ops), and
  removes them. No task survives its run.

### 4.5 Notifications

- `Notifier.publish` enqueues; the loop drains the inbox into the conversation at the next
  turn boundary. Publishing never interrupts a streaming turn.
- **Who publishes:** the SDK publishes **only notification-rule firings** (host-authored
  rule content, fired by the trigger engine from turn facts). Everything else — background
  task completion messages, external/app events, supplementary tool info — is the host
  calling `publish` itself (typically inside `BackgroundTask.onCompletion`).
- Exhaustiveness property a host may rely on: every message in an agent's inbox is either
  (a) a publish the host made, or (b) a firing of a rule the host configured. The SDK
  injects nothing else.

### 4.6 Records

- If `recordsDir` is set, the SDK writes `<recordsDir>/<runId>/events.jsonl` and
  `messages.jsonl` itself — lossless from the first line (wired at construction, not at
  subscription). `recordsDir` is the only filesystem path in the public surface, and it is
  received, never discovered.
- `handle.events({afterSeq})` is the live-observation channel (UI/SSE; resumable via the
  `seq` field on every event → SSE `Last-Event-ID`). Records are the durable channel
  (audit/replay). They are different consumers; neither substitutes for the other.
- Background-task lifecycle is part of both channels: `task_registered` and `task_settled`
  (with the outcome) are events. With the registry ephemeral (§4.4), this is the only
  durable task history.

### 4.7 Configuration

- The SDK exports the **schemas** (`HookConfigEntrySchema`, `TriggerRuleEntrySchema`) and
  accepts **parsed objects**. File discovery, layering, merging, and watching are host
  concerns. The SDK never reads a config file.

## 5. Internal architecture

Current `eos-agent-core/packages/*` disposition. "Internal" packages keep their boundaries
for the SDK's own hygiene but are not published; only the root package is public.

| Package | Disposition |
|---|---|
| `contracts` | internal · **minus** `pursuit.ts` (moves to coding agent) and `sandboxIdFrom` in `ids.ts` (host concept; replace with opaque execution-context id or delete) |
| `llm-client` | internal, unchanged (access/, wires/, retry, stream-client) |
| `scripts` | internal, unchanged (`executeJsonCommand` powers hook/trigger subprocess commands) |
| `notification` | internal (inbox, loop-observer, trigger engine) |
| `background` | internal · rename `BackgroundSessionSupervisor` → `BackgroundTaskSupervisor`; **remove-on-completion registry** — `count(): number` (registry size), rows `{taskId, toolName, title, startedAt}`, no status enum, no source-specific session typing |
| `engine` | internal (agent-loop, conversation, turn, tool-executor port, run handle) · gains the internal terminal-submission gate aligned with the text-exit gate · emits `task_registered`/`task_settled` events |
| `tool` | internal: `contract / define / executor / pipeline / toolset / run-state / hooks/*` stay; **new** `outcome.ts` (`createAgentOutcomeFn`); **deleted:** `tools/*` (all families — agent, background, pursuit, submission), `advisory_prompts/*`, `description_prompts/*` (host-side now, or replaced by the factory) |
| `agent-runtime` | **split**: assembly (`runtime.ts` minus pursuit wiring), `run-registry.ts`, `transcript.ts`, `llm-client-registry.ts` stay internal under a `runtime` package · config loaders (`config-root/config-file/hook-config/notification-rules-config`), profile loaders/registry, `pursuit-context-scripts.ts`, and `pursuitWiring()` move to `eos-coding-agent` |
| `pursuit` | moves to `eos-coding-agent/packages/workflows/pursuit` |
| `db` | moves with pursuit (it is `createPursuitDatabase`) |
| `testkit` | split: scripted `LlmClient`, `scripted-tools`, `transcript-fixture` stay; `.eos-agents` fixture building moves to the coding agent |

Deleted concepts (not moved): `PursuitAgentSubmissionBinding` (replaced by `onSubmit`),
the profile-kind strictness table (planner/worker terminal-tool enforcement moves into
pursuit's own startup validation), per-name submission tools, `behavior` metadata,
`RunRecorder` public port (now `recordsDir`), `getRun`, run-end callback on the handle,
task status enum / settled rows.

## 6. Invariants (regression tests to write first)

1. **Totality** — `wait_for_agent_outcome()` resolves for every run: completed, failed
   (incl. synthesized death), or cancelled; it never rejects.
2. **Single mutator** — `onSubmit` is the only writer at submission; an accepted submission
   and host state cannot diverge (handler commits before the run finishes).
3. **Idempotent submission** — replaying `onSubmit` with the same `submissionId` is a no-op.
4. **Free rejection** — `{reject}` reaches the live model and consumes no host budget.
5. **Gate parity** — text-exit gate and terminal-submission gate evaluate the same
   `backgroundTaskSupervisor.count() == 0` predicate.
6. **Owed completion** — a run cannot finish while the registry is non-empty; `onCompletion`
   is bounded by `taskCompletionTimeoutMs`; a throw or timeout removes the task and records
   the error — it never wedges the run.
7. **Silent default** — with no `onCompletion`, completion removes the task immediately and
   publishes nothing; with one, the SDK invokes it exactly once.
8. **Completion wake** — a parked run wakes on task removal even with an empty inbox.
9. **Cancel race** — `cancel` returns `true` only for a running task; after completion the
   task is not found, `cancel` returns `false` and changes nothing.
10. **Lossless records** — `events.jsonl`/`messages.jsonl` contain every line from seq 0,
    including `task_registered`/`task_settled`, regardless of when (or whether) anyone
    subscribed to `events()`.
11. **Exhaustive inbox** — no inbox message originates inside the SDK except configured
    trigger-rule firings.
12. **Run-end disposal** — a terminating run (any outcome) cancels its running tasks, runs
    their `onCompletion` with `status:"cancelled"`, and leaves an empty registry.

## 7. Acceptance criteria (leak checks)

- `grep -r "@eos/pursuit"` in the SDK → 0 hits; `grep -ri "pursuit\|planner\|worker\|advisor\|subagent\|workflow\|sandbox"`
  over public types → 0 hits.
- The supervisor source contains no `toolName`-specific branches.
- `AgentSdkConfig` / `AgentSpec` / `AgentRunHandle` mention no filesystem path except `recordsDir`.
- A consumer can implement `run_subagent`, `ask_advisor`, `list_background_task`,
  `cancel_background_task`, a transcript reader, and a workflow hub **using only §3** —
  this is the proof the surface is sufficient (demonstrated in the coding-agent spec).
- `AgentSdk` has exactly one method.

## 8. Decision log

| Decision | Resolution (supersedes earlier drafts) |
|---|---|
| SDK name | Keep **eos-agent-core** (no rename to eos-agent-sdk) |
| Built-in tools | **None.** Earlier carve-outs (subagent/advisor tools, background-task tools, workflow toolset) all reversed; capabilities on `ToolCallContext` instead |
| Workflow hub | Host-side (`eos-coding-agent`); `WorkflowProvider` is a host contract |
| Advisor / subagent | Host patterns (registry + tools + advisor-gate hook); SDK has no such concepts; outcome tool docstring is `description` |
| `behavior` metadata | Removed; runtime patterns only |
| Settlement notifications | Supervisor **never** publishes; `BackgroundTask.onCompletion` (host) owns publication, receives `notifier` as an argument; exit gate = "owed completion handler" |
| `onSettled` | Renamed `onCompletion`; the SDK is the listener (it gates task removal on the callback finishing) |
| Task registry | **Remove-on-completion:** registry = the open set; `count(): number`; rows `{taskId, toolName, title, startedAt}` with no status enum; history via `task_registered`/`task_settled` events; run-end disposal cancels survivors |
| Run-end channel | `wait_for_agent_outcome(): Promise<AgentOutcome>` on the handle; run-end callback dropped (`onSubmit` name reserved for the terminal handler) |
| Records | `recordsDir: string` config; `RunRecorder` port internal-only |
| Facade | `AgentSdk = { createAgent }` only; `getRun`/sdk-level accessors removed (handle owns them) |
| Pursuit launch seam | Pursuit consumes the SDK directly; `AgentLaunchPort`/`LaunchSettlement` deleted (trade acknowledged: pursuit tests use SDK testkit instead of a fake port) |
| `backgroundSession` | Renamed `backgroundTask` throughout |
| AgentSpec | `systemPrompt` explicit; `outcome` → `agentOutcomeFn` |
| Conversation access | `llm_messages` / `display_messages` read-only snapshots on `ToolCallContext` |

## 9. Open questions

- `LlmRef` resolution shape (string id vs structured ref) — decide when wiring
  `llm-client-registry` into the new `runtime` package.
- Whether `events()` late-subscription replays from seq 0 in-memory or reads back from
  records when `recordsDir` is set (current lean: in-memory ring up to a cap; records are
  the unbounded source).
- Stream deltas (token-level events) in `AgentEvent` — out of scope for the split; revisit
  for UI needs.
- Public naming convention is currently mixed (`start_agent_run` / `wait_for_agent_outcome` /
  `llm_messages` snake_case vs `createAgent` / `backgroundTaskSupervisor` camelCase) —
  normalize one way before implementation begins.
- `AgentOutcome.outcome` / `BackgroundTaskOutcome.outcome` read as `o.outcome` at call
  sites — acceptable, or rename to `result`? (current spec keeps `outcome`).
