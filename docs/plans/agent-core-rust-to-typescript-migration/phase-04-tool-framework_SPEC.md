# EOS Agent Core Rust to TypeScript Migration - Phase 04 Tool Framework

Status: Proposed
Date: 2026-06-10
Owner: eos-agent-core
Migration direction: Rust -> TypeScript
Project path: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core`
Rust source boundary: `agent-core/crates/eos-tool` (contract, registry, hooks,
concrete tools), `agent-core/crates/eos-engine/src/tool_call`
(execute/batch/hooks), `agent-core/crates/eos-engine/src/background`,
`agent-core/crates/eos-engine/src/notifications.rs`
Depends on: Phase 03 (`@eos/engine`), Phase 02 (`@eos/contracts`,
`@eos/llm-client`)
Knowledge inputs: `knowledge/tool-definition-and-registry.md`,
`knowledge/tool-execution-pipeline.md`, `knowledge/tool-hooks.md`,
`knowledge/background-task-tracking.md`,
`knowledge/background-task-spawn-and-cancellation.md`,
`knowledge/message-steering.md`

## 1. Intent

Phase 04 introduces the tool framework as a new `@eos/tool` package and grows
the Phase 03 engine seams it needs:

- a flat Zod-first tool contract (`ToolHandler`) with exactly two metadata
  flags (`terminal`, `availableInIsolatedWorkspace`) and fail-closed
  `defineTool` defaults,
- a per-call execution pipeline (parse -> pre-hooks -> execute -> post-hooks
  -> stamping) plus a batch executor that together absorb the engine's
  Phase 03 tool seam: `tools.ts` and `tool-runner.ts` leave `@eos/engine`,
  which keeps one injected `ToolExecutor` port,
- a generic background supervisor (command / subagent / workflow sessions
  behind one `SessionDriver` interface, keyed by native ids),
- a system notification queue drained at the loop boundary, with an
  auto-wait rule when the model idles while sessions are live,
- a pluggable hook protocol (PreToolUse / PostToolUse / PostToolUseFailure)
  with a JS-script `command` adapter and an in-process `callback` adapter,
- the five tool families: sandbox, agent, submission, workflow, background.

Each tool family is constructed with exactly its own service (`SandboxPort`,
`AgentRunPort`, `WorkflowPort`); real implementations are Phase 04.5
(`@eos/agent-runtime`) and later sandbox-host work. This phase verifies
everything against fakes in `@eos/testkit` ("happy" sandbox).

This phase is additive plus a bounded engine restructure at the tool
boundary. The Rust engine remains the live implementation; nothing under
`agent-core/` changes.

## 2. Design Decisions

Deliberate choices, recorded so later phases do not mistake them for
omissions:

1. **No intent classification.** The Rust
   `ReadOnly | WriteAllowed | Lifecycle` taxonomy is not ported. Terminal
   solo-dispatch hangs off the `terminal` flag alone; lifecycle solo-dispatch
   is replaced by turn-boundary workspace-mode semantics (decision 4);
   read/write partitioning stays on the existing dormant
   `isConcurrencySafe` seam.
2. **Every tool is synchronous from the dispatcher's view.** `execute()`
   resolves promptly for all tools. `run_subagent` and `delegate_workflow`
   start the work, register a background session, and return the native id.
   `exec_command` races completion against a yield window and either returns
   output or promotes the already-running session to the supervisor.
   "Background" exists only as supervisor state; the engine dispatch model
   is unchanged.
3. **No minted session ids.** The supervisor keys sessions by the native ids
   the model already holds (`agent_run_id`, `workflow_run_id`,
   `command_id`) via a discriminated `SessionRef`. Cancellation input is
   `{ type, id }`, not a parallel session-id namespace.
4. **Workspace-mode flips apply at the next turn boundary.** A mode change
   recorded by `enter_isolated_workspace` / `exit_isolated_workspace` does
   not affect siblings in the same batch; the next turn's tool specs are
   filtered by `availableInIsolatedWorkspace` and a call-time pipeline guard
   denies stale calls. No batch policy is needed for mode tools.
5. **No built-in hooks.** Hooks are purely an operator extension surface.
   Framework invariants are plain code at their structural sites: the
   isolated-mode ban is a pipeline guard; "no live sessions before
   submission" lives inside the submission tool factory; "at most one open
   workflow" lives inside `delegate_workflow`. The Rust hook enums
   (`BlockInIsolatedMode`, `RequireNoBackgroundSessions`, destructive-shell
   guards) are not ported as hooks.
6. **Hooks cannot rewrite tool output, and there is no `ask` decision.**
   PreToolUse may allow/deny/update input/add context; PostToolUse and
   PostToolUseFailure may only add context. Output rewriting is rejected
   (it forces result-emission order to depend on hook capability —
   `knowledge/tool-hooks.md`); `ask` is rejected because the engine is
   headless — advisor approval is the ask-path and it is a tool, not a hook
   decision.
7. **Tool outcome stays small; call facts are pipeline-owned.** Tools return
   `{ content, isError?, metadata? }`. `is_terminal` and the timing stamps
   are facts about the execution, not claims by the tool: the pipeline
   stamps `is_terminal = handler.terminal && !isError` (a failed submission
   can never terminate a run) and clocks around `execute()` only, so slow
   hooks never masquerade as slow tools.
8. **`content` is `JsonValue` with one serialization point.** Submission
   payloads ride the terminal result's `content` into the run outcome — no
   `SubmissionSink` port, no duplicate carrier. Non-string content is
   `JSON.stringify`-ed exactly once, where the engine projects
   `tool_result` blocks; the structured value survives in events and the
   outcome.
9. **`ToolCallResult` is constructed, not inherited.** The batch executor
   owns the per-call record (it must pair `tool_use_id`); it stamps and
   normalizes around each pipeline result. There is no
   `ToolOutcome extends` hierarchy, and the record type lives in
   `@eos/contracts` because it crosses the engine/tool boundary.
10. **One shared per-call fact record.** `ToolCallMeta` is built once per
    call, frozen, and shared by pre-hooks, `execute`, and post-hooks. It
    contains only serializable facts (command hooks eat JSON over stdin);
    the live `signal` composes on top for `execute` only — services are
    closed over at construction (decision 15), so nothing ambient travels
    with the call.
11. **Completion reaches the model as a system notification drained at the
    loop boundary** — never a late synthetic `tool_result` (provider
    adjacency), never model polling, never blocking the tool call. When the
    model produces a no-tool-use turn while sessions are live, the engine
    awaits the next notification instead of finishing (auto-wait): waiting
    consumes no turns and needs no Sleep tool.
12. **Notifications are typed data rendered late; the rule machinery is not
    ported.** The queue is push-only (`session_settled`, `hook_context`).
    The Rust `NotificationRule` trait, budget tiers, and the terminal-call
    reminder rule are replaced by one engine-owned reminder branch
    (submission regime, §7) and named seams (§12).
13. **Per-kind toolsets are one table.** The five submission tools are one
    factory over a `Record<AgentKind, …>` table; `AGENT_TOOLSET` maps each
    agent kind to its tool names. Both are data, each with a single edit
    point.
14. **Notification senders and readers are symmetric per session kind.**
    Every kind has a spawn tool, a read tool (`read_command_transcript`,
    `read_agent_run_transcript`, `query_workflow`), and the shared cancel.
    Notifications carry only `{ ref, status, summary }`; the model pulls
    detail through the read tool. Full outputs never sit in conversation
    state.
15. **No ambient `ToolRuntime`.** There is no shared port record threaded
    through calls. Each tool family factory takes exactly its own
    service(s) — `sandboxTools(sandbox, supervisor)`,
    `agentTools(agents, supervisor)`,
    `workflowTools(workflows, supervisor)`, `backgroundTools(supervisor)`,
    `submissionTool(kind, supervisor)` — and handlers close over them. A
    sandbox tool cannot reach the workflow port by construction, and a new
    service later touches one factory signature, not a shared type.
16. **The engine owns no tool machinery.** `tools.ts` and `tool-runner.ts`
    are removed from `@eos/engine`; registry, concurrency cap, batch
    policy, and the pipeline all live behind one injected `ToolExecutor`.
    The engine keeps the invariant it cannot delegate: after
    `executeBatch` returns, it fills any unanswered `tool_use_id` with a
    synthetic error result, so provider-history validity (Phase 03 §7)
    never depends on executor correctness.

## 3. Scope

In scope:

- `@eos/tool` package: contract, `defineTool`, pipeline, batch executor,
  hook protocol and runner, background supervisor and drivers,
  notification queue, executor assembly, and the five tool families,
- engine restructure at the tool boundary: `tools.ts` and `tool-runner.ts`
  removed, one `ToolExecutor` port added, batch-result normalization,
  terminal exit, `NotificationSource` port, auto-wait and reminder
  branches; loop tests ported to a scripted executor,
- `@eos/contracts` additions: `AgentKind`, `AgentRunId`, `WorkflowRunId`,
  `CommandId`, `ToolCallResult`,
- `@eos/testkit` first real content: happy `SandboxPort`, fake
  `AgentRunPort` / `WorkflowPort`, transcript fixture helper,
- tests per §15.

Out of scope (named seams in §12):

- real `SandboxPort` over the sandbox host, real `AgentRunPort` /
  `WorkflowPort` (Phase 04.5 and later), the composition root, hook config
  file loading, the JSONL transcript writer (Phase 04.5; this phase's hook
  tests write fixture files),
- persistence (`@eos/db`), observability wiring,
- `isConcurrencySafe` partitioning, result-size persistence
  (`maxResultSizeChars`), compaction,
- any edit under `agent-core/`.

## 4. Rust Surface and TypeScript Target

| Rust source | TypeScript target | Carries |
| --- | --- | --- |
| `eos-tool/src/registry.rs` (`ToolExecutor`, `RegisteredTool`, `ToolRegistry`, `ToolRuntime`) | `packages/tool/src/contract.ts`, `define.ts`, `toolset.ts` | Redesigned: flat handler + two flags + per-family service injection |
| `eos-tool/src/model.rs` (`ToolResult`, `ExecutionMetadata`) | `packages/tool/src/contract.ts` | `ToolOutcome`, `ToolCallMeta` |
| `eos-engine/src/tool_call/execute.rs` (pipeline, `stamp_terminal`) | `packages/tool/src/pipeline.ts` | Per-call pipeline; terminal stamping |
| `eos-engine/src/tool_call/batch.rs` (terminal batch policy) + Phase 03 `tool-runner.ts` | `packages/tool/src/executor.ts` | Batch dispatch (cap 8, ordering, abort settling) + terminal-solo; lifecycle policy rejected (§2.4) |
| `eos-tool/src/hooks.rs` + `eos-engine/src/tool_call/hooks.rs` | `packages/tool/src/hooks/` | Redesigned: enum hooks -> external protocol (§2.5) |
| `eos-engine/src/background/session_runtime.rs` (managers, monitors, statuses) | `packages/tool/src/background/` | Generic supervisor + per-kind drivers |
| `eos-engine/src/notifications.rs` | `packages/tool/src/notifications/` + engine `NotificationSource` port | Push queue; rule trait not ported (§2.12) |
| `eos-tool/src/tools/{sandbox,command,subagent,workflow}.rs` + submission tools | `packages/tool/src/tools/` | The five families |

## 5. Tool Contract (`contract.ts`, `define.ts`, family modules)

```ts
// Authoring surface — the only types a tool author sees.
interface ToolHandler<I> {
  name: ToolName;                          // branded string
  description: string;
  input: z.ZodType<I>;                     // wire spec via z.toJSONSchema()
  terminal: boolean;                       // submissions only
  availableInIsolatedWorkspace: boolean;   // sandbox family only
  execute(input: I, ctx: ToolCallContext): Promise<ToolOutcome>;
}

