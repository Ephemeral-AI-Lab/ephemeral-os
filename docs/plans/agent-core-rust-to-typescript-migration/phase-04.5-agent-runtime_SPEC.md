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
  (`transcript_path`) and `read_agent_run_transcript` serves; the runtime
  is the single consumer of each run's engine event stream,
- hook config loading (`.eos-agents/hooks.json`).

Phase 04.5 does not add provider backends, external execution backends, or
other orchestration backends. The runtime accepts the already-built tool
definitions it is given, adds the agent-run tools it owns, and runs the
engine against fakes in its integration suite.

This phase is additive except for the owned `@eos/tool` and `@eos/engine`
changes (§11): profile-selected tool binding (the kind-keyed
`AGENT_TOOLSET` table is deleted), hook `additionalContext` carried on
the tool result and published by the engine (decision 11), and
`AgentRunState` gaining `agent_name`. The Rust implementation remains
live; nothing under `agent-core/` changes.

## 2. Design Decisions

1. **One pair per run.** The notification inbox and supervisor (both
   engine classes, Phase 04 §2.12) are constructed here per agent run,
   never shared: notifications target exactly one conversation,
   `liveCount()` backs exactly one run's submission guard, and disposal
   must not touch a sibling run's sessions. Subagent and advisor runs get
   their own pair via the same factory; the caller's subagent
   `SessionHandle` just watches that run's outcome.
2. **The wiring order is the spec.** The §4 `startRun` sketch is the
   order; no second normative copy is kept. Each step is a real
   dependency, and the order lives in one function so neither
   `@eos/engine` nor `@eos/tool` ever learns process topology. Session
   teardown is engine-owned (Phase 04 §2.17): this root wires none of it.
3. **Two lifetimes, one boundary.** Process-level dependencies (LLM client,
   agent profile store, caller-provided base tool definitions, hook config)
   are bound at `createAgentRuntime`; everything per-run is built in
   `startRun`. The runtime is the only layer that holds both.
4. **The transcript JSONL is the one cross-cutting artifact.** Hooks read
   it (`transcript_path`), `read_agent_run_transcript` serves it by byte
   offset, and Phase 04's notification design assumes it exists. It is
   written by the runtime's own event subscriber - not by the engine, not
   by tools.
5. **The runtime is the stream's single consumer.** Phase 03's event
   stream is deliberately single-consumer; the runtime consumes
   `handle.events` directly into the transcript writer and exposes no
   second event surface this phase. The multiplexing broadcaster (and its
   backpressure policy) arrives with the server phase, which brings the
   first second consumer (§10).
6. **Subagent and advisor execution are just `startRun`.** `startRun` is
   agent-name driven, not a separate spawn helper and not
   `AgentKind`-parameterized. `run_subagent` passes its requested
   agent name to `startRun(...)`, registers `handle.outcome.then(...)`
   with the background supervisor, and returns immediately. `ask_advisor`
   calls `startRun(...)` with the advisor profile name
   (`ADVISOR_AGENT_NAME`, one exported constant in `agent-tools.ts` -
   the single site that owns the magic name), awaits `handle.outcome`,
   and returns the advisor submission as the tool result. No second
   execution path exists.
7. **The public runtime vocabulary is agent name.** `AgentKind` stays as
   profile data (`agent_kind: main | planner | worker | subagent |
   advisor`) and as a derived run fact, but callers do not pass it to
   `startRun`.
8. **Profiles resolve before engine start.** `startRun` loads the
   named agent profile from the runtime's `AgentProfileRegistry` before
   constructing the engine input. Profile data contributes the agent kind,
   LLM client id, system prompt, max turns, allowed tools, and explicit
   terminal tool. The runtime resolves `llm_client_id` through
   `.eos-agents/llm_clients.json` to get the configured client, auth
   source, model id, and reasoning effort. Tool selection has exactly one
   source: `selectProfileDefinitions` keeps `allowed_tools + terminal_tool`
   from the available definitions, and `buildToolExecutor` binds exactly
   what it is given - the kind-keyed `AGENT_TOOLSET` table is deleted
   (§11). The engine receives only a resolved `systemPrompt`, LLM client,
   model id, reasoning effort, turn limit, and `ToolExecutor`.
