# EOS Agent Core Rust to TypeScript Migration - Phase 04.5 Agent Runtime

Status: Proposed
Date: 2026-06-10
Owner: eos-agent-core
Migration direction: Rust -> TypeScript
Project path: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core`
Rust source boundary: `agent-core/crates/eos-agent-run` (run lifecycle,
launcher), `agent-core/crates/eos-engine/src/background` (per-run session
runtime ownership)
Depends on: Phase 04 (`@eos/tool`, engine seams), Phase 03 (`@eos/engine`),
Phase 02 (`@eos/contracts`, `@eos/llm-client`)

## 1. Intent

Phase 04.5 introduces `@eos/agent-runtime`: the composition root where
process-level dependencies and per-run engine objects meet. It owns:

- `AgentRuntime.startRun()` - the per-run assembly: notification inbox,
  background supervisor (both engine classes, constructed per run),
  tool executor, engine `startAgentRun` - in one wiring order, in one file;
  this is the start method for main, subagent, and advisor runs,
- the run registry (typed map of active runs; mints `AgentRunId`),
- the agent tool runtime calls: `run_subagent` and `ask_advisor` call
  `startRun`, while `read_agent_run_transcript` reads the JSONL path
  recorded for a run,
- the per-run JSONL transcript writer - the artifact hooks read
  (`transcript_path`) and `read_agent_run_transcript` serves,
- the event broadcaster over the engine's single-consumer stream,
- hook config loading (`.eos-agents/hooks.json`).

Phase 04.5 does not add provider backends, external execution backends, or
other orchestration backends. The runtime accepts the already-built tool
definitions it is given, adds the agent-run tools it owns, and runs the
engine against fakes in its integration suite.

This phase is additive (one stub rename). The Rust implementation remains
live; nothing under `agent-core/` changes.

## 2. Design Decisions

1. **One pair per run.** The notification inbox and supervisor (both
   engine classes, Phase 04 §2.12) are constructed here per agent run,
   never shared: notifications target exactly one conversation,
   `liveCount()` backs exactly one run's submission guard, and disposal
   must not touch a sibling run's sessions. Subagent and advisor runs get
   their own pair via the same factory; a parent's subagent
   `SessionHandle` just watches that run's outcome.
2. **The wiring order is the spec.** inbox -> supervisor -> run state ->
   runtime-owned tool definitions -> `buildToolExecutor` -> engine start
   -> registry-settle subscription. Each arrow is a real dependency; the
   order lives in one function so neither `@eos/engine` nor `@eos/tool`
   ever learns process topology. Session teardown is engine-owned (Phase
   04 §2.17): this root wires none of it.
3. **Two lifetimes, one boundary.** Process-level dependencies (LLM client,
   agent profile store, caller-provided base tool definitions, hook config)
   are bound at `createAgentRuntime`; everything per-run is built in
   `startRun`. The runtime is the only layer that holds both.
4. **The transcript JSONL is the one cross-cutting artifact.** Hooks read
   it (`transcript_path`), `read_agent_run_transcript` serves it by byte
   offset, and Phase 04's notification design assumes it exists. It is
   written by the runtime's own event subscriber - not by the engine, not
   by tools.
5. **Event broadcasting is a runtime adapter, not an engine change.**
   Phase 03's event stream is deliberately single-consumer; the runtime is
   that single consumer and re-broadcasts to its subscribers (transcript
   writer always; caller subscribers optionally). Backpressure remains a
   server-phase concern.
6. **Subagent and advisor execution are just `startRun`.** `startRun` is
   parameterized by `AgentType`, not a separate spawn helper. `run_subagent`
   calls `startRun({ agentType: 'subagent', parent })`, registers
   `handle.outcome.then(...)` with the background supervisor, and returns
   immediately. `ask_advisor` calls `startRun({ agentType: 'advisor',
   parent })`, awaits `handle.outcome`, and returns the advisor submission
   as the tool result. No second execution path exists.
7. **The runtime vocabulary is agent type.** Phase 04.5 uses
   `AgentType`/`agentType` for run selection (`main`, `planner`, `worker`,
   `subagent`, `advisor`). If earlier TypeScript phases still expose
   `AgentKind`, align that contract name before implementing this phase.
8. **Profiles resolve before engine start.** `startRun` loads the
   `AgentType` profile before constructing the engine input. Profile data
   contributes the default model and base system prompt; caller
   `systemPrompt` appends run-specific instructions. Tool selection is
   still assembled by the runtime from `agentType` and available tool
   definitions before `startAgentRun`; the engine receives only a resolved
   `systemPrompt` and `ToolExecutor`.
9. **Initial messages are ordered user messages.** `initialMessages` is a
   non-empty list because some call sites need separable user messages
   (for example transcript-as-evidence, then an instruction about that
   transcript). The system prompt stays a request field, never a message.
   The runtime accepts `Message` values, not raw prompt strings, so callers
   make message boundaries explicit.
10. **`signal` is lifecycle input, not prompt data.** The optional
    `AbortSignal` belongs on `startRun` because cancellation is owned by
    the caller's lifecycle: a UI stop button, server request abort,
    parent-run disposal, or an `ask_advisor` tool cancellation can all
    terminate the run without changing its prompts, profile, or tools. The
    runtime passes this signal to `startAgentRun`; while the process is
    alive, the handle resolves a `cancelled` `AgentRunOutcome`.

## 3. Scope

In scope:

- keep the package at `packages/agent-runtime`
  (`@eos/agent-runtime`; the stub is package.json-only at phase start),
- `AgentRuntime` (`createAgentRuntime`, `startRun`), run registry,
- agent tool runtime calls, transcript writer + reader, event broadcaster,
- hook config loading with Zod validation,
- disposal and parent-run cancellation,
- the §13 integration suite over `MockLlmClient` + in-process fakes.

Out of scope (named seams in §10):

- external execution backends and backend-specific tools,
- persistence beyond the transcript JSONL (`@eos/db` records, resume),
- server transports, observability wiring, run-level authn/quotas,
- compaction, scheduling/admission control.

## 4. Composition Root (`runtime.ts`)

```ts
interface AgentRuntimeDependencies {
  llm: LlmClient;                          // already configured (Phase 02.5)
  profiles: AgentProfileStore;             // loads AgentType profile data
  baseTools?: readonly ToolDefinition[];   // optional process-level tools
  hookConfigPath?: string;                 // default: .eos-agents/hooks.json
  dataDir: string;                         // transcript root
}