interface ToolOutcome {
  content: JsonValue;        // model-facing; string or structured
  isError?: boolean;         // default false
  metadata?: JsonObject;     // transcript/observability only
}
```

`defineTool(def)` centralizes fail-closed defaults: `terminal: false`,
`availableInIsolatedWorkspace: false`. A forgotten override degrades to
"banned in isolated mode, non-terminal", never to "allowed everywhere". It
also derives the `ToolSpec` (`input_schema` from `z.toJSONSchema`).

The per-call ambient record:

```ts
// Serializable facts; built once per call, frozen, shared by all stages.
interface ToolCallMeta {
  tool_use_id: ToolUseId;
  tool_name: ToolName;
  agent: { run_id: AgentRunId; kind: AgentKind };
  workspace: { is_isolated: boolean };     // read once at call start
  transcript_path: string;
}

// What execute() receives: the frozen facts plus the one live handle.
// Services are NOT here — handlers close over their own service at
// construction (§2.15), so neither tools nor hooks ever see a port bag.
interface ToolCallContext {
  meta: ToolCallMeta;
  signal: AbortSignal;
}
```

Each service port is declared in its owning family module and injected at
factory construction by the composition root (Phase 04.5). DI sits at real
resource boundaries only:

```ts
interface SandboxPort {
  readFile(path: string, opts?: { offset?: number; limit?: number }): Promise<string>;
  writeFile(path: string, content: string): Promise<void>;
  editFile(path: string, oldString: string, newString: string,
           replaceAll: boolean): Promise<void>;
  startCommandSession(command: string, opts?: { timeout_ms?: number }):
    Promise<{ id: CommandId; completion: Promise<CommandExit> }>;
  writeStdin(id: CommandId, data: string, end: boolean): Promise<void>;
  readCommandTranscript(id: CommandId, offset?: number):
    Promise<{ content: string; new_offset: number; running: boolean }>;
  killCommand(id: CommandId, reason: string): Promise<void>;
  enterIsolatedWorkspace(): Promise<void>;
  exitIsolatedWorkspace(): Promise<{ summary: string }>;
}