9. **Initial messages are ordered user messages.** `initialMessages` is a
   non-empty list because some call sites need separable user messages
   (for example transcript-as-evidence, then an instruction about that
   transcript). The system prompt stays a request field, never a message.
   The runtime accepts `Message` values, not raw prompt strings, so callers
   make message boundaries explicit.
10. **`signal` is lifecycle input, not prompt data.** The optional
    `AbortSignal` belongs on `startRun` because cancellation is owned by
    the caller's lifecycle: a UI stop button, server request abort,
    caller-run disposal, or an `ask_advisor` tool cancellation can all
    terminate the run without changing its prompts, profile, or tools. The
    runtime passes this signal to `startAgentRun`; while the process is
    alive, the handle resolves a `cancelled` `AgentRunOutcome`.
11. **Hook context rides the result; the engine is its only publisher.**
    Phase 04 §6 routed hook `additionalContext` through an executor-held
    inbox reference - two optional parameters that had to be the same
    instance (`BuildToolExecutorInput.inbox`,
    `StartAgentRunInput.notifications`), with silent context loss when
    one was missing. The §11 owned change moves the transport onto the
    result: the pipeline accumulates `additionalContext` under
    `metadata.hook_contexts` (beside the existing
    `metadata.hook_warnings`), and the engine loop publishes each entry
    as a `hook_context` notification when it appends the tool result.
    Delivery timing is unchanged (drained at the next loop boundary);
    `buildToolExecutor` loses its `inbox` parameter, so the executor has
    exactly one output - the result - and the inbox is wired only at
    `startAgentRun`. Tools still never see the inbox.

## 3. Scope

In scope:

- keep the package at `packages/agent-runtime`
  (`@eos/agent-runtime`; the stub is package.json-only at phase start),
- `AgentRuntime` (`createAgentRuntime`, `startRun`), run registry,
- `AgentProfileRegistry` + profile loader for `.eos-agents/profiles/*.md`,
- `LlmClientRegistry` + config loader for `.eos-agents/llm_clients.json`,
- agent tool runtime calls, transcript writer + reader,
- the §11 owned `@eos/tool` and `@eos/engine` changes (decisions 8, 11),
- hook config loading with Zod validation,
- disposal and caller-run cancellation,
- the §13 integration suite over `MockLlmClient` + in-process fakes.

Out of scope: the seams named in §10.

## 4. Composition Root (`runtime.ts`)

```ts
interface AgentRuntimeDependencies {
  agentProfilesDir?: string;               // default: .eos-agents/profiles
  llmClientsPath?: string;                 // default: .eos-agents/llm_clients.json
  llmClients?: LlmClientRegistry;          // optional in-memory test override
  baseTools?: readonly ToolDefinition[];   // optional process-level tools
  hookConfigPath?: string;                 // default: .eos-agents/hooks.json
  dataDir: string;                         // transcript root
}

type UserMessage = Message & { role: "user" };

interface StartRunParams {
  agentName: string;
  initialMessages: readonly [UserMessage, ...UserMessage[]];
  signal?: AbortSignal;                    // caller cancellation scope
}

interface StartedRun {
  runId: AgentRunId;
  handle: AgentRunHandle;                  // steer / interrupt / outcome
  transcriptPath: string;
}
```

Naming follows one rule package-wide: snake_case for serialized or
config-derived DTO fields (profile frontmatter, `llm_clients.json`,
`AgentRunState`, transcript lines), camelCase for everything in-process.
An interface that holds a live value - a handle, a signal, a function -
is in-process by definition and is camelCase throughout: `StartRunParams`
and `StartedRun` follow the engine's `StartAgentRunInput` precedent
("camelCase; never serialized").

`createAgentRuntime` loads agent profiles at startup:

```ts
function createAgentRuntime(dependencies: AgentRuntimeDependencies): AgentRuntime {
  const agentProfiles = loadAgentProfileRegistry(
    dependencies.agentProfilesDir ?? ".eos-agents/profiles",
  );
  const llmClients =
    dependencies.llmClients ??
    loadLlmClientRegistry(
      dependencies.llmClientsPath ?? ".eos-agents/llm_clients.json",
    );
  const hookEngine = buildHookEngine(loadHookConfig(dependencies.hookConfigPath));
  return createRuntime({ ...dependencies, agentProfiles, llmClients, hookEngine });
}
```