type UserMessage = Message & { role: "user" };

interface StartRunParams {
  agentType: AgentType;
  initialMessages: readonly [UserMessage, ...UserMessage[]];
  model?: string;                          // overrides profile default
  systemPrompt?: string;                   // appended after profile prompt
  maxTurns?: number;
  parent?: AgentRunId;                     // set for subagent/advisor runs
  signal?: AbortSignal;                    // caller cancellation scope
}

interface StartedRun {
  run_id: AgentRunId;
  handle: AgentRunHandle;                  // steer / interrupt / outcome
  subscribe(): AsyncIterable<AgentEvent>;  // event-broadcaster tap (§6)
  transcript_path: string;
}
```

`startRun` implementation sketch:

```ts
function startRun(params: StartRunParams): StartedRun {
  if (params.agentType === "main" && params.parent !== undefined) {
    throw new TypeError("main runs cannot have a parent");
  }

  const run_id = mintAgentRunId();
  const profile = dependencies.profiles.load(params.agentType);
  const systemPrompt = composeSystemPrompt(profile.systemPrompt, params.systemPrompt);
  const model = params.model ?? profile.model;
  const inbox = new NotificationInbox();
  const supervisor = new BackgroundSupervisor(inbox);
  const transcript_path = transcriptPathFor(dependencies.dataDir, run_id);

  const runState = createAgentRunState({
    run_id,
    agentType: params.agentType,
    parent: params.parent,
    transcript_path,
  });
  registry.add(runState);

  const definitions = [
    ...(dependencies.baseTools ?? []),
    ...agentTools(
      {
        startRun: (next) => startRun({ ...next, parent: run_id }),
        resolveTranscriptPath: (target) => registry.transcriptPath(target),
        readTranscriptFile,
      },
      supervisor,
    ),
    ...backgroundTools(supervisor),
    submissionTool(params.agentType, supervisor),
  ];

  const tools = buildToolExecutor({
    runState,
    definitions,
    inbox,
    hookEngine,
  });

  const handle = startAgentRun({
    llmClient: dependencies.llm,
    tools,
    notifications: inbox,
    background: supervisor,
    model,
    systemPrompt,
    maxTurns: params.maxTurns,
    signal: params.signal,
    initialMessages: [...params.initialMessages],
  });

  const broadcaster = createEventBroadcaster(handle.events);
  const transcriptWriter = new TranscriptWriter(transcript_path);
  broadcaster.subscribe((event) => transcriptWriter.append(event));
  registry.attach(run_id, handle);
  handle.outcome.finally(() => {
    void transcriptWriter.flush().finally(() => registry.settle(run_id));
  });

  return {
    run_id,
    handle,
    subscribe: () => broadcaster.subscribe(),
    transcript_path,
  };
}
```

`startRun` wiring order (decision 2):

```
1. run_id = mintAgentRunId()
2. inbox = new NotificationInbox()               // engine class
3. supervisor = new BackgroundSupervisor(inbox)  // engine class; self-
                                                 // subscribes for delivery