interface AgentRunPort {
  spawnSubagent(req: { prompt: string; model?: string }):
    Promise<{ run_id: AgentRunId; settled: Promise<SubagentSettled> }>;
  askAdvisor(req: { question: string; context?: string },
             signal: AbortSignal): Promise<{ answer: string }>;
  readTranscript(runId: AgentRunId, offset?: number):
    Promise<{ content: string; new_offset: number; status: string }>;
}

interface WorkflowPort {
  delegate(req: { workflow: string; args?: JsonObject }):
    Promise<{ workflow_run_id: WorkflowRunId; settled: Promise<WorkflowSettled> }>;
  query(id: WorkflowRunId): Promise<JsonObject>;
}
```

`WorkspaceState` is a tiny mutable holder (`{ isIsolated: boolean }`) owned
by the composition root, closed over by the mode tools (which flip it after
the `SandboxPort` call succeeds), the per-turn spec provider, and the
pipeline's meta builder.

Naming rule (Phase 02 §4.1): authoring/in-process surfaces (`ToolHandler`,
`ToolOutcome`, the family factories) are camelCase; records that cross a
process or persistence boundary (`ToolCallMeta`, `ToolCallResult`,
`HookPayload`, notification payloads) are snake_case.

## 6. Execution Pipeline (`pipeline.ts`)

`bindTool(handler, deps)` closes over run-level dependencies (`hookEngine`,
`workspaceState`, run identity, `transcriptPath`) at executor build —
handlers already close over their own services (§2.15). The `@eos/tool`
batch executor (§7) keeps batch concerns (concurrency, ordering, abort,
`tool_use_id` pairing); the pipeline owns per-call semantics:

```
1. meta = Object.freeze({ … })          tool_use_id from the executor
2. isolated-mode guard                  meta.workspace.is_isolated &&
                                        !handler.availableInIsolatedWorkspace
                                        -> is_error result