`agent-profile-loader.ts` parses one Markdown file into frontmatter plus
body. `agent-profile-registry.ts` loads the directory once and performs
ALL static validation at startup - Zod schema, duplicate `name` values,
and the §13 case 1 tool-selection rules - so `startRun` never
re-validates a profile and never registers a run that validation could
still reject. The name universe for that validation is static: each
runtime-owned tool family exports a name-only constant (including the
terminal tool names - no supervisor is needed to know them), and
`baseTools` contributes each `definition.name`. Lookup is by agent name
only:

```ts
interface AgentProfile {
  name: string;
  description: string;
  llm_client_id: string;
  max_turns: number;
  agent_kind: AgentKind;
  allowed_tools: readonly ToolName[];
  terminal_tool: ToolName;
  system_prompt: string;                   // Markdown body after frontmatter
  source_path: string;                     // diagnostics only, never API input
}

interface AgentProfileRegistry {
  require(agentName: string): AgentProfile;
}
```

Profile file format:

```md
---
name: worker
description: Worker
llm_client_id: codex_coding_plan
max_turns: 100
agent_kind: worker
allowed_tools:
  - read
  - multi_read
  - write
  - edit
  - exec_command
  - command_stdin
  - read_command_transcript
  - list_background_sessions
  - cancel_background_session
  - ask_advisor
terminal_tool: submit_worker_outcome
---

You are the worker for one assigned work item.

Complete only the `<work_item>` in your context. Treat `<needs>` as fixed
direct dependency outcomes. If delegated workflow tools are available and a
subtask needs decomposition, you may delegate it, then inspect or cancel all
outstanding workflow handles before your terminal submission.

Before terminal submission, call `ask_advisor` with
`tool_name="submit_worker_outcome"` and the exact payload you intend to
send.
```

`allowed_tools` names ordinary non-terminal tools to expose. `terminal_tool`
names exactly one terminal tool to expose, separate from the allowlist. The
registry validates at load - never per run - that every `allowed_tools`
entry names a known non-terminal tool, `terminal_tool` names exactly one
known terminal tool, and the terminal name is not also listed under
`allowed_tools`. The loader does not infer ordinary tools from prose: if
the profile body tells the agent to call `ask_advisor`, `allowed_tools`
must include `ask_advisor`. The profile is the ONLY selection source
(§2.8) - which is what lets a worker profile expose `ask_advisor` at all.
`terminalToolDefinitions(supervisor)` is a name-keyed inventory of terminal
definitions; it is not keyed by `AgentKind`, and the profile selects the
terminal by `terminal_tool`.

`llm-client-registry.ts` loads `.eos-agents/llm_clients.json`, validates it
with Zod, and exposes lookup by `llm_client_id`:

```ts
interface LlmClientBinding {
  id: string;
  model_id: string;
  reasoning_effort: ReasoningEffort;
  client: LlmClient;
}

interface LlmClientRegistry {
  require(llmClientId: string): LlmClientBinding;
}
```

Config file format:

```json
{
  "clients": [
    {
      "id": "codex_coding_plan",
      "provider": "codex_coding_plan",
      "model_id": "gpt-5.5",
      "reasoning_effort": "medium",
      "base_url": "https://chatgpt.com/backend-api/codex",
      "auth": {
        "kind": "codex_cli_auth_file",
        "path": "/Users/yifanxu/.codex/auth.json"
      }
    }
  ]
}
```

The Codex loader mirrors
`packages/llm-client/e2e/support/codex-auth.ts`: read
`tokens.access_token` from the configured auth file, validate the JWT has
the ChatGPT account claim and is not expired, wrap it in `SecretString`,
then call `createLlmClient({ provider: "codex_coding_plan", base_url,
access_token })`. The token is never written back to
`.eos-agents/llm_clients.json`.

`startRun` implementation sketch:

```ts
interface StartRunContext {
  parent?: AgentRunId;                     // internal only, never public input
}

function startRun(params: StartRunParams, context: StartRunContext = {}): StartedRun {
  const profile = agentProfiles.require(params.agentName);
  const llm = llmClients.require(profile.llm_client_id);
  if (profile.agent_kind === "main" && context.parent !== undefined) {
    throw new Error("main profiles can only be started externally");
  }

  const runId = mintAgentRunId();
  const inbox = new NotificationInbox();
  const supervisor = new BackgroundSupervisor(inbox);
  const transcriptPath = runTranscriptPath(dependencies.dataDir, runId);

  const runState: AgentRunState = {
    run_id: runId,
    kind: profile.agent_kind,
    parent: context.parent,
    agent_name: profile.name,
    // Placeholder until the sandbox family phase binds real sandboxes.
    sandbox_id: sandboxIdFrom(runId),
    transcript_path: transcriptPath,
    workspace: { is_isolated: false },
  };

  const availableDefinitions = [
    ...(dependencies.baseTools ?? []),
    ...agentTools(
      {
        startRun: (next) => startRun(next, { parent: runId }),
        transcriptPathOf: (target) => registry.transcriptPathOf(target),
        readTranscriptFile,
      },
      supervisor,
    ),
    ...backgroundTools(supervisor),
    ...terminalToolDefinitions(supervisor),
  ];
  const definitions = selectProfileDefinitions(profile, availableDefinitions);

  // No inbox parameter (decision 11): hook context rides result metadata
  // and the engine publishes it; tools and the executor never see the inbox.
  const tools = buildToolExecutor({ runState, definitions, hookEngine });

  const handle = startAgentRun({
    llmClient: llm.client,
    tools,
    notifications: inbox,
    background: supervisor,
    model: llm.model_id,
    reasoningEffort: llm.reasoning_effort,
    systemPrompt: profile.system_prompt,
    maxTurns: profile.max_turns,
    signal: params.signal,
    initialMessages: [...params.initialMessages],
  });

  const transcriptWriter = new TranscriptWriter(transcriptPath);
  void (async () => {
    // Decision 5: the runtime is the stream's single consumer.
    for await (const event of handle.events) transcriptWriter.append(event);
  })();
  registry.add(runState, handle);
  handle.outcome.finally(() => {
    void transcriptWriter.flush().finally(() => registry.finish(runId));
  });

  return { runId, handle, transcriptPath };
}
```

The sketch IS the wiring order (decision 2); no second normative copy is
kept. Registration is atomic and last: everything that can fail - profile
and client lookup, definition selection, engine start - happens before
the single `registry.add(runState, handle)`, so the registry can never
hold a run without a handle or a run that will never finish. The
`outcome.finally` flush is the one authoritative flush trigger (§6 defers
to it). Session teardown needs no wiring here: the engine loop triggers
`supervisor.dispose(reason)` on every finish (Phase 04 §2.17), cancelling
stragglers through each start site's `SessionHandle`; the final
`registry.finish` subscription is pure bookkeeping. Drain order needs no
wiring either: the engine already drains steers before inbox notifications
at the loop boundary (user input outranks system notices).

## 5. Run Registry and Agent Tool Runtime Calls (`run-registry.ts`, `agent-tools.ts`)

The registry is one typed map: `Map<AgentRunId, { state: AgentRunState,
handle, status }>` - the run facts live exactly once, in the state record
(Phase 04 §2.19); the registry adds only what the record must not hold
(the live handle, the registry-level status), and `add(runState, handle)`
is the single registration call (§4). Terminal runs stay listed
until the caller-run session (if any) has observed their settlement;
transcript reads against finished runs must keep working. Caller-less
terminal runs stay listed for the process lifetime - eviction is deferred
(§10) and acceptable while the runtime is in-process only.

Agent-family tools receive narrow bound functions, not a service object.
`read_agent_run_transcript` resolves `run_id -> transcript path` through
the bound registry lookup (`transcriptPathOf`), then calls
`readTranscriptFile(path, offset, maxBytes)` - byte-offset reads with a
per-call byte cap; the model never supplies a raw filesystem path. The
subagent and advisor calls are `startRun` recursion (decision 6):

