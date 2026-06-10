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

- a flat Zod-first tool contract (`ToolDefinition`) with exactly two metadata
  flags (`terminal`, `availableInIsolatedWorkspace`) and fail-closed
  `defineTool` defaults,
- a per-call execution pipeline (parse -> pre-hooks -> execute -> post-hooks
  -> stamping) plus a batch executor that together absorb the engine's
  Phase 03 tool seam: `tools.ts` and `tool-runner.ts` leave `@eos/engine`,
  which keeps one injected `ToolExecutor` port,
- an engine-owned `BackgroundSupervisor` (session lifecycle is loop
  lifecycle: registration by native id, capability-handle watching,
  teardown on every finish), with tools as its registering clients,
- an engine-owned, fully generic `NotificationInbox` drained at the loop
  boundary (the system-side twin of the steer queue) — publishers today
  are the supervisor and hook context, later trigger rules and
  agent-to-agent messages — with an auto-wait rule when the model idles
  while sessions are live,
- a pluggable hook protocol (PreToolUse / PostToolUse / PostToolUseFailure)
  with a JS-script `command` adapter and an in-process `callback` adapter,
- four tool families: sandbox, submission, workflow, background. The
  agent family (`run_subagent`, `ask_advisor`,
  `read_agent_run_transcript`) ships with its runtime in Phase 04.5
  (decision 21).

Each tool family is constructed with exactly its own service (`SandboxPort`,
`WorkflowPort`); real implementations are Phase 04.5 (`@eos/agent-runtime`)
and later sandbox-host work. This phase verifies everything against fakes
in `@eos/testkit` ("happy" sandbox).

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
   `exec_command` waits up to its yield window for exit and either returns
   output or promotes the still-running session to the supervisor.
   "Background" exists only as supervisor state; the engine dispatch model
   is unchanged.
3. **No minted session ids.** The supervisor keys sessions by the native ids
   the model already holds (`agent_run_id`, `workflow_run_id`,
   `command_id`) via a discriminated `SessionRef`. Cancellation input is
   `{ type, id }`, not a parallel session-id namespace.
4. **Workspace-mode flips apply at the next turn boundary.** A mode change
   recorded by `enter_isolated_workspace` / `exit_isolated_workspace` does
   not affect siblings in the same batch: the executor snapshots
   `workspace.isIsolated` once per batch, and every sibling's meta is built
   from that snapshot — a per-call read would leak a mid-batch flip into
   siblings still queued behind the concurrency cap. The next turn's tool
   specs are filtered by `availableInIsolatedWorkspace` and a call-time
   pipeline guard denies stale calls. No batch policy is needed for mode
   tools.