4. profile = dependencies.profiles.load(agentType)
5. systemPrompt/model = profile defaults + startRun overrides
6. transcript_path = runs/<run_id>/transcript.jsonl
7. runState = createAgentRunState({ run_id, agentType, parent,
     transcript_path })                          // Phase 04 §2.19
   registry.add(runState)                        // facts stored once
8. definitions = [
     ...(dependencies.baseTools ?? []),
     ...agentTools({ startRun, resolveTranscriptPath, readTranscriptFile }, supervisor),
     ...backgroundTools(supervisor),
     submissionTool(agentType, supervisor),
   ]
   tools = buildToolExecutor({ runState, definitions, inbox, hookEngine })
9. handle = startAgentRun({ llmClient, tools, notifications: inbox,
     background: supervisor, systemPrompt, model,
     initialMessages, ... })
10. broadcaster = createEventBroadcaster(handle.events) // sole consumer
   broadcaster.subscribe(transcriptWriter)
11. handle.outcome.finally(() => registry.settle(run_id))
```

Session teardown needs no wiring here: the engine loop triggers
`supervisor.dispose(reason)` on every finish (Phase 04 §2.17), cancelling
stragglers through each spawn site's `SessionHandle`. Step 9 is pure
registry bookkeeping.

## 5. Run Registry and Agent Tool Runtime Calls (`registry.ts`)

The registry is one typed map: `Map<AgentRunId, { state: AgentRunState,
handle, status }>` - the run facts live exactly once, in the state record
(Phase 04 §2.19); the registry adds only what the record must not hold
(the live handle, the registry-level status). Terminal runs stay listed
until their parent (if any) has settled them; transcript reads against
finished runs must keep working.

Agent-family tools receive narrow bound functions, not a service object:

- `run_subagent` receives a start function that can call
  `startRun({ agentType: 'subagent', parent })`; it registers
  `handle.outcome.then(mapSubagentOutcome)` as the `SessionHandle.settled`
  promise and returns the session/run reference immediately.
- `ask_advisor` receives a start function that can call
  `startRun({ agentType: 'advisor', parent, signal })`; it awaits the
  returned handle's outcome and maps the advisor submission to its tool
  result.
- `read_agent_run_transcript` resolves `run_id -> transcript_path` through
  a bound runtime lookup, then calls `readTranscriptFile(path, offset)`.
  The model never supplies a raw filesystem path.

Tool flow:

```ts
// main: external caller starts the primary run.
const main = runtime.startRun({
  agentType: "main",
  initialMessages: [fromUserText(prompt)],
  model,
  signal,
});
return main;

// ask_advisor: synchronous tool result.
const advisor = startRun({
  agentType: "advisor",
  parent,
  initialMessages: [
    fromUserText(callerTranscript),
    fromUserText(
      "Read the transcript and verify if the caller submitted the payload correctly.",
    ),
  ],
  model,
  signal,
});
const advisorOutcome = await advisor.handle.outcome;
return mapAdvisorOutcome(advisorOutcome);