```ts
// main: external caller starts the primary run.
const main = runtime.startRun({
  agentName: "root",
  initialMessages: [fromUserText(prompt)],
  signal,
});
return main;

// ask_advisor: synchronous tool result. `signal` is the advisor tool
// call's own execution signal (the executor's per-call signal), which is
// what cancels the advisor run when the caller aborts mid-ask (§13
// case 6).
const advisor = startRun({
  agentName: ADVISOR_AGENT_NAME,
  initialMessages: [
    fromUserText(callerTranscript),
    fromUserText(
      "Read the transcript and verify if the caller submitted the payload correctly.",
    ),
  ],
  signal,
});
const advisorOutcome = await advisor.handle.outcome;
return mapAdvisorOutcome(advisorOutcome);

// run_subagent: background session; returns immediately with the run
// reference while `SessionHandle.settled` carries the mapped outcome.
// Deliberately passes NO signal: a detached run gets a fresh abort root
// and never dies with the caller's turn (the bug class the Claude Code
// study designs out) - cancellation reaches it only through the §8
// disposal cascade or `cancel_background_session`.
const subagent = startRun({
  agentName,
  initialMessages: [fromUserText(prompt)],
});
supervisor.register(
  { type: "subagent", id: subagent.runId },
  toolUseId,
  {
    settled: subagent.handle.outcome.then(mapSubagentOutcome),
    cancel: async (reason) => {
      subagent.handle.interrupt(reason);
      await subagent.handle.outcome;
    },
  },
);
return { run_id: subagent.runId };
```

## 6. Transcript Writer (`transcript.ts`)

The runtime's internal loop is the engine stream's single consumer
(decision 5): it reads `handle.events` directly and feeds the writer. No
other event surface exists this phase - `outcome` is the completion
surface (Phase 03 §8 parity), and the multiplexing broadcaster arrives
with the server phase (§10).

`TranscriptWriter` appends one JSON line per conversation-shaping event to
`<dataDir>/runs/<run_id>/transcript.jsonl`:

```ts
type TranscriptLine =
  | { seq, ts, kind: 'user'; origin: 'initial' | 'steer'; message: Message }
  | { seq, ts, kind: 'assistant'; message: Message }
  | { seq, ts, kind: 'tool_result'; result: ToolCallResult }
  | { seq, ts, kind: 'notification'; text: string }
  | { seq, ts, kind: 'run_finished'; outcome_status: string;
      interrupt_reason?: string; submission?: JsonValue };
```

Writes go through one append queue per run (ordered, awaited before
`readTranscript` returns; the §4 `outcome.finally` flush is the
authoritative flush trigger). This file is the
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
| caller run disposed with live subagent | the subagent `SessionHandle.cancel` -> `handle.interrupt('caller_disposed')` -> that run's own engine dispose cascades |
| caller `signal` aborts | engine cancels (Phase 03 semantics) and disposes on finish |
| `cancel_background_session` on a subagent | same subagent interrupt path, model-initiated |

The cascade is depth-first through session handles; no global kill switch
exists - each run only ever touches sessions it registered.

Runtime-originated interrupts use fixed reason strings - `caller_disposed`
(disposal cascade) and `model_cancelled` (`cancel_background_session`);
external aborts keep the engine default. The reason rides the `cancelled`
outcome (`handle.cancelReason`) and lands in the transcript `run_finished`
line as `interrupt_reason`.

## 9. Public API (`index.ts`)

```ts
function createAgentRuntime(dependencies: AgentRuntimeDependencies): AgentRuntime;

interface RunSummary {
  run_id: AgentRunId;
  agent_name: string;
  agent_kind: AgentKind;
  status: 'running' | 'finished';   // 'settled' stays session vocabulary
  parent?: AgentRunId;
}

interface AgentRuntime {
  startRun(params: StartRunParams): StartedRun;
  listRuns(): readonly RunSummary[];
}
```

Identity stamping of events (Phase 03 §8 deferral) stays deferred: the
transcript file serves one run; multiplexed envelopes belong to the server
phase.

## 10. Deferred (named seams)