3. handler.input.safeParse              fail -> is_error result (zod issue
                                        summary); never throws
4. PreToolUse hooks                     deny -> is_error result with reason;
                                        updatedInput -> re-safeParse via the
                                        SAME schema, replace (fail -> error)
5. t0; handler.execute(input, ctx); t1  throw -> catch, run
                                        PostToolUseFailure hooks, return
                                        is_error result with timing
6. PostToolUse hooks                    success path only; context-only
7. return enriched output               is_terminal = handler.terminal &&
                                        !is_error; tool_start_time = t0;
                                        tool_end_time = t1
```

Rules:

- Timing brackets step 5 only. Pre-execution rejections (steps 2-4) stamp
  both times with the rejection instant.
- A pre-hook's `updatedInput` goes back through the same Zod schema — a
  hook can rewrite input but cannot smuggle an invalid shape past
  validation.
- `additionalContext` from any hook stage is published to the notification
  queue as a `hook_context` notification (seen by the model at the next
  loop boundary), not folded into the tool result.
- Hook execution warnings (non-blocking failures, §8) accumulate under the
  result's `metadata.hook_warnings`.
- The pipeline never throws; every path returns a result the runner can
  record.

## 7. Engine Changes (the tool boundary becomes one port)

Phase 03's `tools.ts` (`ToolDefinition`, `ToolRegistry`, `ToolContext`,
`ToolOutput`) and `tool-runner.ts` are REMOVED from `@eos/engine` (§2.16).
The engine retains exactly one piece of tool knowledge, an injected port:

```ts
// packages/engine/src/tool-executor.ts (new)
interface ToolExecutor {
  /** Evaluated per turn; @eos/tool filters by workspace mode here (§2.4). */
  specs(): ToolSpec[];
  /** Empty -> Phase 03 termination; non-empty -> submission regime, and
      the names render into the reminder message. */
  terminalToolNames(): readonly string[];
  executeBatch(calls: ToolUseBlock[], signal: AbortSignal,
               emit: (event: AgentEvent) => void): Promise<ToolCallResult[]>;
}
```

`ToolCallResult` moves to `@eos/contracts` — it crosses the engine/tool
boundary and references only contracts types:

```ts
interface ToolCallResult {
  tool_use_id: ToolUseId;
  content: JsonValue;
  is_error: boolean;             // normalized, no optional
  is_terminal: boolean;
  tool_start_time: number;       // epoch ms
  tool_end_time: number;
  metadata?: JsonObject;
}
```

The Phase 03 runner behaviors relocate to `@eos/tool`'s executor
(`packages/tool/src/executor.ts`) with their tests, semantics unchanged:
concurrency cap 8, `tool_use`-order assembly, error/unknown-tool mapping to
`is_error` results, abort settling with straggler-emit suppression, and
`tool_execution_started`/`completed` emission (the completed event grows
`is_terminal`, `tool_start_time`, `tool_end_time`, `metadata`; `output`
stays the string projection). The executor adds the terminal-solo policy
(the Phase 03 "batch policies" seam): a batch containing a terminal call
plus any sibling rejects ALL calls with `is_error` results — parity with
Rust `reject_terminal_batch`; a solo terminal call dispatches normally.

`agent-loop.ts` — the loop spine grows three branches and one invariant:

```
3.  drain steers; then drain notifications        (steers first: user input
    conversation.appendUser(...) each              outranks system notices)
6.  calls.length === 0:
      pending steers                 -> continue   (Phase 03)
      notifications?.hasLiveSessions() -> await
        notifications.waitForNext(signal); continue        (auto-wait)
      terminalToolNames() non-empty  -> appendUser(terminal reminder);
                                        continue   (submission regime)
      otherwise                      -> finish(completed)  (Phase 03)
7.  results = tools.executeBatch(calls, signal, emit)
    NORMALIZE: every tool_use_id absent from results gets a synthetic
    is_error "interrupted" result — provider-history validity stays an
    engine-owned invariant (Phase 03 §7) regardless of executor behavior
7.5 any result.is_terminal          -> finish({ status: 'completed',
                                        final_message, stop_reason,
                                        submission: result.content })
8.  project ToolResultBlocks (non-string content stringified exactly
    here, §2.8); conversation.appendToolResults(...)
```

- The submission regime is derived from `terminalToolNames()`, never
  configured. The reminder is one engine-rendered user message naming the
  run's terminal tool(s); it re-fires on every bare-text turn and
  `maxTurns` is the backstop against spin.
- Auto-wait consumes no turn (no provider call). `waitForNext` is
  level-triggered: it resolves immediately if notifications are already
  pending, on the next `publish`, or on abort (the loop-top check then
  classifies `cancelled`).
- `AgentRunStatus`'s `completed` arm gains `submission?: JsonValue`.

The `NotificationSource` port (new `packages/engine/src/notifications.ts`;
implemented by `@eos/tool`, faked in engine tests):

```ts
interface NotificationSource {
  drain(): Message[];                       // pending notices as user
                                            // messages; marks delivered
  waitForNext(signal: AbortSignal): Promise<void>;
  hasLiveSessions(): boolean;
}
```

`StartAgentRunInput`: `tools: ToolRegistry` is replaced by
`tools: ToolExecutor`, and `notifications?: NotificationSource` is added.
`TurnConfig.toolSpecs` becomes a thunk over `tools.specs()`. Engine loop
tests run against a scripted `ToolExecutor`; with empty
`terminalToolNames()` and no notification source, Phase 03 termination and
transcript semantics are preserved unchanged.

## 8. Hook System (`hooks/protocol.ts`, `hooks/runner.ts`)

Events: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`.