// run_subagent: background session.
const subagent = startRun({
  agentType: "subagent",
  parent,
  initialMessages: [fromUserText(prompt)],
  model,
});
supervisor.register(
  { type: "subagent", id: subagent.run_id },
  toolUseId,
  {
    settled: subagent.handle.outcome.then(mapSubagentOutcome),
    cancel: async (reason) => {
      subagent.handle.interrupt(reason);
      await subagent.handle.outcome;
    },
  },
);
return { run_id: subagent.run_id };
```

## 6. Transcript Writer and Event Broadcaster (`transcript.ts`, `event-broadcaster.ts`)

`createEventBroadcaster(events)` consumes the engine stream once and
re-emits to N subscribers (push, per-subscriber buffer; a slow caller tap
never blocks the transcript writer). `subscribe()` after `run_finished`
replays nothing and completes immediately - `outcome` is the completion
surface, parity with Phase 03 §8.

`TranscriptWriter` appends one JSON line per conversation-shaping event to
`<dataDir>/runs/<run_id>/transcript.jsonl`:

```ts
type TranscriptLine =
  | { seq, ts, kind: 'user' | 'assistant'; message: Message }
  | { seq, ts, kind: 'tool_result'; result: ToolCallResult }
  | { seq, ts, kind: 'notification'; text: string }
  | { seq, ts, kind: 'run_finished'; outcome_status: string;
      submission?: JsonValue };
```

Writes go through one append queue per run (ordered, awaited before
`readTranscript` returns, flushed on `run_finished`). This file is the
`transcript_path` in every Phase 04 `HookPayload` and `ToolCallMeta` - the
hook-state story depends on it existing for every run, including
subagent/advisor runs.

## 7. Hook Config Loading (`hook-config.ts`)

`loadHookConfig(path)`: read `hookConfigPath` (default
`.eos-agents/hooks.json`), `safeParse` against the Phase 04
`HookConfigEntry[]` schema. Missing file -> `[]` (no hooks). Malformed file
-> startup error naming the Zod issues - config errors fail loudly at
`createAgentRuntime`, never silently mid-run. One `HookEngine` is built per
runtime and shared by all runs (hook commands are stateless processes; the
per-call payload carries all identity).

## 8. Disposal and Cancellation

| Trigger | Effect |
| --- | --- |
| run finishes (any status) | the ENGINE triggers `supervisor.dispose` (Phase 04 §2.17); the runtime only marks the registry terminal |
| owning run disposed with live subagent | the subagent `SessionHandle.cancel` -> `handle.interrupt('parent disposed')` -> that run's own engine dispose cascades |
| caller `signal` aborts | engine cancels (Phase 03 semantics) and disposes on finish |
| `cancel_background_session` on a subagent | same subagent interrupt path, model-initiated |

The cascade is depth-first through session handles; no global kill switch
exists - each run only ever touches sessions it registered.

## 9. Public API (`index.ts`)

```ts
function createAgentRuntime(dependencies: AgentRuntimeDependencies): AgentRuntime;

interface AgentRuntime {
  startRun(params: StartRunParams): StartedRun;
  getRun(runId: AgentRunId): StartedRun | undefined;
  listRuns(): ReadonlyArray<{ run_id, agentType, status, parent? }>;
}
```

Identity stamping of events (Phase 03 §8 deferral) stays deferred: a
`StartedRun` tap serves one run; multiplexed envelopes belong to the server
phase.

## 10. Deferred (named seams)

| Deferred behavior | Seam left by this phase |
| --- | --- |
| Backend-specific tool families | `dependencies.baseTools` accepts already-built definitions |
| DB-backed run records / resume | `TranscriptLine` + `AgentRunOutcome` carry what a recorder needs |
| Server transport, multiplexed event envelopes | `StartedRun.subscribe()` per run |
| Hook config hot-reload / per-project layering | `loadHookConfig` is one call site |
| Run admission control / concurrency budget | `startRun` is the single entry |

## 11. Workspace Changes

- `packages/agent-runtime/`; package name `@eos/agent-runtime`
  (`dependencies`: `@eos/contracts`, `@eos/engine`, `@eos/tool` via
  `workspace:*`). Phase 03 §11's runtime references resolve to this package.
- `packages/testkit/`: gains a scripted `MockLlmClient` scenario helper if
  the engine's double is promoted (second consumer now exists); otherwise
  the runtime suite keeps a local copy.
- No new third-party dependencies (`node:fs/promises`, `node:crypto`
  suffice).

Resulting layout:

```
packages/agent-runtime/
├─ src/
│  ├─ runtime.ts          createAgentRuntime() + startRun() §4 wiring
│  ├─ registry.ts         run map, AgentRunId minting, parent links
│  ├─ agent-tools.ts      bound runtime calls for subagent/advisor/transcript
│  ├─ transcript.ts       per-run JSONL writer + offset reader
│  ├─ event-broadcaster.ts single-consumer stream -> N subscribers
│  ├─ hook-config.ts      loadHookConfig()
│  └─ index.ts
├─ tests/                 §13 integration suite
└─ package.json           @eos/agent-runtime; deps: @eos/contracts,
                          @eos/engine, @eos/tool, @eos/llm-client