| Deferred behavior | Seam left by this phase |
| --- | --- |
| Backend-specific tool families | `dependencies.baseTools` accepts already-built definitions |
| DB-backed run records / resume | `TranscriptLine` + `AgentRunOutcome` carry what a recorder needs |
| Server transport, multiplexed event envelopes, event broadcaster | the runtime's transcript loop is the stream's one consumer; a broadcaster slots in front of that single site when the first second consumer arrives |
| Run lookup for transports (`getRun`) | the registry already keys runs by `AgentRunId` |
| Runtime `dispose()` / process-exit cleanup | the registry holds every live handle; interrupt-all is one iteration. Required no later than the sandbox/exec family - the first time runs hold external resources |
| Caller steering of a running subagent | `AgentRunHandle.steer` exists; only a tool binding is missing |
| Reason-conditional teardown (steer-interrupt vs hard abort) | interrupt reasons are already recorded data (§8); behavior branches arrive with backgroundable `exec_command` sessions |
| Codex token expiry / auth refresh | `LlmClientRegistry.require` is the single lookup site to grow refresh-on-read; today the JWT is validated only at load |
| Eviction of finished registry entries | finish is already tracked per run; eviction is one predicate |
| Hook config hot-reload / per-project layering | `loadHookConfig` is one call site |
| Run admission control / concurrency budget | `startRun` is the single entry |

## 11. Workspace Changes

- `packages/agent-runtime/`; package name `@eos/agent-runtime`
  (`dependencies`: `@eos/contracts`, `@eos/engine`, `@eos/tool`,
  `@eos/llm-client` via `workspace:*`, plus `yaml` for profile
  frontmatter parsing). Phase 03 §11's runtime references resolve to this
  package.
- `packages/tool/`: `buildToolExecutor` binds exactly the definitions it
  is given - the `AGENT_TOOLSET` intersection and table are deleted, the
  profile being the selection source - and loses its `inbox` parameter
  (decision 11): the pipeline accumulates hook `additionalContext` under
  the result's `metadata.hook_contexts`, beside `metadata.hook_warnings`,
  instead of publishing it. `AgentRunState` gains `agent_name` (`parent`
  stays the caller link). Toolset tests move to the
  pre-selected-definitions shape; pipeline tests assert the metadata
  transport.
- `packages/engine/`: the loop publishes each `metadata.hook_contexts`
  entry as a `hook_context` notification when it appends the tool result
  - superseding Phase 04 §6's executor-published transport with the same
  next-boundary delivery, one publisher site. Phase 04 §15 case 13
  splits accordingly: the tool suite asserts the metadata, the loop suite
  asserts publish + drain.
- `packages/testkit/`: gains a scripted `MockLlmClient` scenario helper if
  the engine's double is promoted (second consumer now exists); otherwise
  the runtime suite keeps a local copy.
- `yaml` is the only new third-party dependency in this phase; all parsed
  profile data is still validated through Zod before registration.

Resulting layout:

```
packages/agent-runtime/
├─ src/
│  ├─ runtime.ts          createAgentRuntime() + startRun() §4 wiring
│  ├─ run-registry.ts     run map, AgentRunId minting, caller links
│  ├─ agent-profile-loader.ts frontmatter/body parser + Zod validation
│  ├─ agent-profile-registry.ts name-indexed registry
│  ├─ llm-client-registry.ts llm_clients.json loader + client factory binding
│  ├─ agent-tools.ts      bound runtime calls for subagent/advisor/transcript
│  ├─ transcript.ts       per-run JSONL writer + offset reader
│  ├─ hook-config.ts      loadHookConfig()
│  └─ index.ts
├─ tests/                 §13 integration suite
└─ package.json           @eos/agent-runtime; deps: @eos/contracts,
                          @eos/engine, @eos/tool, @eos/llm-client
```

`@eos/agent-runtime` is the only package that depends on everything; the
workspace dependency graph stays acyclic with the composition root on top
(contracts <- engine <- tool <- testkit; agent-runtime consumes all).

## 12. Migration Steps and Progress