```ts
interface HookConfigEntry {
  event: HookEvent;
  matcher?: string;                // exact tool name; absent = all tools
  hooks: HookCommand[];
}

type HookCommand =
  | { type: "command"; command: string; timeout_ms?: number }   // JS script et al.
  | { type: "callback"; run(payload: HookPayload, signal: AbortSignal):
        Promise<HookOutput> };                                   // tests/SDK

interface HookPayload {            // snake_case: crosses the process boundary
  event: HookEvent;
  tool_name: string;
  tool_input: JsonObject;
  // …ToolCallMeta fields: tool_use_id, agent, workspace, transcript_path
  tool_response?: string;          // PostToolUse only (string projection)
  error?: string;                  // PostToolUseFailure only
}

interface HookOutput {
  decision?: "allow" | "deny";
  reason?: string;                 // deny: model-visible feedback
  updatedInput?: JsonObject;       // PreToolUse only
  additionalContext?: string;      // -> hook_context notification
}
```

Command adapter mechanics (the JS-script pluggability):

- spawn with `shell: true`; `HookPayload` JSON + `"\n"` written to stdin;
  per-hook timeout (default 60 s) as an AbortSignal derived from the call's
  signal — a cancelled run kills its hooks.
- exit 0: stdout parsed as `HookOutput` (Zod `safeParse`; mismatch = a
  non-blocking warning, treated as passthrough);
- exit 2: deny, stderr is the model-visible reason;
- other exit: passthrough plus a warning in `metadata.hook_warnings`.
  Three distinct channels — structured decision, model feedback, operator
  warning — never collapsed.

Semantics:

- All hooks matching one event run in `Promise.all`; precedence across
  their outputs is `deny > allow > passthrough`, centralized in one kernel
  function. All hooks still run after a deny.
- `updatedInput` is applied only when exactly one hook supplies it; two or
  more conflicting updates deny the call with a conflict reason
  (deterministic, no merge guessing).
- Matching is name-level only this phase; rule-content matchers
  (`Bash(git *)`-style) are a §12 seam.
- Capabilities per event: PreToolUse = decision + updatedInput +
  additionalContext; PostToolUse / PostToolUseFailure = additionalContext
  only (§2.6).
- State inference happens through `transcript_path` (an append-only JSONL
  written by Phase 04.5; tests use fixture files) — hooks never receive
  live objects or ports.

`HookEngine` is constructed from `HookConfigEntry[]`; loading entries from
`.eos-agents/hooks.json` is Phase 04.5.

## 9. Background Supervisor (`background/`)

```ts
type SessionRef =
  | { type: "subagent"; id: AgentRunId }
  | { type: "workflow"; id: WorkflowRunId }
  | { type: "command";  id: CommandId };

type SessionStatus = "running" | "completed" | "failed" | "cancelled"
                   | "delivered";

interface BackgroundSession {
  ref: SessionRef;
  status: SessionStatus;
  spawned_by: ToolUseId;
  started_at: number;
  ended_at?: number;
  summary?: string;                // set at terminal transition; one line
}

interface SessionDriver {
  type: SessionRef["type"];
  /** Begin watching; call settle exactly once. Push, never polled. */
  watch(session: BackgroundSession, handle: unknown,
        settle: (outcome: SessionOutcome) => void): void;
  cancel(session: BackgroundSession, handle: unknown,
         reason: string): Promise<void>;
  describe(session: BackgroundSession): string;
}
```

`BackgroundSupervisor` is a typed map (keyed `"${type}:${id}"`) plus a
status machine plus delivery bookkeeping. Public surface:
`register(ref, spawnedBy, handle)`, `cancel(ref, reason)`, `list()`,
`liveCount()`, `markDelivered(refs)`, `dispose(reason)`.

Lifecycle rules:

- `running -> completed | failed | cancelled -> delivered -> evicted`.
  Terminal-status and delivered are separate facts (the model must never
  miss a completion); eviction (removal from the map) requires both.
- Each driver settles exactly once (the Claude Code dual-delivery lesson);
  `settle` on a non-running session is dropped silently — this is the
  cancel race: `cancel()` transitions to `cancelled` immediately, publishes,
  then calls `driver.cancel` for teardown, and the driver's late natural
  settle is ignored.
- Every terminal transition publishes one `session_settled` notification.
- `liveCount()` counts `running` only; it backs the submission guard and
  the engine's `hasLiveSessions`.
- `dispose(reason)` cancels all running sessions; the composition root
  calls it when the run finishes (on the success path the submission guard
  already guarantees zero live sessions).

Per-kind drivers absorb the asymmetry; the supervisor stays generic:

| Driver | Watch | Cancel |
| --- | --- | --- |
| subagent | awaits the child run's `settled` promise from `AgentRunPort.spawnSubagent` | abort via the port (child `interrupt`) |
| workflow | awaits `settled` from `WorkflowPort.delegate` | workflow API cancel |
| command | inherits the **already-in-flight** `completion` promise from `exec_command` — no second wait is opened | `SandboxPort.killCommand` |

`exec_command` promotion (the hybrid):

```
sandbox.startCommandSession(cmd) -> { id, completion }
race(completion, sleep(clamp(yield_time_ms, 1, 30_000)))   // default 1_000
  completion won -> return transcript output synchronously
  timer won      -> supervisor.register({type:'command', id}, toolUseId,
                                        { completion })
                    return { command_id, status: 'running',
                             transcript: partial output }
```

`command_stdin` and `read_command_transcript` address sessions by
`command_id` through `SandboxPort` directly, independent of the supervisor —
they work identically for still-yielding and backgrounded sessions.

## 10. System Notifications (`notifications/`)

```ts
type SystemNotification =
  | { type: "session_settled"; ref: SessionRef;
      status: "completed" | "failed" | "cancelled"; summary: string }
  | { type: "hook_context"; tool_use_id: ToolUseId; text: string };
```

`SystemNotificationQueue`:

- `publish(n, key?)`: FIFO; a pending entry with the same `key` is replaced
  (`session_settled` keys on `"${type}:${id}"`; `hook_context` entries are
  unkeyed).
- `drain()`: renders ALL pending notifications into one user message —
  each as a `<system_notification>{json}</system_notification>` block —
  removes them, and emits the drained `SessionRef`s on an `onDrained`
  callback in the same synchronous block (delivery is transactional with
  the drain; a crash between drain and append cannot strand a session).
- `waitForNext(signal)`: level-triggered (§7).

`createNotificationSource(queue, supervisor)` adapts the pair to the engine
port: `drain` delegates to the queue, `hasLiveSessions` to
`supervisor.liveCount()`, and the composition root wires
`queue.onDrained(refs => supervisor.markDelivered(refs))` — queue and
supervisor never import each other.

Rendered notifications enter both transcript lists as ordinary user
messages (the wrapper text is the discriminator); a displayed-side `isMeta`
projection is a §12 seam.

## 11. Tool Families, Schemas, Toolsets (`tools/`, `toolset.ts`)

Contracts additions: `AgentKind = "main" | "planner" | "worker" | "advisor"
| "subagent"` (Zod enum) and branded ids `AgentRunId` (mint + adopt),
`WorkflowRunId` (adopt-only), `CommandId` (adopt-only; the sandbox assigns).

| Tool | Input schema (Zod sketch) | Flags / notes |
| --- | --- | --- |
| `read` | `{ path, offset?, limit? }` | isolated-ok |
| `multi_read` | `{ paths: string[] (1..32) }` | isolated-ok |
| `write` | `{ path, content }` | isolated-ok |
| `edit` | `{ path, old_string, new_string, replace_all? }` | isolated-ok |
| `exec_command` | `{ command, yield_time_ms? (1..30_000, default 1_000), timeout_ms? }` | isolated-ok; §9 promotion |
| `command_stdin` | `{ command_id, data, end? }` | isolated-ok |
| `read_command_transcript` | `{ command_id, offset? }` | isolated-ok |
| `enter_isolated_workspace` | `{}` | isolated-ok=false (no nesting) |
| `exit_isolated_workspace` | `{}` | isolated-ok; flips mode back |
| `run_subagent` | `{ prompt, model? }` | returns `{ agent_run_id }`; registers session |
| `ask_advisor` | `{ question, context? }` | synchronous; awaits the advisor run |
| `read_agent_run_transcript` | `{ agent_run_id, offset? }` | |
| `submit_<kind>_outcome` ×5 | `{ summary: string, payload?: JsonObject }` | `terminal: true`; guard: `liveCount() > 0` -> error |
| `delegate_workflow` | `{ workflow, args? }` | returns `{ workflow_run_id }`; guard: one open workflow |
| `query_workflow` | `{ workflow_run_id }` | |
| `list_background_sessions` | `{}` | rows `{ type, id, status, started_at, summary? }` (running + undelivered-terminal) |
| `cancel_background_session` | `{ type: "subagent"\|"workflow"\|"command", id, reason? }` | unknown ref -> error result; already-terminal -> noted, no-op |

Submission factory: one `makeSubmissionTool(kind)` over a
`Record<AgentKind, { name, description }>` table; all five share the
outcome schema this phase (per-kind payload schemas are a §12 seam). The
terminal result's `content` is the parsed `{ summary, payload? }` object —
that object is what arrives at `outcome.submission` (§7).

`AGENT_TOOLSET` — the single edit point for kind/tool product decisions
(defaults below are deliberate but tunable):

| Kind | Toolset |
| --- | --- |
| main | all sandbox + all agent + workflow + background + `submit_main_outcome` |
| worker | all sandbox + background + `submit_worker_outcome` |
| subagent | all sandbox + background + `submit_subagent_outcome` |
| planner | `read`, `multi_read` + `submit_planner_outcome` |
| advisor | `read`, `multi_read` + `submit_advisor_outcome` |

Family factories — each takes exactly its own service(s) (§2.15):