5. **No built-in hooks.** Hooks are purely an operator extension surface.
   Framework invariants are plain code at their structural sites: the
   isolated-mode ban is a pipeline guard; "no open sessions before
   submission" (running or undelivered, §9) lives inside the submission
   tool factory; "at most one open
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
   stamps `is_terminal = definition.terminal && !isError` (a failed submission
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
    call, frozen, and shared by pre-hooks, `execute`, and post-hooks. Its
    run facts are one nested `AgentRunSnapshot` — the whole run record
    snapshotted, never cherry-picked fields. It contains only
    serializable facts (command hooks eat JSON over stdin);
    the live `signal` composes on top for `execute` only — services are
    closed over at construction (decision 15), so nothing ambient travels
    with the call.
11. **Completion reaches the model as a system notification drained at the
    loop boundary** — never a late synthetic `tool_result` (provider
    adjacency), never model polling, never blocking the tool call. When the
    model produces a no-tool-use turn while sessions are live, the engine
    awaits the next notification OR steer instead of finishing (auto-wait):
    waiting consumes no turns, needs no Sleep tool, and never blocks user
    input — an arriving steer wakes the parked loop exactly like a
    settlement.
12. **The inbox and the supervisor are engine-owned; both are generic.**
    Rust parity: `notifications.rs` AND `background/session_runtime.rs`
    are both `eos-engine` modules. The `NotificationInbox` is a plain
    mailbox of already-rendered `Message`s with opaque `key`/`tag` fields
    — any holder of the reference can publish (supervisor settlements and
    hook context today; trigger rules and agent-to-agent messages later,
    with no inbox change). The `BackgroundSupervisor` is generic over
    `{ type: string, id: string }` refs and publishes its own
    `session_settled` notifications; the narrow
    `"subagent" | "workflow" | "command"` union is a tool-side
    refinement. There is no tool-side notifications module: the
    `<system_notification>` renderer is one engine helper
    (`systemNotificationMessage`). The Rust `NotificationRule` trait is
    not ported and none of its rules survive as engine branches: the
    budget tiers (75/100/125%) and the terminal-call reminder return
    later as agent-package notification rules publishing through the
    same `inbox.publish()` (§12, decision 20).
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
    service(s) — `sandboxTools(sandbox, supervisor, workspace)`,
    `agentTools(agents, supervisor)`,
    `workflowTools(workflows, supervisor)`, `backgroundTools(supervisor)`,
    `submissionTool(kind, supervisor)` — and the definitions close over
    them. A sandbox tool cannot reach the workflow port by construction,
    and a new service later touches one factory signature, not a shared
    type.
16. **The engine owns no tool machinery.** `tools.ts` and `tool-runner.ts`
    are removed from `@eos/engine`; registry, concurrency cap, batch
    policy, and the pipeline all live behind one injected `ToolExecutor`.
    The engine keeps the invariant it cannot delegate: after
    `executeBatch` returns, it fills any unanswered `tool_use_id` with a
    synthetic error result, so provider-history validity (Phase 03 §7)
    never depends on executor correctness.
17. **Background sessions are loop lifecycle, owned by the engine.** The
    auto-wait gate reads `background.liveCount()` directly, and the
    loop's exit path triggers `background.dispose(reason)` on every
    finish — interruption tears all running sessions down with no
    composition wiring. Disposal is fire-and-forget: `run_finished` does
    not wait for teardown to settle (an awaited-teardown barrier is a
    §12 seam). `dispose` also latches the supervisor: a `register` that
    arrives afterwards (an abandoned `execute()` continuation finishing
    after an abort — promises cannot be cancelled) is immediately
    cancelled via its own handle instead of leaking an unsupervised
    session.
18. **No per-kind drivers.** The Rust per-kind managers/monitors are not
    ported. A spawn site registers a `SessionHandle` capability record —
    `{ settled, cancel, describe? }` — that closes over exactly the right
    port (`exec_command` closes over `killCommand`, `run_subagent` over
    the child run). The supervisor never resolves kind -> behavior; new
    session kinds need no engine change.
19. **One `AgentRunState` record for per-run metadata; one mutable cell.**
    The frozen per-run facts (`run_id`, `kind`, `parent?`, `sandbox_id`,
    `transcript_path`) and the single tool-writable cell
    (`workspace.isIsolated`) live in one `AgentRunState` record built by
    the composition root. Guardrails that keep it from becoming an
    AppState-style bag: it holds data only (a port or service inside it
    would be §2.15's rejected `ToolRuntime` again); everything except
    `workspace.isIsolated` is readonly; tools never receive it at call
    time — `ToolCallMeta` nests its frozen snapshot (`AgentRunSnapshot`),
    a spread + freeze of the whole record; and any future mutable field
    needs a named writer, the same bar `workspace` met.
20. **Text output never terminates a run, and the engine ships no
    reminder.** A no-tool-use turn continues the loop unconditionally:
    run completion is exclusively a terminal tool result; `maxTurns`,
    interruption, and provider failure are the only other exits. The
    Phase 03 bare-text `finish(completed)` is removed, not preserved
    behind a flag, and the engine appends nothing on a bare-text turn —
    the model-facing nudge ("keep working, submit the terminal tool") is
    a future agent-package notification rule firing on text return
    through the §12 seam, not an engine branch.
21. **The agent family ships with its runtime.** `AgentRunPort` and the
    agent tools (`run_subagent`, `ask_advisor`,
    `read_agent_run_transcript`) are Phase 04.5, defined next to the
    `@eos/agent-runtime` code that implements them — building them here
    would mean designing the port against nothing but its own fake. This
    phase loses no coverage: the spawn -> register -> settle -> notify
    -> read pattern is fully exercised by `exec_command` and
    `delegate_workflow`, and the supervisor is generic (§2.18), so the
    agent family adds tools later without touching the engine or the
    executor.

## 3. Scope

In scope:

- `@eos/tool` package: contract, `defineTool`, pipeline, batch executor,
  hook protocol and runner, executor assembly, and the four tool families
  (one folder per family, one file per tool),
- engine restructure: `tools.ts` and `tool-runner.ts` removed, one
  `ToolExecutor` port added, batch-result normalization, terminal-only
  exit (decision 20), the engine-owned `NotificationInbox` and
  `BackgroundSupervisor` (generic mailbox + generic session lifecycle
  with dispose-on-finish), and the auto-wait branch; loop tests ported
  to a scripted executor,
- `@eos/contracts` additions: `AgentKind`, `AgentRunId`, `WorkflowRunId`,
  `CommandId`, `SandboxId`, `ToolCallResult`,
- `@eos/testkit` first real content: happy `SandboxPort`, fake
  `WorkflowPort`, transcript fixture helper,
- tests per §15.

Out of scope (named seams in §12):

- the agent tool family and `AgentRunPort` (Phase 04.5, decision 21),
- real `SandboxPort` over the sandbox host, real `WorkflowPort`
  (Phase 04.5 and later), the composition root, hook config
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
| `eos-engine/src/background/session_runtime.rs` (managers, monitors, statuses) | `packages/engine/src/background/` | Engine-owned generic supervisor; spawn-site capability handles replace per-kind managers/monitors (§2.18) |
| `eos-engine/src/notifications.rs` | `packages/engine/src/notification-inbox.ts` | Engine-owned generic inbox + `<system_notification>` renderer; rule trait not ported (§2.12) |
| `eos-tool/src/tools/{sandbox,command,workflow}.rs` + submission tools | `packages/tool/src/tools/` | Four families this phase; `subagent.rs` follows in Phase 04.5 (decision 21) |

## 5. Tool Contract (`contract.ts`, `define.ts`, family modules)

```ts
// Authoring surface — the only types a tool author sees. (The name is
// freed by §7's removal of the engine's Phase 03 seam type; the two
// shapes never coexist.)
interface ToolDefinition<I> {
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
// Serializable facts; built once per call, frozen, shared by all stages:
// the call identity plus the WHOLE run record as a snapshot — no
// cherry-picked fields.
interface ToolCallMeta {
  tool_use_id: ToolUseId;
  tool_name: ToolName;
  run: AgentRunSnapshot;
}

// Frozen, fully serializable copy of AgentRunState (§2.19): same fields,
// with the one mutable cell collapsed to the batch's snapshot (§2.4).
// Construction is a spread + freeze, so a new run-state fact reaches
// every tool call and hook payload without touching this projection.
interface AgentRunSnapshot {
  run_id: AgentRunId;
  kind: AgentKind;
  parent?: AgentRunId;
  sandbox_id: SandboxId;
  transcript_path: string;
  workspace: { is_isolated: boolean };     // batch-scoped snapshot (§2.4)
}

// What execute() receives: the frozen facts plus the one live handle.
// Services are NOT here — handlers close over their own service at
// construction (§2.15), so neither tools nor hooks ever see a port bag.
interface ToolCallContext {
  meta: ToolCallMeta;
  signal: AbortSignal;
}
```

Each service port is declared in its owning family folder
(`tools/<family>/port.ts`) and injected at factory construction by the
composition root (Phase 04.5). DI sits at real resource boundaries only:

```ts
interface SandboxPort {
  readonly id: SandboxId;                  // runtime/sandbox-assigned
  readFile(path: string, opts?: { offset?: number; limit?: number },
           signal?: AbortSignal): Promise<string>;
  writeFile(path: string, content: string,
            signal?: AbortSignal): Promise<void>;
  editFile(path: string, oldString: string, newString: string,
           replaceAll: boolean, signal?: AbortSignal): Promise<void>;
  startCommandSession(command: string, opts?: { timeout_ms?: number }):
    Promise<{ id: CommandId }>;          // returns promptly; no held wait
  /** Bounded wait: resolves when the command exits or after timeout_ms,
      whichever is first. The sandbox owns command state, so repeated
      waits are idempotent; the impl may long-poll server-side or poll
      internally — transport's choice. */
  waitCommand(id: CommandId, timeout_ms: number, signal?: AbortSignal):
    Promise<{ running: true } | { running: false; exit: CommandExit }>;
  writeStdin(id: CommandId, data: string, end: boolean,
             signal?: AbortSignal): Promise<void>;
  readCommandTranscript(id: CommandId, offset?: number,
                        signal?: AbortSignal):
    Promise<{ content: string; new_offset: number; running: boolean }>;
  killCommand(id: CommandId, reason: string): Promise<void>;
  enterIsolatedWorkspace(signal?: AbortSignal): Promise<void>;
  exitIsolatedWorkspace(signal?: AbortSignal): Promise<{ summary: string }>;
}

interface WorkflowPort {
  delegate(req: { workflow: string; args?: JsonObject }):
    Promise<{ workflow_run_id: WorkflowRunId; settled: Promise<WorkflowSettled> }>;
  query(id: WorkflowRunId, signal?: AbortSignal): Promise<JsonObject>;
}
```

Signal rule, applied uniformly across the ports (including Phase 04.5's
`AgentRunPort`): methods that observe or apply a bounded effect take the
call's `signal`, so a cancelled run can abandon in-flight wire I/O
instead of merely ignoring its result. Methods that create detachable
work (`startCommandSession`, `delegate`, later `spawnSubagent`) and
methods that tear it down (`killCommand`) deliberately take none —
detached work is cancelled through its `SessionHandle`, and teardown
must still succeed after the run's signal has already aborted
(`dispose`, §9).

`AgentRunState` (`run-state.ts`) is the per-run metadata record (§2.19),
assembled once by the composition root:

```ts
interface AgentRunState {
  readonly run_id: AgentRunId;
  readonly kind: AgentKind;
  readonly parent?: AgentRunId;
  readonly sandbox_id: SandboxId;
  readonly transcript_path: string;
  workspace: { isIsolated: boolean };   // the ONE mutable cell; written
                                        // only by the two mode tools,
                                        // after the SandboxPort call
                                        // succeeds
}
```

The executor's per-turn `specs()` filter and the pipeline's guard + meta
builder read `workspace.isIsolated` from it; `ToolCallMeta` above nests
its frozen `AgentRunSnapshot` (spread + freeze at the §2.4 batch
snapshot), so tools and hooks see the whole record without ever holding
the live mutable cell.

Naming rule (Phase 02 §4.1): authoring/in-process surfaces
(`ToolDefinition`, `ToolOutcome`, the family factories) are camelCase;
records that cross a process or persistence boundary (`ToolCallMeta`,
`ToolCallResult`, `HookPayload`, notification payloads) are snake_case.

## 6. Execution Pipeline (`pipeline.ts`)

`bindTool(definition, deps)` closes over run-level dependencies
(`hookEngine`, the `NotificationInbox`, the `AgentRunState`) at executor
build — definitions already close over their own services (§2.15). The `@eos/tool` batch executor (§7) keeps
batch concerns (concurrency, ordering, abort, `tool_use_id` pairing); the
pipeline owns per-call semantics:

```
1. meta = Object.freeze({ … })          tool_use_id and the workspace
                                        snapshot from the executor (§2.4)
2. abort check                          ctx.signal.aborted -> is_error
                                        "interrupted" result; defense in
                                        depth — the executor already stops
                                        dispatching queued calls on abort
3. isolated-mode guard                  meta.run.workspace.is_isolated &&
                                        !handler.availableInIsolatedWorkspace
                                        -> is_error result
4. definition.input.safeParse           fail -> is_error result (zod issue
                                        summary); never throws
5. PreToolUse hooks                     deny -> is_error result with reason;
                                        updatedInput -> re-safeParse via the
                                        SAME schema, replace (fail -> error)
6. t0; definition.execute(input, ctx);  throw -> catch, run
   t1                                   PostToolUseFailure hooks, return
                                        is_error result with timing
7. PostToolUse hooks                    success path only; context-only
8. return enriched output               is_terminal = definition.terminal
                                        && !is_error; tool_start_time = t0;
                                        tool_end_time = t1
```

Rules:

- Timing brackets step 6 only. Pre-execution rejections (steps 2-5) stamp
  both times with the rejection instant.
- A pre-hook's `updatedInput` goes back through the same Zod schema — a
  hook can rewrite input but cannot smuggle an invalid shape past
  validation.
- `additionalContext` from any hook stage is published to the
  `NotificationInbox` as a `hook_context` notification (rendered via the
  engine's `systemNotificationMessage`; seen by the model at the next
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
`is_error` results, abort settling with straggler-emit suppression (once
the signal aborts, queued calls are settled with synthetic `is_error`
results without dispatching — the pipeline's step-2 abort check is
defense in depth, not the primary mechanism), and
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
      background?.liveCount() > 0    -> await race(
        notifications.waitForNext(signal),
        steers.waitForNext(signal)); continue              (auto-wait)
      otherwise                      -> continue   (text never terminates,
                                        decision 20; maxTurns backstops)
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

- There is no completion-on-text and no engine reminder (decision 20): a
  bare-text turn with no pending steers and no live sessions appends
  nothing and re-issues the provider call; `maxTurns` is the backstop
  against spin. The planned text-return notification rule (agent
  package, §12) is the model-facing nudge; the seam it needs —
  `inbox.publish()` — already exists.
- Auto-wait consumes no turn (no provider call) and races the inbox
  against the steer queue: `waitForNext` is level-triggered (resolves
  immediately if entries are already pending, on the next arrival, or on
  abort — the loop-top check then classifies `cancelled`), and an
  arriving steer wakes the parked loop identically, so a user can
  redirect or interrupt a run waiting on slow sessions. Step 3's
  ordering still holds after the race: steers drain before
  notifications.
- A terminal result ends the run even when steers are pending: steers
  accepted mid-batch die with the run, the same determinism Phase 03
  applies to steers queued when `interrupt()` fires. The submission
  outranks late redirection.
- A run that never submits finishes `failed { kind: 'max_turns' }` with
  no `submission`; the salvaged `final_message` is the only carrier of
  partial work (a forced final-submission turn is a §12 seam).
- `AgentRunStatus`'s `completed` arm gains `submission?: JsonValue`.
- Every finish path triggers `background?.dispose(reason)` in the loop's
  `finally`, after the synchronous finish commit (§2.17) — interruption
  therefore tears down all running sessions with no composition wiring;
  `run_finished` does not wait for teardown.

The `NotificationInbox` (new `packages/engine/src/notification-inbox.ts`)
is a concrete engine-owned class — the system-side twin of the steer
queue, drained by the same loop one priority lower. It is deliberately a
plain mailbox so any publisher can use it (§2.12): the supervisor and
hook context today; trigger rules and agent-to-agent messages later, with
no inbox change:

```ts
class NotificationInbox {
  publish(message: Message, opts?: { key?: string; tag?: unknown }): void;
  drain(): Message[];        // removes all pending; fires onDrained(tags)
                             // in the same synchronous block
  onDrained(cb: (tags: unknown[]) => void): void;
  waitForNext(signal: AbortSignal): Promise<void>;   // level-triggered
}

/** The one rendering helper: wraps any JSON payload as a user message
    containing <system_notification>{json}</system_notification>. */
function systemNotificationMessage(payload: JsonObject): Message;
```

The inbox stores already-rendered `Message`s, dedupes on the opaque `key`
(a pending entry with the same key is replaced), and hands back opaque
`tag`s on drain — the supervisor self-subscribes via `onDrained` to mark
its own sessions delivered (§9).

`StartAgentRunInput`: `tools: ToolRegistry` is replaced by
`tools: ToolExecutor`; `notifications?: NotificationInbox` and
`background?: BackgroundSupervisor` are added — both constructed by the
composition root (the same instances handed to the tool factories) and
consulted by the loop for drain, the auto-wait gate, and
dispose-on-finish. `TurnConfig.toolSpecs` becomes a thunk over
`tools.specs()`. Engine loop tests construct real inboxes and supervisors
in-test (no fakes needed — both classes are concrete); with no inbox and
no supervisor, Phase 03 transcript semantics are preserved unchanged.
Termination deliberately is NOT: the Phase 03 bare-text
`finish(completed)` is removed (decision 20), so ported loop tests that
ended on a text turn now end through a scripted terminal result or
`maxTurns`.

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
  // …ToolCallMeta fields: tool_use_id, run (the full AgentRunSnapshot —
  // run_id, kind, parent, sandbox_id, transcript_path, workspace)
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

## 9. Background Supervisor (engine-owned)

Session lifecycle is loop lifecycle (§2.17), so background management is
a dedicated engine folder, `packages/engine/src/background/` (Rust
parity: `background/` is an `eos-engine` module directory) — `session.ts`
holds the data contracts (`SessionRef`, `SessionStatus`, `SessionOutcome`,
`SessionHandle`, `SessionRow`), `supervisor.ts` the `BackgroundSupervisor`
class with its status machine and dispose latch. It is generic: the
engine never learns what a "subagent" is.

```ts
// Engine-side: type is an open string. The narrow
// "subagent" | "workflow" | "command" union is a tool-side refinement
// (cancel_background_session validates it with a Zod enum).
interface SessionRef { type: string; id: string }

type SessionStatus = "running" | "completed" | "failed" | "cancelled"
                   | "delivered";

interface SessionOutcome {
  status: "completed" | "failed" | "cancelled";
  summary: string;             // one line; detail stays behind read tools
}

/** Capability record handed over by the spawn site (§2.18). */
interface SessionHandle {
  settled: Promise<SessionOutcome>;        // push; resolves exactly once
  cancel(reason: string): Promise<void>;   // closes over the right port
  describe?(): string;                     // for list_background_sessions
}

class BackgroundSupervisor {
  constructor(inbox: NotificationInbox);   // self-subscribes onDrained
  register(ref: SessionRef, spawnedBy: ToolUseId,
           handle: SessionHandle): void;
  cancel(ref: SessionRef, reason: string): Promise<boolean>;
  list(): SessionRow[];                    // running + undelivered-terminal
  liveCount(): number;                     // running only: auto-wait gate
  openCount(): number;                     // running + undelivered-terminal:
                                           // submission guard
  dispose(reason: string): Promise<void>;  // cancel all running; latches
}
```

Lifecycle rules:

- `running -> completed | failed | cancelled -> delivered -> evicted`
  (map keyed `"${type}:${id}"`). Terminal-status and delivered are
  separate facts (the model must never miss a completion); eviction
  (removal from the map) requires both.
- A handle settles exactly once (the Claude Code dual-delivery lesson); a
  settle against a non-running session is dropped silently — this is the
  cancel race: `cancel()` transitions to `cancelled` immediately,
  publishes, then calls `handle.cancel` for teardown, and the late
  natural settle is ignored.
- The supervisor owns rejection mapping: a `settled` promise that REJECTS
  settles the session as `failed` with the error message as `summary`.
  Spawn sites hand over raw promise chains and never need a `.catch`; no
  rejection can escape as unhandled.
- Every terminal transition publishes one `session_settled` notification
  via `systemNotificationMessage` (`key = "type:id"`, `tag = ref`); the
  constructor's `onDrained` subscription marks the supervisor's own tags
  delivered — delivery bookkeeping never leaves the class.
- `liveCount()` counts `running` only and backs the loop's auto-wait gate
  (§7). `openCount()` adds undelivered-terminal sessions and backs the
  submission guard: the model cannot submit past a settlement it has not
  yet seen. Guarding on `liveCount` alone would make submit-vs-settle a
  race — allowed or denied depending on whether the session settled
  before or after the guard ran, silently dropping the pending
  notification on the allowed side.
- `dispose(reason)` cancels all running sessions and LATCHES the
  supervisor: any later `register` (an abandoned `execute()` continuation
  finishing after an abort) immediately invokes the incoming handle's
  `cancel(reason)` and registers nothing — no session outlives the run
  unsupervised, and nothing is published. The LOOP triggers dispose on
  every finish (§2.17) — on the success path the submission guard already
  proved zero open sessions, so it is a no-op there.

What each spawn site passes as its `SessionHandle` (no driver classes —
the capabilities close over the right port at the call site, §2.18):

| Spawn site | `settled` | `cancel(reason)` |
| --- | --- | --- |
| `delegate_workflow` | `settled` from `WorkflowPort.delegate` | workflow API cancel |
| `exec_command` | a `waitCommand` poll loop started at promotion — the bounded yield-window wait has already returned, and the sandbox owns command state, so re-waiting on `command_id` is idempotent | `SandboxPort.killCommand` |

Phase 04.5's `run_subagent` follows the same shape (decision 21):
`settled` from the child run's outcome, cancel via the child run's
interrupt — no supervisor or engine change.

`exec_command` promotion (the hybrid). The yield-window wait and the
background watch use the same `waitCommand` primitive; exactly one waiter
addresses the sandbox at any moment — the background poll starts only
after the foreground wait has returned:

```
sandbox.startCommandSession(cmd) -> { id }
r = sandbox.waitCommand(id, clamp(yield_time_ms, 1, 30_000), ctx.signal)
                                                            // default 1_000
  exited within window  -> return transcript output synchronously
  still running / abort -> supervisor.register({type:'command', id},
                             toolUseId, {
                               settled: watchCommand(sandbox, id),
                               cancel: (r) => sandbox.killCommand(id, r),
                             })
                           return { command_id, status: 'running',
                                    transcript: partial output }

watchCommand(sandbox, id)        // sandbox-family helper: the background
  loop:                          // poll the user-visible "monitor"
    r = await sandbox.waitCommand(id, BACKGROUND_WAIT_MS)   // e.g. 10_000
    if (!r.running) return toSessionOutcome(r.exit)
```

`watchCommand` deliberately takes no run signal: it must keep observing
through teardown so that `cancel` -> `killCommand` -> the next bounded
wait reports the exit (the late settle is then dropped by the status
machine as usual).

The abort arm exists because the command is already running in the
sandbox: registering makes it reachable by `dispose` (or by the §9 latch,
which cancels it on the spot if dispose already ran). The executor's
synthetic "interrupted" result then supersedes the return value; the
registration is the side effect that matters.

`command_stdin` and `read_command_transcript` address sessions by
`command_id` through `SandboxPort` directly, independent of the supervisor —
they work identically for still-yielding and backgrounded sessions.

## 10. System Notifications (one generic inbox)

There is no tool-side notifications module. The engine owns the mailbox
(`NotificationInbox`, §7), the renderer (`systemNotificationMessage`),
and the heaviest publisher (the supervisor, §9). Publishers compose; the
inbox never changes:

| Publisher | Payload | key / tag |
| --- | --- | --- |
| supervisor (§9) | `{ type: "session_settled", session: { type, id }, status, summary }` | `key = "type:id"`, `tag = ref` |
| hook pipeline (§6) | `{ type: "hook_context", tool_use_id, text }` | unkeyed, untagged |
| trigger rules (later, §12) | rule-defined | rule-defined |
| agent-to-agent (later, §12) | sender-defined | sender-defined |

Rendering happens at publish (`systemNotificationMessage(payload)`); the
inbox stores plain `Message`s, so new publishers never require inbox or
engine changes.

Delivery stays transactional with the drain: `drain()` removes entries
and fires `onDrained(tags)` in the same synchronous block (the
supervisor's self-subscription marks its sessions delivered), so no
interleaved publish or second drain can double-deliver or skip a
session. Crash durability is explicitly NOT claimed: delivered-marking
is in-memory this phase, and a persistence phase must commit the
appended messages before marking sessions delivered — marking at drain
time and persisting later would strand a settlement on a crash between
the two.

Rendered notifications enter both transcript lists as ordinary user
messages (the wrapper text is the discriminator); a displayed-side `isMeta`
projection is a §12 seam.

## 11. Tool Families, Schemas, Toolsets (`tools/`, `toolset.ts`)

Contracts additions: `AgentKind = "main" | "planner" | "worker" | "advisor"
| "subagent"` (Zod enum) and branded ids `AgentRunId` (mint + adopt),
`WorkflowRunId` (adopt-only), `CommandId` (adopt-only; the sandbox
assigns), `SandboxId` (adopt-only; the runtime/sandbox assigns).

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
| `submit_<kind>_outcome` ×5 | `{ summary: string, payload?: JsonObject }` | `terminal: true`; guard: `openCount() > 0` -> error (running + undelivered, §9) |
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
| main | all sandbox + all agent (Phase 04.5) + workflow + background + `submit_main_outcome` |
| worker | all sandbox + background + `submit_worker_outcome` |
| subagent | all sandbox + background + `submit_subagent_outcome` |
| planner | `read`, `multi_read` + `submit_planner_outcome` |
| advisor | `read`, `multi_read` + `submit_advisor_outcome` |

The agent block in the main row is recorded here as the product target
but activates only in Phase 04.5, when `agentTools(agents, supervisor)`
arrives with its port (decision 21) — the assembly already skips
families whose port is absent.

Construction and registration are separate layers, and **ports stop at
construction**. Each tool file exports a factory taking exactly the
service(s) that tool uses (`readTool(sandbox)`,
`execCommandTool(sandbox, supervisor)`, …); the family `index.ts` is just
the per-family aggregate of those per-tool factories (§2.15):

```ts
sandboxTools(sandbox: SandboxPort, supervisor: BackgroundSupervisor,
             workspace: AgentRunState["workspace"])   // mode tools flip it
workflowTools(workflows: WorkflowPort, supervisor: BackgroundSupervisor)
backgroundTools(supervisor: BackgroundSupervisor)
submissionTool(kind: AgentKind, supervisor: BackgroundSupervisor)
// each returns ToolDefinition[] — services are fully absorbed here;
// Phase 04.5 adds agentTools(agents, supervisor) the same way
```

Registration never sees a port. The composition root calls the factories
and hands `buildToolExecutor` only finished definitions:

```ts
buildToolExecutor({ runState, definitions, inbox, hookEngine })
```

It consults `AGENT_TOOLSET` for `runState.kind` and intersects that row
with the supplied definitions: a row name with no definition — workflow
tools when no `WorkflowPort` was configured, the agent family until
Phase 04.5 constructs it (decision 21) — is simply skipped, and a
definition outside the row is excluded. Every kept definition is bound
through the §6 pipeline; the result is the engine `ToolExecutor`: a
deterministic sorted registry (prompt-cache stability), per-turn `specs()`
filtering on `runState.workspace.isIsolated` ×
`availableInIsolatedWorkspace`, and batch dispatch. Its signature is
stable: adding a tool family or a new service changes factory calls at
the composition root, never the registration machinery.

## 12. Deferred and Rejected

Deferred (named seams):

| Deferred behavior | Seam left by this phase |
| --- | --- |
| Real `SandboxPort` over the sandbox host | `SandboxPort` interface; happy fake is the only impl |
| The agent tool family — `AgentRunPort` + `run_subagent`, `ask_advisor`, `read_agent_run_transcript` (decision 21) | `AGENT_TOOLSET` already names the tools; the assembly's port-presence rule includes the family when 04.5 supplies `agents` |
| Real `WorkflowPort`, composition root, hook config loading, JSONL transcript writer | Phase 04.5 (`@eos/agent-runtime`) |
| Per-kind submission payload schemas | the `SUBMISSIONS` table holds a schema slot per kind |
| Rule-content hook matchers (`Bash(git *)`) | `matcher` is a string; the match function is one site |
| Async hooks, `prompt`/`agent`/`http` hook kinds | `HookCommand` is a discriminated union; new arms are additive |
| Notification rules, wired by the future agent package — budget tiers (75/100/125%), the text-return "keep working / submit the terminal tool" reminder (decision 20) | `inbox.publish()` is the entry; a rule evaluator is just one more publisher |
| Agent-to-agent notifications | another run's runtime publishes into this run's inbox via the same `publish()` |
| Push-based command completion (sandbox event channel) | `waitCommand` is the only completion read; upgrading bounded waits to push changes one port-method implementation, not the supervisor or `watchCommand`'s contract |
| Auto-wait watchdog (sessions live but never settle) | the `waitForNext` site; classify as `failed` after a ceiling — until then, a steer or `interrupt()` is the only escape (§7) |
| Forced final-submission turn on `max_turns` | the §7 max-turns bullet; `outcome.submission` stays absent until then |
| Awaited session teardown on finish | the loop's `finally` dispose call site (§2.17) |
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
- Per-kind `SessionDriver` classes (§2.18): spawn-site capability handles
  instead.
- A Sleep/wait tool: auto-wait is an engine rule (§2.11).

## 13. Workspace Changes

- `packages/tool/`: new package `@eos/tool` (`dependencies`:
  `@eos/contracts`, `@eos/engine` via `workspace:*`, `zod`).
- `packages/engine/`: §7 restructure — `tools.ts` and `tool-runner.ts`
  deleted; `tool-executor.ts`, `notification-inbox.ts`, and the
  `background/` folder added; runner tests move to `@eos/tool` with the
  relocated logic; loop tests port to a scripted `ToolExecutor`; inbox
  and supervisor suites are engine tests.
- `packages/contracts/`: `AgentKind`, four branded ids, `ToolCallResult`
  (additive).
- `packages/testkit/`: first real content — `@eos/testkit` (`dependencies`:
  `@eos/contracts`, `@eos/tool`): happy `SandboxPort` (in-memory files +
  scripted command sessions whose `waitCommand` reports exit under test
  control, plus a concurrent-waiter counter for §15 case 8), fake
  `WorkflowPort` with resolvable `settled` promises,
  transcript fixture writer for hook tests.
- No new third-party dependencies.

Resulting layout (files this phase owns; NEW / MOD / DEL relative to
Phase 03):

```
packages/
├─ contracts/src/
│  ├─ ids.ts                 MOD  + AgentRunId (mint), WorkflowRunId,
│  │                              CommandId, SandboxId (adopt-only)
│  ├─ agents.ts              NEW  AgentKind
│  ├─ tool-calls.ts          NEW  ToolCallResult
│  └─ index.ts               MOD  re-exports
├─ engine/src/
│  ├─ agent-loop.ts          MOD  §7 branches + normalization + projection
│  ├─ tool-executor.ts       NEW  ToolExecutor port + ToolUseBlock
│  ├─ notification-inbox.ts  NEW  NotificationInbox +
│  │                              systemNotificationMessage() (the steer
│  │                              queue's system-side twin)
│  ├─ background/            NEW  dedicated background management (§9)
│  │  ├─ session.ts               SessionRef, SessionStatus,
│  │  │                           SessionOutcome, SessionHandle, SessionRow
│  │  └─ supervisor.ts            BackgroundSupervisor (status machine,
│  │                              dispose latch)
│  ├─ turn.ts                MOD  toolSpecs thunk over tools.specs()
│  ├─ run-handle.ts          MOD  completed arm gains submission?
│  ├─ events.ts              MOD  tool_execution_completed grows 4 fields
│  ├─ tools.ts               DEL  -> tool-executor.ts (port only)
│  └─ tool-runner.ts         DEL  -> tool/src/executor.ts
├─ tool/                     NEW package @eos/tool
│  ├─ src/
│  │  ├─ contract.ts         ToolDefinition<I>, ToolOutcome,
│  │  │                      ToolCallContext, ToolCallMeta
│  │  ├─ define.ts           defineTool() defaults + Zod -> ToolSpec
│  │  ├─ pipeline.ts         bindTool(): the §6 stages
│  │  ├─ executor.ts         batch dispatch (relocated runner) +
│  │  │                      terminal-solo; implements ToolExecutor
│  │  ├─ toolset.ts          AGENT_TOOLSET + buildToolExecutor()
│  │  ├─ run-state.ts        AgentRunState: frozen per-run facts +
│  │  │                      the one mutable workspace cell (§2.19)
│  │  ├─ hooks/
│  │  │  ├─ protocol.ts      events, payload/output, config schema,
│  │  │  │                   precedence kernel
│  │  │  └─ runner.ts        HookEngine: command + callback adapters
│  │  ├─ tools/              one folder per family: port.ts owns the
│  │  │  │                   family's service (§2.15), index.ts is the
│  │  │  │                   factory, one file per tool
│  │  │  ├─ sandbox/
│  │  │  │  ├─ port.ts                  SandboxPort
│  │  │  │  ├─ read.ts
│  │  │  │  ├─ multi-read.ts
│  │  │  │  ├─ write.ts
│  │  │  │  ├─ edit.ts
│  │  │  │  ├─ exec-command.ts          §9 yield/promotion
│  │  │  │  ├─ command-stdin.ts
│  │  │  │  ├─ read-command-transcript.ts
│  │  │  │  ├─ enter-isolated-workspace.ts
│  │  │  │  ├─ exit-isolated-workspace.ts
│  │  │  │  └─ index.ts                 sandboxTools()
│  │  │  ├─ workflow/
│  │  │  │  ├─ port.ts                  WorkflowPort
│  │  │  │  ├─ delegate-workflow.ts
│  │  │  │  ├─ query-workflow.ts
│  │  │  │  └─ index.ts                 workflowTools()
│  │  │  ├─ background/
│  │  │  │  ├─ list-background-sessions.ts
│  │  │  │  ├─ cancel-background-session.ts
│  │  │  │  └─ index.ts                 backgroundTools()
│  │  │  └─ submission/
│  │  │     └─ index.ts                 SUBMISSIONS table +
│  │  │                                 submissionTool(kind): the five
│  │  │                                 tools are ONE parameterized
│  │  │                                 definition — separate files
│  │  │                                 would quintuplicate it
│  │  └─ index.ts
│  └─ tests/                 pipeline, executor (relocated suite), hooks
│                            (incl. real spawned scripts), families,
│                            toolset — supervisor + inbox suites live in
│                            engine tests
└─ testkit/src/
   ├─ happy-sandbox.ts       NEW  in-memory SandboxPort
   ├─ fake-workflow-port.ts  NEW  resolvable settled promises
   ├─ transcript-fixture.ts  NEW  fixture JSONL for hook tests
   └─ index.ts               NEW
```

Deliberate absences: no `hooks/builtin.ts` (§2.5 — guards are plain code
in `pipeline.ts`, `submission/`, and `workflow/`); no `runtime.ts` port
bag (§2.15 — every port lives in its owning family folder); no tool-side
`background/` or `notifications.ts` (§2.12, §2.17 — the supervisor, the
inbox, and the renderer are engine modules).

## 14. Migration Steps

1. Contracts additions (`AgentKind`, ids) -> verify: contracts tests green.
2. Engine §7 restructure (`ToolExecutor` port, batch normalization,
   terminal-only exit per decision 20, `NotificationInbox` +
   `BackgroundSupervisor` + auto-wait + dispose-on-finish; loop tests
   ported to a scripted executor) -> verify: ported Phase 03 loop suite
   green plus the new loop and supervisor cases (§15 cases 2-7, 21-22).
3. `@eos/tool` contract + `defineTool` + pipeline + batch executor
   (relocated runner logic and tests; hooks stubbed pass-through) ->
   verify: pipeline order, guard, parse, stamping tests plus the relocated
   runner suite (cap 8, ordering, abort settling, terminal-solo — §15
   cases 1, 10).
4. Hook protocol + runner (callback first, then command adapter with real
   spawned scripts) -> verify: §15 cases 10-13.
5. Session registration at the spawn sites (`SessionHandle` capability
   records, `exec_command` promotion) -> verify: §15 cases 8-9.
6. Tool families over testkit fakes + `buildToolExecutor` assembly ->
   verify: §15 cases 14-16, 18-19.
7. Workspace wiring -> verify: `pnpm run check` green from
   `eos-agent-core/`.
8. Update the migration `index.md` row for this phase.

## 15. Verification

All suites in-process; no network, no real sandbox.

| # | Case | Asserts |
| --- | --- | --- |
| 1 | Terminal-solo policy | terminal + sibling batch: every call `is_error`, nothing dispatched; solo terminal dispatches |
| 2 | Terminal exit | terminal result finishes `completed` with `submission` = structured content; `final_message` is the submitting assistant message |
| 3 | Auto-wait | bare-text turn + live sessions: loop awaits, a published notification resumes it, drained at step 3; a steer resumes it identically (and drains first); no turn consumed while waiting |
| 4 | Text never terminates | bare-text turn, no steers, no sessions: nothing appended, loop continues; the run ends only via a terminal result or `maxTurns` (`failed`, no `submission`) |
| 5 | Steers outrank notifications | both pending at step 3: steers drain first |
| 6 | Supervisor lifecycle | running -> settled publishes once; drain marks delivered then evicts; double-settle dropped; rejected `settled` -> `failed` with the error as summary; register after dispose -> handle cancelled, nothing registered or published; `liveCount`/`openCount` correct throughout |
| 7 | Cancel race | `cancel()` publishes `cancelled`; the handle's late natural settle is ignored |
| 8 | `exec_command` promotion | fast command returns output within the yield wait; slow command returns `{ command_id }` and registers; abort during the yield window still registers (dispose can reach the running command); the background `waitCommand` poll starts only after promotion and settles the session (fake counts concurrent waiters: never more than one) |
| 9 | Cancel by `(type, id)` | cancels the right session; unknown ref -> error result; already-terminal -> no-op note |
| 10 | Pipeline order | abort check -> guard -> parse -> pre-hooks -> execute -> post-hooks; already-aborted call returns `is_error` without executing; timing brackets execute only; pre-execution rejection stamps rejection instant |
| 11 | Hook deny / exit-2 | command hook exit 2: call never executes; stderr is the model-visible reason |
| 12 | Hook updatedInput | single update re-validated and applied; invalid update -> error; two conflicting updates -> deny |
| 13 | Hook context + warnings | `additionalContext` arrives as `hook_context` notification next boundary; nonzero/garbage stdout -> passthrough + `metadata.hook_warnings` |
| 14 | Isolated-mode ban | mode flip filters next turn's specs; stale call denied by the call-time guard; sandbox tools unaffected |
| 15 | Mode turn-boundary | `enter_isolated_workspace` batch siblings execute under the old mode — including a sibling dispatched AFTER the flip completes (batch snapshot, §2.4) |
| 16 | Submission guard | submit with running OR settled-but-undelivered sessions -> error naming them; after cancel/settle+delivery -> succeeds |
| 17 | (moved to Phase 04.5) | subagent round-trip ships with the agent family and the real `AgentRunPort` (decision 21) |
| 18 | Workflow pair | `delegate_workflow` registers + returns id; second open delegate denied; `query_workflow` passes through |
| 19 | Executor assembly | each kind gets exactly its table row (minus the Phase 04.5 agent block) + one submission tool; deterministic order; each factory receives only its own service; `buildToolExecutor` receives definitions only (no ports); workflow tools skipped when not constructed |
| 20 | Serialization point | structured content stringified once in the projected `tool_result` block; intact in `ToolCallResult`, events, and `outcome.submission` |
| 21 | Engine normalization | an executor that drops a result: the missing `tool_use_id` gets a synthetic `is_error` result; `outcome.llm` stays provider-valid |
| 22 | Dispose on finish | interrupt with running sessions: the loop's finish triggers dispose; every session handle's `cancel` is invoked; `run_finished` does not wait for teardown |

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
  testkit edits, drop the index row. With no inbox and no supervisor,
  Phase 03 behavior is preserved except bare-text termination, which this
  phase removes on purpose (decision 20); restoring it means reverting
  the loop's step-6 `continue` branch.

## 17. Acceptance Criteria

Phase 04 is accepted when:

- `@eos/tool` exposes the §5 contract with `defineTool` fail-closed
  defaults, and tool authors never see `is_terminal` or timing fields,
- the §6 pipeline enforces abort/guard/parse/hook/stamp order and never
  throws,
- the engine implements §7 exactly: tool machinery removed behind one
  injected `ToolExecutor` port, batch-result normalization, terminal exit
  with `submission` on the outcome, notification drain below steers,
  auto-wait woken by notifications AND steers, dispose-on-finish, and the
  terminal-only exit rule (decision 20: bare text never completes a run,
  no engine reminder) — with Phase 03 transcript semantics preserved
  under a scripted executor with no inbox and no supervisor,
- hooks run only from operator config (no built-ins), with the §8 exit-code
  protocol, precedence kernel, and single-update rule,
- the engine-owned supervisor is generic over `{ type, id }` refs and
  spawn-site `SessionHandle`s (no driver classes), with push-only single
  settles, supervisor-owned rejection mapping, delivery-then-evict, a
  dispose latch on every finish (late registrations auto-cancelled), and
  a working `exec_command` promotion whose background `waitCommand` poll
  starts only at promotion (never more than one waiter against the
  sandbox) and which registers on abort,
- the four Phase 04 tool families build per the §11 tables against
  testkit fakes (the agent family is Phase 04.5, decision 21), each
  factory injected with exactly its own service and no ambient port
  record anywhere in a call path,
- `AgentRunState` holds data only, with `workspace.isIsolated` as its
  single mutable cell (§2.19), and tools observe it solely through the
  frozen `ToolCallMeta` projection,
- the §15 suite passes under `pnpm run check` with no network I/O,
- the Rust `agent-core/` tree is byte-for-byte unchanged,
- and the migration `index.md` lists Phase 04 with status and verification.

## 18. Progress Tracker

| Step | Status | Required proof |
| --- | --- | --- |
| Contracts additions | Pending | contracts tests green with `AgentKind` + 4 ids |
| Engine restructure (executor port, inbox, supervisor) | Pending | ported Phase 03 loop suite green + §15 cases 2-7, 21-22 |
| Contract + pipeline + executor | Pending | §15 cases 1, 10 plus relocated runner suite and defineTool default tests |
| Hook protocol + runner | Pending | §15 cases 11-13 incl. real spawned scripts |
| Spawn-site session handles | Pending | §15 cases 8-9 |
| Tool families + toolsets | Pending | §15 cases 14-16, 18-20 |
| Workspace wiring | Pending | `pnpm run check` green; `git diff --stat -- agent-core` empty |
| Index updated | Pending | Phase 04 row in `index.md` |
