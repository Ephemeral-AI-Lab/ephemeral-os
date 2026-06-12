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
run-scoped capabilities, one background-task contract, one hook engine, and zero tools**.
It knows exactly six nouns: **agent, run, tool, background task, notification, hook**. It
does not know that advisors, subagents, planners, workers, workflows, pursuits, or
sandboxes exist — those are all host (`eos-coding-agent`) vocabulary.

Two rules produced this surface, and keep it stable:

> **The capability razor.** The SDK ships **capabilities**; the host ships **every tool**.
> When someone wants a built-in tool "because the SDK already has the data," the answer is
> to expose the missing capability on the call context and let the host write the tool.

> **One channel per signal.** Hook decisions ride the tool-result channel (synchronous,
> attributed to one call). Everything asynchronous rides the notifier (inbox, drained at
> turn boundaries). No signal has two paths; no path carries two kinds of signal.

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

- Cross-language / wire transport for any contract — including SSE helpers. `events()` is
  an in-process iterable; hosts own any encoding (deferred until a non-TS consumer exists;
  the tool-call boundary is the seam that would carry it).
- Subprocess execution. The SDK never spawns a process; hosts wrap commands into callback
  hooks.
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
  hooks?: HookEntry[];                // global hook entries — callbacks only
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
  hooks?: HookEntry[];                // per-agent extension of the globals
}

interface Agent {
  /** Reusable template: any number of calls, concurrent runs allowed. */
  start(input: { messages: UserMessage[] }): AgentRunHandle;
}

interface AgentRunHandle {
  runId: AgentRunId;
  steer(message: UserMessage): boolean;     // false once finishing has begun
  interrupt(): void;
  outcome(): Promise<AgentOutcome>;   // totality: always resolves, never rejects;
                                      // memoized — callable any number of times,
                                      // before or after the run finishes
  events(): AsyncIterable<AgentEvent>;      // live-only, single consumer; seq on every event
  backgroundTaskSupervisor: BackgroundTaskSupervisor;     // per-run, created at start
  notifier: Notifier;                                     // per-run, created at start
}

type AgentOutcome = {
  usage: UsageSnapshot;               // summed across completed turns
  turns: number;
} & (
  | { status: "completed"; outcome: unknown }   // terminal-tool payload, or final text in text mode
  | { status: "failed"; error: { kind: "max_turns" | "provider_error" | "internal"; message: string } }
  | { status: "cancelled" }
);

// ── run-scoped capabilities (same instances on handle and tool ctx) ──
interface BackgroundTaskSupervisor {
  register(task: BackgroundTask): { taskId: BackgroundTaskId };
  list(): BackgroundTaskRow[];        // live tasks only — completed tasks are removed (§4.4)
  count(): number;                    // registry size = running + settling (§4.4)
  cancel(taskId: BackgroundTaskId): Promise<boolean>;
}

interface Notifier {
  /** Drains at the next turn boundary. An undrained message with the same
      key is replaced (coalesce); key has no other meaning. */
  publish(message: string, opts?: { key?: string }): void;
}