```ts
sandboxTools(sandbox: SandboxPort, supervisor: BackgroundSupervisor)
agentTools(agents: AgentRunPort, supervisor: BackgroundSupervisor)
workflowTools(workflows: WorkflowPort, supervisor: BackgroundSupervisor)
backgroundTools(supervisor: BackgroundSupervisor)
submissionTool(kind: AgentKind, supervisor: BackgroundSupervisor)
```

`buildToolExecutor({ kind, sandbox, agents, workflows?, supervisor,
hookEngine, workspace, identity, transcript_path })` consults
`AGENT_TOOLSET`, invokes only the factories the kind's row names — each
with exactly its own service — binds every handler through the §6
pipeline, and returns the engine `ToolExecutor`: a deterministic sorted
registry (prompt-cache stability), per-turn `specs()` filtering on
`WorkspaceState.isIsolated` × `availableInIsolatedWorkspace`,
`terminalToolNames()`, and batch dispatch. The services appear together
only as named arguments at this one assembly site; no ambient record
reaches a tool call. The workflow family is included only when a
`WorkflowPort` is supplied.

## 12. Deferred and Rejected

Deferred (named seams):

| Deferred behavior | Seam left by this phase |
| --- | --- |
| Real `SandboxPort` over the sandbox host | `SandboxPort` interface; happy fake is the only impl |
| Real `AgentRunPort` / `WorkflowPort`, composition root, hook config loading, JSONL transcript writer | Phase 04.5 (`@eos/agent-runtime`) |
| Per-kind submission payload schemas | the `SUBMISSIONS` table holds a schema slot per kind |
| Rule-content hook matchers (`Bash(git *)`) | `matcher` is a string; the match function is one site |
| Async hooks, `prompt`/`agent`/`http` hook kinds | `HookCommand` is a discriminated union; new arms are additive |
| Notification rules (budget tiers etc.) | `publish()` is the entry; a rule evaluator would be one more publisher |
| Auto-wait watchdog (sessions live but never settle) | the `waitForNext` site; classify as `failed` after a ceiling |
| Displayed-side `isMeta` projection for notifications | the `<system_notification>` wrapper is the discriminator |
| Large-result persistence (`maxResultSizeChars`) | `ToolCallResult.content` is the interception point |
| `isConcurrencySafe` partitioning | unchanged Phase 03 seam |

Rejected, not deferred (decisions; no seam kept):

- Intent classification and lifecycle batch policy (§2.1, §2.4).
- Built-in hooks (§2.5).
- Hook output rewriting and the `ask` decision (§2.6).
- A `SubmissionSink` port (§2.8): the return path carries the payload.
- An ambient `ToolRuntime` port record threaded through calls (§2.15):
  per-family construction injection instead.
- Minted session ids (§2.3): native refs only.
- A Sleep/wait tool: auto-wait is an engine rule (§2.11).

## 13. Workspace Changes

- `packages/tool/`: new package `@eos/tool` (`dependencies`:
  `@eos/contracts`, `@eos/engine` via `workspace:*`, `zod`).
- `packages/engine/`: §7 restructure — `tools.ts` and `tool-runner.ts`
  deleted, `tool-executor.ts` and `notifications.ts` added; runner tests
  move to `@eos/tool` with the relocated logic; loop tests port to a
  scripted `ToolExecutor`.
- `packages/contracts/`: `AgentKind`, three branded ids, `ToolCallResult`
  (additive).
- `packages/testkit/`: first real content — `@eos/testkit` (`dependencies`:
  `@eos/contracts`, `@eos/tool`): happy `SandboxPort` (in-memory files +
  scripted command sessions with controllable completion), fake
  `AgentRunPort` / `WorkflowPort` with resolvable `settled` promises,
  transcript fixture writer for hook tests.
- No new third-party dependencies.

## 14. Migration Steps

1. Contracts additions (`AgentKind`, ids) -> verify: contracts tests green.
2. Engine §7 restructure (`ToolExecutor` port, batch normalization,
   terminal exit, `NotificationSource` + auto-wait + reminder; loop tests
   ported to a scripted executor and fake source) -> verify: ported
   Phase 03 loop suite green plus new loop cases (§15 cases 2-5, 21).
3. `@eos/tool` contract + `defineTool` + pipeline + batch executor
   (relocated runner logic and tests; hooks stubbed pass-through) ->
   verify: pipeline order, guard, parse, stamping tests plus the relocated
   runner suite (cap 8, ordering, abort settling, terminal-solo — §15
   cases 1, 10).
4. Hook protocol + runner (callback first, then command adapter with real
   spawned scripts) -> verify: §15 cases 10-13.
5. Supervisor + drivers + notification queue + `createNotificationSource`
   -> verify: §15 cases 6-9.
6. Tool families over testkit fakes + `buildToolExecutor` assembly ->
   verify: §15 cases 14-19.
7. Workspace wiring -> verify: `pnpm run check` green from
   `eos-agent-core/`.
8. Update the migration `index.md` row for this phase.

## 15. Verification

All suites in-process; no network, no real sandbox.