| # | Step | Verify | Status |
| --- | --- | --- | --- |
| 1 | Stub package | `pnpm install` + workspace resolution green | Done |
| 2 | §11 owned `@eos/tool` + `@eos/engine` changes | toolset suite green over pre-selected definitions; `AGENT_TOOLSET` deleted; `AgentRunState.agent_name` typed; pipeline emits `metadata.hook_contexts` (no `inbox` param) and the loop suite asserts publish + drain | Pending |
| 3 | Agent profile loader + registry | §13 case 1: valid frontmatter/body profile loads by agent name; duplicate names, malformed profiles, and tool-selection violations fail at startup | Pending |
| 4 | LLM client registry | §13 case 2: `.eos-agents/llm_clients.json` loads, Codex CLI auth-file entries build `codex_coding_plan` clients, missing `llm_client_id` references fail at startup | Pending |
| 5 | Transcript writer | §13 case 9: ordered lines, offset reads, flush gated on `outcome` | Pending |
| 6 | Registry + agent tool runtime calls (subagent start, advisor await, transcript read) | §13 cases 5-6 | Pending |
| 7 | Hook config loading | missing/valid/malformed cases and §13 case 8 | Pending |
| 8 | `createAgentRuntime` + `startRun` wiring + disposal | §13 cases 3-4, 7 | Pending |
| 9 | Workspace wiring | `pnpm run check` green; `git diff --stat -- agent-core` empty | Pending |
| 10 | Update the migration `index.md` row | Phase 04.5 row with status and verification | Pending |

## 13. Verification

Integration suite over `MockLlmClient` scripts + in-process fakes; no
network, real files only under a temp `dataDir`.

| # | Case | Asserts |
| --- | --- | --- |
| 1 | Profile loader / registry | the worker-format Markdown profile loads by agent name; duplicate `name`, missing `llm_client_id`, invalid `max_turns`, unknown `allowed_tools`, unknown/non-terminal `terminal_tool`, and `terminal_tool` duplicated under `allowed_tools` fail before any run starts |
| 2 | LLM client registry | `llm_clients.json` loads the Codex coding-plan entry, reads the configured Codex auth file without persisting the token, and rejects missing client ids referenced by profiles |
| 3 | Wiring order | a `startRun` smoke run produces transcript lines, drains a notification, and observes the engine-triggered dispose on finish (spy ordering matches §4); the executor exposes exactly `allowed_tools + terminal_tool` - the worker profile's `ask_advisor` included |
| 4 | Submission end-to-end | scripted main run calls `submit_main_outcome`; `outcome.submission` carries the payload; transcript `run_finished` line matches |
| 5 | Subagent round-trip | main starts a subagent run by agent name, idles -> auto-wait, `session_settled` notification arrives, caller reads the subagent transcript via the tool, then submits |
| 6 | Advisor ask | `ask_advisor` blocks, advisor run submits, answer returns in the tool result; caller abort mid-ask cancels the advisor run |
| 7 | Disposal cascade | interrupting the caller cancels the live subagent run; both registry entries reach `finished` and the subagent `run_finished` line records `interrupt_reason: 'caller_disposed'` |
| 8 | Hook script over transcript | a real spawned node hook denies a call based on `transcript_path` contents (read-before-write style assertion), and a hook's `additionalContext` reaches the conversation as a `hook_context` notification at the next loop boundary (decision 11, end to end) |
| 9 | Transcript completeness | the writer records every conversation-shaping event including `run_finished`; `read_agent_run_transcript` sees the flushed file once `outcome` settles; offset reads return increments |

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
- Rollback: revert the §11 owned `@eos/tool` and `@eos/engine` edits,
  delete the package contents, drop the index row. Phases 02-03 are
  unaffected.

## 15. Acceptance Criteria

Phase 04.5 is accepted when:

- `@eos/agent-runtime` exposes exactly the §9 API and `startRun` performs
  the §4 wiring in order, with inbox/supervisor pairs strictly per-run
  and one atomic registration after engine start,
- tool exposure comes solely from the profile (`allowed_tools` +
  `terminal_tool`); the kind-keyed `AGENT_TOOLSET` table is gone,
- hook `additionalContext` reaches the model via `metadata.hook_contexts`
  published by the engine; `buildToolExecutor` takes no inbox
  (decision 11),
- subagent and advisor execution is `startRun` recursion (no second path),
  with caller cancellation and the §8 disposal cascade covered by
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