interface ToolCallContext {
  runId: AgentRunId;
  toolUseId: ToolUseId;                          // event/record correlation; idempotency keying
  signal: AbortSignal;                           // aborts on interrupt()
  llmMessages: readonly Message[];               // read-only snapshot of the conversation so far
  displayMessages: readonly DisplayedMessage[];  // read-only snapshot of the display projection
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

// ── hooks (the one extension engine) ────────────────────────────
type HookEntry =
  | { event: "preToolUse";  matcher?: HookMatcher;
      run: (call: ToolCallFacts) => HookDecision | Promise<HookDecision> }
  | { event: "postToolUse"; matcher?: HookMatcher;
      run: (call: ToolCallFacts, result: ToolResult) => HookDecision | Promise<HookDecision> }
  | { event: "turnBoundary";                     // where host notification rules live
      run: (turn: TurnFacts, ctx: { notifier: Notifier; runId: AgentRunId }) => void | Promise<void> };

interface HookMatcher { toolName?: string }
type HookDecision = { decision: "passthrough" } | { decision: "deny"; reason: string };
type ToolCallFacts = {
  readonly runId: AgentRunId; readonly toolUseId: ToolUseId;
  readonly toolName: string; readonly input: JsonObject;
};

// ── authoring ───────────────────────────────────────────────────
export function defineTool<I>(init: ToolDefinitionInit<I>): ToolDefinition<I>;  // NO behavior field

interface ToolDefinitionInit<I> {
  name: string;
  description: string;
  input: ZodType<I>;
  execute(input: I, ctx: ToolCallContext): Promise<ToolResult>;
}
type ToolResult =
  | { output: JsonValue; metadata?: JsonObject }   // engine stringifies once
  | { error: string };

export function createAgentOutcomeFn<T>(spec: {
  name: string;                       // the terminal tool the model calls and hook matchers see,
                                      //   e.g. "submit_main_outcome"
  description?: string;               // the submit tool's docstring; absent → derived from schema
  schema: ZodSchema<T>;
  onSubmit?: (payload: T, ctx: SubmitCtx) => Promise<{ accept: T } | { reject: string }>;
}): AgentOutcomeFn;                   // default onSubmit: accept(payload) — the trivial validator

interface SubmitCtx { runId: AgentRunId; submissionId: string /* stable = toolUseId */ }

// ── exported types (no values, no schemas) ──────────────────────
// AgentEvent (carries seq; kinds incl. turn_started, tool_execution_started/completed,
//   task_registered, task_settled, run_finished) · AgentOutcome · UsageSnapshot ·
// AgentOutcomeFn (opaque; minted only by createAgentOutcomeFn) · ToolDefinition ·
// ToolDefinitionInit · ToolResult · ToolCallContext · ToolCallFacts · TurnFacts ·
// SubmitCtx · UserMessage · Message · DisplayedMessage · AgentRunId · ToolUseId ·
// BackgroundTaskId · BackgroundTaskRow · BackgroundTaskOutcome ·
// HookEntry · HookMatcher · HookDecision
```

Notes on the shape:

- `AgentSdk` has exactly one method and no run accessors — **the handle is the only
  capability for a live run**; hosts that need by-id access keep their own
  `Map<runId, handle>`.
- `outcome()` is the only run-end channel on the handle. There is no run-end callback —
  `.then()` is the callback. The name `onSubmit` is reserved for the terminal handler in
  `createAgentOutcomeFn`, which is a different event with different powers: it fires at
  *submission time*, inside the run, and can `reject` back to a live model; run end is
  observation-only.
- "Advisor" is not an SDK concept. The outcome tool's identity is caller-supplied (`name`,
  `description`); advisory enforcement (e.g. an advisor agent vetting submissions) is a
  host hook pattern (see coding-agent spec §7).
- "Notification rules" are not an SDK concept. A host notification rule is a
  `turnBoundary` hook entry that publishes; the host compiles its rule files into such
  entries (§4.3, §4.7).
- There are no exported Zod schemas. Hooks are callbacks; the host owns config files,
  validates them itself, and wraps any subprocess command into a callback (the
  JSON-on-stdin runner moves to the host with `scripts`).
- One-shot LLM completions are deliberately absent: the blessed pattern is a one-turn
  text-mode agent. Hosts must not grow a second LLM client stack beside `llmClients`.

## 4. Semantics

### 4.1 Run lifecycle and termination

The loop is the existing `agent-loop.ts` eleven-step structure, unchanged in spirit:

1. **Terminal-tool mode** (`agentOutcomeFn` present): a tool result flagged terminal
   finishes the run with the accepted submission.
2. **Text mode** (`agentOutcomeFn` absent, Phase 04.10): a bare-text assistant turn finishes
   the run when the gate is open, with `outcome = assistantText(final_message)`.
3. **The gate** (one predicate, both exits):
   `calls == 0 ∧ no pending steers ∧ backgroundTaskSupervisor.count() == 0 ∧ inbox drained`.
   A terminal submission attempted while the gate is closed is denied in-run. The gate is
   SDK-internal mechanism, not a configurable hook — hosts tune nothing here. The
   `inbox drained` conjunct is load-bearing: without it, a message published during
   settlement could be stranded by a text exit or accepted submission at the same boundary.
4. **Park:** `calls == 0 ∧ no pending steers ∧ inbox drained ∧ count() > 0` → the run
   parks (auto-wait). Wake sources: any inbox publish, any task removal (completion wakes
   the loop even if the host publishes nothing), any steer, and interrupt.
5. **Empty wake:** a wake whose drain yields nothing (a silent task removal) re-evaluates
   the gate. In text mode the run then completes with the existing final text; in
   terminal-tool mode the loop re-prompts the model. The §4.4 convention — awaited tasks
   MUST publish — exists precisely so awaited work never takes this path.
6. **Backstops:** `maxTurns` → `failed {kind: "max_turns"}`; `interrupt()` → `cancelled`;
   provider/internal errors → `failed`.
7. **Totality:** `outcome()` always resolves. A crashed agent yields a synthesized
   `failed`; there is no path that leaves a caller hanging.

### 4.2 Terminal contract (`createAgentOutcomeFn`)

- The factory owns the terminal tool's identity: `name` is what the model calls and what
  hook matchers see (the host's advisor gate matches it); `description` is its docstring.
- The terminal tool is an ordinary tool whose successful result ends the run. The SDK
  validates the payload against `schema`; shape errors return an error result to the model
  in-run.
- `onSubmit` is the caller-owned submission handler, invoked **at submission time, before
  the run finishes**:
  - `{accept: payload'}` → transaction committed by the host inside the handler; the run
    finishes with `payload'`. The accepted value and host state cannot disagree.
  - `{reject: reason}` → returned to the live model as a tool error; the run continues.
    **A rejection costs the host nothing** (no attempt burned, no state mutated).
- `ctx.submissionId` is stable per submission attempt (the toolUseId). Hosts MUST key
  transactional transitions on it so handler retries are idempotent.
- Default `onSubmit` is `accept(payload)` — the trivial schema-validate-and-echo case
  requires zero host code.
- Death/cancel never invoke `onSubmit` (there is no payload); hosts observe those at the
  `outcome()` boundary.

### 4.3 Tools and hooks

- The SDK ships the authoring contract (`defineTool`, `ToolDefinitionInit`, `ToolResult`,
  `ToolCallContext`), the batch executor, and the hook engine. It ships **zero tool
  implementations** — no subagent, advisor, transcript, background-task, or workflow tools.
- There is **no `behavior` field**. Foreground / background / yield are runtime patterns of
  `execute`, not declared metadata, and the engine never branches on them:
  - *foreground:* do the work, resolve the final output;
  - *background:* start the work, `register(...)` a task, resolve `{taskId}` immediately;
  - *yield:* run to a yield point (timeout / quiet period / output cap); if finished,
    resolve the final output; if still running, register and resolve partial output + taskId.
  The unifying rule: **a tool call resolves exactly one turn-result and may leave behind at
  most one registered background task.** `executeBatch` is untouched by any of this.
- `llmMessages` / `displayMessages` on `ToolCallContext` are **read-only snapshots** taken
  at batch start. They grant every tool read access to the full conversation — a deliberate
  capability (compaction, transcript-aware tools); hosts that run third-party tools should
  treat it as part of their trust decision.
- **Hooks are callbacks on three events, matched by tool name.** Channel discipline
  ("one channel per signal"):
  - `preToolUse` deny → the call never executes; the model receives the deny `reason` as
    that call's tool-result error, in-run.
  - `postToolUse` deny → the executed result is replaced by an error carrying `reason`.
  - `turnBoundary` → runs at the inbox-drain boundary, observes turn facts, and may
    `publish` through the provided notifier; it returns nothing. This event is where host
    notification rules live — the host compiles its rule files into these callbacks.
  Pre/post hooks do not receive the notifier; the decision is their only output. A
  throwing pre/post hook resolves as `deny` with the thrown message (fail-closed; a host
  wanting fail-open catches inside its callback); a throwing `turnBoundary` hook is
  recorded and skipped. A broken hook never wedges a batch.

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
  its `onCompletion`; silence is strictly for fire-and-forget work** (see §4.1 "empty
  wake" for what happens when a silent removal opens the gate).
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
  turn boundary. Publishing never interrupts a streaming turn. An undrained message with
  the same `key` is replaced.
- **Who publishes: only the host.** From tools, from `BackgroundTask.onCompletion`, from
  `turnBoundary` hooks (its notification rules), or through the handle's `notifier` for
  external/app events. The SDK itself never publishes.
- Exhaustiveness property a host may rely on: **every message in an agent's inbox is a
  publish the host made.** The SDK injects nothing.

### 4.6 Records

- If `recordsDir` is set, the SDK writes `<recordsDir>/<runId>/events.jsonl` and
  `messages.jsonl` itself — lossless from the first line (wired at construction, not at
  subscription). `recordsDir` is the only filesystem path in the public surface, and it is
  received, never discovered.
- `handle.events()` is the live-observation channel: single-consumer, no replay. Every
  event carries `seq`; **resume and replay are served from records** (a reconnecting
  consumer reads the gap from `events.jsonl`, then attaches live). The channels have
  different consumers; neither substitutes for the other.
- Background-task lifecycle is part of both channels: `task_registered` and `task_settled`
  (with the outcome) are events. With the registry ephemeral (§4.4), this is the only
  durable task history.

### 4.7 Configuration

- The SDK accepts **parsed objects and callbacks**. File discovery, parsing, validation,
  layering, merging, watching, and subprocess wrapping are host concerns. The SDK never
  reads a config file and never spawns a process.

## 5. Internal architecture

Current `eos-agent-core/packages/*` disposition. "Internal" packages keep their boundaries
for the SDK's own hygiene but are not published; only the root package is public.

| Package | Disposition |
|---|---|
| `contracts` | internal · **minus** `pursuit.ts` (moves to coding agent) and `sandboxIdFrom` in `ids.ts` (host concept; replace with opaque execution-context id or delete) |
| `llm-client` | internal, unchanged (access/, wires/, retry, stream-client) |
| `scripts` | **moves to `eos-coding-agent`** (`executeJsonCommand` powers the host's subprocess→callback hook wrapping) |
| `notification` | internal (inbox, loop-observer) · **trigger engine deleted** — rule evaluation compiles host-side into `turnBoundary` hook callbacks; loop-observer's turn-fact extraction feeds `turnBoundary` dispatch |
| `background` | internal · rename `BackgroundSessionSupervisor` → `BackgroundTaskSupervisor`; **remove-on-completion registry** — `count(): number` (registry size), rows `{taskId, toolName, title, startedAt}`, no status enum, no source-specific session typing |
| `engine` | internal (agent-loop, conversation, turn, tool-executor port, run handle) · gains the internal terminal-submission gate aligned with the text-exit gate (incl. the `inbox drained` conjunct) · emits `task_registered`/`task_settled` events |
| `tool` | internal: `contract / define / executor / pipeline / toolset / run-state` stay; `hooks/*` reduced to callback dispatch (the subprocess protocol moves to the host); **new** `outcome.ts` (`createAgentOutcomeFn`); **deleted:** `tools/*` (all families — agent, background, pursuit, submission), `advisory_prompts/*`, `description_prompts/*` (host-side now, or replaced by the factory) |
| `agent-runtime` | **split**: assembly (`runtime.ts` minus pursuit wiring), `run-registry.ts`, `transcript.ts`, `llm-client-registry.ts` stay internal under a `runtime` package · config loaders (`config-root/config-file/hook-config/notification-rules-config`), profile loaders/registry, `pursuit-context-scripts.ts`, and `pursuitWiring()` move to `eos-coding-agent` |
| `pursuit` | moves to `eos-coding-agent/packages/workflows/pursuit` |
| `db` | moves with pursuit (it is `createPursuitDatabase`) |
| `testkit` | split: scripted `LlmClient`, `scripted-tools`, `transcript-fixture` stay; `.eos-agents` fixture building moves to the coding agent |

Deleted concepts (not moved): `PursuitAgentSubmissionBinding` (replaced by `onSubmit`),
the profile-kind strictness table (planner/worker terminal-tool enforcement moves into
pursuit's own startup validation), per-name submission tools (identity now comes from
`createAgentOutcomeFn`), `behavior` metadata, `RunRecorder` public port (now `recordsDir`),
`getRun`, run-end callback on the handle, task status enum / settled rows, the trigger
engine and `TriggerRuleEntry`, subprocess hook commands and exported config schemas,
`toSSE` and `events({afterSeq})` replay.

## 6. Invariants (regression tests to write first)

1. **Totality** — `outcome()` resolves for every run: completed, failed (incl. synthesized
   death), or cancelled; it never rejects.
2. **Single mutator** — `onSubmit` is the only writer at submission; an accepted submission
   and host state cannot diverge (handler commits before the run finishes).
3. **Idempotent submission** — replaying `onSubmit` with the same `submissionId` is a no-op.
4. **Free rejection** — `{reject}` reaches the live model and consumes no host budget.
5. **Gate parity** — text-exit gate and terminal-submission gate evaluate the same
   predicate: `count() == 0 ∧ inbox drained` (plus no calls, no pending steers).
6. **Owed completion** — a run cannot finish while the registry is non-empty; `onCompletion`
   is bounded by `taskCompletionTimeoutMs`; a throw or timeout removes the task and records
   the error — it never wedges the run.
7. **Silent default** — with no `onCompletion`, completion removes the task immediately and
   publishes nothing; with one, the SDK invokes it exactly once.
8. **Completion wake** — a parked run wakes on task removal even with an empty inbox; the
   empty-wake continuation is the defined §4.1 behavior, never a hang.
9. **Cancel race** — `cancel` returns `true` only for a running task; after completion the
   task is not found, `cancel` returns `false` and changes nothing.
10. **Lossless records** — `events.jsonl`/`messages.jsonl` contain every line from seq 0,
    including `task_registered`/`task_settled`, regardless of when (or whether) anyone
    consumed `events()`.
11. **Exhaustive inbox** — no inbox message originates inside the SDK; every message is a
    host publish (tools, `onCompletion`, `turnBoundary` hooks, or the handle).
12. **Run-end disposal** — a terminating run (any outcome) cancels its running tasks, runs
    their `onCompletion` with `status:"cancelled"`, and leaves an empty registry.
13. **One channel** — hook decisions never appear in the inbox; notifier content never
    alters a tool result.

## 7. Acceptance criteria (leak checks)

- `grep -r "@eos/pursuit"` in the SDK → 0 hits; `grep -ri "pursuit\|planner\|worker\|advisor\|subagent\|workflow\|sandbox"`
  over public types → 0 hits.
- `grep -r "child_process"` in the SDK → 0 hits (no subprocess execution).
- The supervisor source contains no `toolName`-specific branches and no status enum.
- `AgentSdkConfig` / `AgentSpec` / `AgentRunHandle` mention no filesystem path except `recordsDir`.
- A consumer can implement `run_subagent`, `ask_advisor`, `list_background_task` (live
  rows only), `cancel_background_task`, a transcript reader over records, an advisor gate
  (`preToolUse`), its notification rules (`turnBoundary`), and a workflow hub **using only
  §3** — this is the proof the surface is sufficient (demonstrated in the coding-agent
  spec).
- Every identifier used in the coding-agent spec's code snippets exists verbatim in §3.
- `AgentSdk` has exactly one method.

## 8. Decision log

| Decision | Resolution (supersedes earlier drafts) |
|---|---|
| SDK name | Keep **eos-agent-core** (no rename to eos-agent-sdk) |
| Built-in tools | **None.** Earlier carve-outs (subagent/advisor tools, background-task tools, workflow toolset) all reversed; capabilities on `ToolCallContext` instead |
| Workflow hub | Host-side (`eos-coding-agent`); `WorkflowProvider` is a host contract |
| Advisor / subagent | Host patterns (registry + tools + advisor-gate hook); SDK has no such concepts; outcome tool identity is `name` + `description` |
| `behavior` metadata | Removed; runtime patterns only |
| Settlement notifications | Supervisor **never** publishes; `BackgroundTask.onCompletion` (host) owns publication, receives `notifier` as an argument; exit gate = "owed completion handler" |
| `onSettled` | Renamed `onCompletion`; the SDK is the listener (it gates task removal on the callback finishing) |
| Task registry | **Remove-on-completion:** registry = the open set; `count(): number`; rows `{taskId, toolName, title, startedAt}` with no status enum; history via `task_registered`/`task_settled` events; run-end disposal cancels survivors |
| Run-end channel | `outcome(): Promise<AgentOutcome>` on the handle; run-end callback dropped (`onSubmit` name reserved for the terminal handler) |
| Outcome payload | `AgentOutcome` carries `usage` and `turns` on every status (hosts must not parse records for hot-path accounting) |
| Notification rules | Deleted as an SDK concept; the trigger engine folds into the `turnBoundary` hook event; hosts compile their rule files into callbacks |
| Hook transport | Callbacks only; subprocess JSON-on-stdin moves to the host (with `scripts`); no exported config schemas |
| Hook channels | pre/post speak only through `HookDecision` (deny → that call's tool-result error); `turnBoundary` speaks only through the notifier |
| Records | `recordsDir: string` config; `RunRecorder` port internal-only |
| Events | Live-only single-consumer `events()` with `seq`; no `afterSeq` replay, no `toSSE` — records serve resume |
| Facade | `AgentSdk = { createAgent }` only; `getRun`/sdk-level accessors removed (handle owns them) |
| Tool context | `signal` restored (interrupt must reach in-flight `execute`); `toolUseId` added; `llmMessages`/`displayMessages` read-only snapshots kept as a deliberate capability |
| `steer` | Takes `UserMessage`, returns `boolean` (false once finishing has begun) |
| Tool results | `ToolResult` is `{output, metadata?}` or `{error}`; the engine stringifies once |
| Pursuit launch seam | Pursuit consumes the SDK directly; `AgentLaunchPort`/`LaunchSettlement` deleted (trade acknowledged: pursuit tests use SDK testkit instead of a fake port) |
| `backgroundSession` | Renamed `backgroundTask` throughout |
| AgentSpec / naming | `systemPrompt` explicit; `outcome` → `agentOutcomeFn`; public surface normalized to camelCase (`start()` / `outcome()` / `llmMessages`), matching the coding-agent spec |

## 9. Open questions

- `LlmRef` resolution shape (string id vs structured ref) — decide when wiring
  `llm-client-registry` into the new `runtime` package.
- `TurnFacts` shape for `turnBoundary` hooks — fix when folding the trigger engine's
  turn-fact extraction into hook dispatch; it must cover today's notification-rule needs
  (`notification-triggers.e2e.ts` is the behavioral reference).
- Stream deltas (token-level events) in `AgentEvent` — out of scope for the split; revisit
  for UI needs.
- `AgentOutcome.outcome` / `BackgroundTaskOutcome.outcome` read as `o.outcome` at call
  sites — acceptable, or rename to `result`? (current spec keeps `outcome`).