| # | Case | Asserts |
| --- | --- | --- |
| 1 | Terminal-solo policy | terminal + sibling batch: every call `is_error`, nothing dispatched; solo terminal dispatches |
| 2 | Terminal exit | terminal result finishes `completed` with `submission` = structured content; `final_message` is the submitting assistant message |
| 3 | Auto-wait | bare-text turn + live sessions: loop awaits, a published notification resumes it, drained at step 3; no turn consumed while waiting |
| 4 | Submission-regime reminder | bare text, no sessions, terminal tool registered: reminder user message appended, loop continues; `maxTurns` still backstops |
| 5 | Steers outrank notifications | both pending at step 3: steers drain first |
| 6 | Supervisor lifecycle | running -> settled publishes once; `markDelivered` then evicts; double-settle dropped; `liveCount` correct throughout |
| 7 | Cancel race | `cancel()` publishes `cancelled`; the driver's late natural settle is ignored |
| 8 | `exec_command` promotion | fast command returns output synchronously; slow command returns `{ command_id }` and registers; the SAME completion promise settles the session (fake counts waits) |
| 9 | Cancel by `(type, id)` | cancels the right session; unknown ref -> error result; already-terminal -> no-op note |
| 10 | Pipeline order | guard -> parse -> pre-hooks -> execute -> post-hooks; timing brackets execute only; pre-execution rejection stamps rejection instant |
| 11 | Hook deny / exit-2 | command hook exit 2: call never executes; stderr is the model-visible reason |
| 12 | Hook updatedInput | single update re-validated and applied; invalid update -> error; two conflicting updates -> deny |
| 13 | Hook context + warnings | `additionalContext` arrives as `hook_context` notification next boundary; nonzero/garbage stdout -> passthrough + `metadata.hook_warnings` |
| 14 | Isolated-mode ban | mode flip filters next turn's specs; stale call denied by the call-time guard; sandbox tools unaffected |
| 15 | Mode turn-boundary | `enter_isolated_workspace` batch siblings execute under the old mode |
| 16 | Submission guard | submit with live sessions -> error naming them; after cancel/settle+delivery -> succeeds |
| 17 | Subagent round-trip | `run_subagent` returns `{ agent_run_id }`; fake settle -> notification -> `read_agent_run_transcript` reads via port |
| 18 | Workflow pair | `delegate_workflow` registers + returns id; second open delegate denied; `query_workflow` passes through |
| 19 | Executor assembly | each kind gets exactly its table row + one submission tool; deterministic order; each factory receives only its own service; workflow family absent without a port |
| 20 | Serialization point | structured content stringified once in the projected `tool_result` block; intact in `ToolCallResult`, events, and `outcome.submission` |
| 21 | Engine normalization | an executor that drops a result: the missing `tool_use_id` gets a synthetic `is_error` result; `outcome.llm` stays provider-valid |

Commands:

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core
pnpm install
pnpm run check
```

- Rust boundary hygiene: `git diff --stat -- agent-core` stays empty.
- Docs hygiene: `git diff --check -- docs/plans/agent-core-rust-to-typescript-migration eos-agent-core`.

## 16. Coexistence and Rollback

- Coexistence: the Rust engine remains the live implementation. `@eos/tool`
  has no runtime consumer until Phase 04.5; it is exercised only by its
  tests and the engine's new loop cases.
- Rollback: delete `packages/tool/`, revert the bounded engine/contracts/
  testkit edits, drop the index row. Phase 03 behavior is fully preserved
  when no `notifications` source and no terminal tools are supplied.

## 17. Acceptance Criteria

Phase 04 is accepted when:

- `@eos/tool` exposes the §5 contract with `defineTool` fail-closed
  defaults, and tool authors never see `is_terminal` or timing fields,
- the §6 pipeline enforces guard/parse/hook/stamp order and never throws,
- the engine implements §7 exactly: tool machinery removed behind one
  injected `ToolExecutor` port, batch-result normalization, terminal exit
  with `submission` on the outcome, notification drain below steers,
  auto-wait, and the derived submission-regime reminder — with Phase 03
  loop semantics preserved under a scripted executor that has no terminal
  tools and no notification source,
- hooks run only from operator config (no built-ins), with the §8 exit-code
  protocol, precedence kernel, and single-update rule,
- the supervisor is generic over §9 drivers, keyed by native refs, with
  push-only single settles, delivery-then-evict, and a working
  `exec_command` promotion that reuses the in-flight completion promise,
- all five tool families build per the §11 tables against testkit fakes,
  each factory injected with exactly its own service and no ambient port
  record anywhere in a call path,
- the §15 suite passes under `pnpm run check` with no network I/O,
- the Rust `agent-core/` tree is byte-for-byte unchanged,
- and the migration `index.md` lists Phase 04 with status and verification.

## 18. Progress Tracker

| Step | Status | Required proof |
| --- | --- | --- |
| Contracts additions | Pending | contracts tests green with `AgentKind` + 3 ids |
| Engine restructure | Pending | ported Phase 03 loop suite green + §15 cases 2-5, 21 |
| Contract + pipeline + executor | Pending | §15 cases 1, 10 plus relocated runner suite and defineTool default tests |
| Hook protocol + runner | Pending | §15 cases 11-13 incl. real spawned scripts |
| Supervisor + notifications | Pending | §15 cases 6-9 + 3 |
| Tool families + toolsets | Pending | §15 cases 14-20 |
| Workspace wiring | Pending | `pnpm run check` green; `git diff --stat -- agent-core` empty |
| Index updated | Pending | Phase 04 row in `index.md` |