```

`@eos/agent-runtime` is the only package that depends on everything; the
workspace dependency graph stays acyclic with the composition root on top
(contracts <- engine <- tool <- testkit; agent-runtime consumes all).

## 12. Migration Steps

1. Verify the stub package -> verify: `pnpm install` + workspace resolution
   green.
2. Transcript writer + event broadcaster -> verify: ordered lines, offset
   reads, slow-tap isolation tests.
3. Registry + agent tool runtime calls (subagent start, advisor await,
   transcript read) -> verify: §13 cases 3-4.
4. Hook config loading -> verify: missing/valid/malformed cases.
5. `createAgentRuntime` + `startRun` wiring + disposal -> verify: §13
   cases 1-2, 5-7.
6. Workspace wiring -> verify: `pnpm run check` green.
7. Update the migration `index.md` row for this phase.

## 13. Verification

Integration suite over `MockLlmClient` scripts + in-process fakes; no
network, real files only under a temp `dataDir`.

| # | Case | Asserts |
| --- | --- | --- |
| 1 | Wiring order | a `startRun` smoke run produces transcript lines, drains a notification, and observes the engine-triggered dispose on finish (spy ordering matches §4) |
| 2 | Submission end-to-end | scripted main run calls `submit_main_outcome`; `outcome.submission` carries the payload; transcript `run_finished` line matches |
| 3 | Subagent round-trip | main starts a subagent run, idles -> auto-wait, `session_settled` notification arrives, parent reads the subagent transcript via the tool, then submits |
| 4 | Advisor ask | `ask_advisor` blocks, advisor run submits, answer returns in the tool result; caller abort mid-ask cancels the advisor run |
| 5 | Disposal cascade | interrupting the parent cancels the live subagent run; both registries settle |
| 6 | Hook script over transcript | a real spawned node hook denies a call based on `transcript_path` contents (read-before-write style assertion) |
| 7 | Event broadcast isolation | transcript subscriber receives every event while a slow caller subscriber lags or returns early |

Commands:

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core
pnpm install
pnpm run check
```

- Rust boundary hygiene: `git diff --stat -- agent-core` stays empty.
- Docs hygiene: `git diff --check -- docs/plans/agent-core-rust-to-typescript-migration eos-agent-core`.

## 14. Coexistence and Rollback

- Coexistence: the Rust implementation remains live; `@eos/agent-runtime`
  has no server or CLI consumer yet and is exercised only by its suite.
- Rollback: revert the rename, delete the package contents, drop the index
  row. Phases 02-04 are unaffected.

## 15. Acceptance Criteria

Phase 04.5 is accepted when:

- `@eos/agent-runtime` exposes exactly the §9 API and `startRun` performs
  the §4 wiring in order, with inbox/supervisor pairs strictly per-run,
- subagent and advisor execution is `startRun` recursion (no second path),
  with parent-abort propagation and the §8 disposal cascade covered by
  tests,
- every run (including subagent/advisor runs) has a readable JSONL
  transcript that hook scripts and `read_agent_run_transcript` consume by
  offset,
- hook config loads fail loudly at startup and absent config means no
  hooks,
- the §13 suite passes under `pnpm run check` with no network I/O,
- the Rust `agent-core/` tree is byte-for-byte unchanged,
- and the migration `index.md` lists Phase 04.5 with status and
  verification.

## 16. Progress Tracker

| Step | Status | Required proof |
| --- | --- | --- |
| Package rename | Done | workspace resolves `@eos/agent-runtime` |
| Transcript + event broadcaster | Pending | §13 writer/tap tests green |
| Registry + agent tool runtime calls | Pending | §13 cases 3-4 |
| Hook config loading | Pending | missing/valid/malformed cases green |
| Composition root + disposal | Pending | §13 cases 1-2, 5-7 |
| Workspace wiring | Pending | `pnpm run check` green; `git diff --stat -- agent-core` empty |
| Index updated | Pending | Phase 04.5 row in `index.md` |
