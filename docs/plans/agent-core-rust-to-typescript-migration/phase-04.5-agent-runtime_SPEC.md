# EOS Agent Core Rust to TypeScript Migration - Phase 04.5 Agent Runtime

Status: Proposed
Date: 2026-06-10
Owner: eos-agent-core
Migration direction: Rust -> TypeScript
Project path: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core`
Rust source boundary: `agent-core/crates/eos-agent-run` (run lifecycle,
launcher), `agent-core/crates/eos-tool/src/registry.rs` (`ToolRuntime`
composition), `agent-core/crates/eos-engine/src/background` (per-run session
runtime ownership)
Depends on: Phase 04 (`@eos/tool`, engine seams), Phase 03 (`@eos/engine`),
Phase 02 (`@eos/contracts`, `@eos/llm-client`)

## 1. Intent

Phase 04.5 introduces `@eos/agent-runtime` (renaming the empty
`@eos/runtime` stub): the composition root where process-level services and
per-run objects meet. It owns:

- `AgentRuntime.startRun()` — the per-run assembly: notification queue,
  background supervisor, `ToolRuntime`, toolset, engine `startAgentRun`,
  disposal — in one wiring order, in one file,
- the run registry (typed map of active runs; mints `AgentRunId`),
- the real `AgentRunPort`: subagent spawning recurses into `startRun`,
  advisor asks run a child to completion, transcript reads serve the JSONL,
- the per-run JSONL transcript writer — the artifact hooks read
  (`transcript_path`) and `read_agent_run_transcript` serves,
- the event fan-out adapter over the engine's single-consumer stream,
- hook config loading (`.eos-agents/hooks.json`).

Real `SandboxPort` and `WorkflowPort` backends remain out of scope; the
runtime accepts whatever port implementations it is given (tests use the
`@eos/testkit` happy sandbox). This phase is where the Phase 04 design
becomes a runnable multi-agent system end to end against fakes.

This phase is additive (one stub rename). The Rust implementation remains
live; nothing under `agent-core/` changes.

## 2. Design Decisions

1. **One pair per run.** The notification queue and supervisor are created
   per agent run, never shared: notifications target exactly one
   conversation, `liveCount()` backs exactly one run's submission guard,
   and disposal must not touch a sibling run's sessions. Subagents get
   their own pair via the same factory — the hierarchy needs no tree; a
   parent's subagent driver just watches the child's outcome.
2. **The wiring order is the spec.** queue -> supervisor -> `onDrained`
   delivery wiring -> `ToolRuntime` -> toolset -> engine start -> dispose
   subscription. Each arrow is a real dependency; the order lives in one
   function so neither `@eos/engine` nor `@eos/tool` ever learns process
   topology.
3. **Two lifetimes, one boundary.** Process-level services (LLM client,
   sandbox factory, workflow port, hook config) are bound at
   `createAgentRuntime`; everything per-run is built in `startRun`. The
   runtime is the only layer that holds both.
4. **The transcript JSONL is the one cross-cutting artifact.** Hooks read
   it (`transcript_path`), `AgentRunPort.readTranscript` serves it by byte
   offset, and Phase 04's notification design assumes it exists. It is
   written by the runtime's own event subscriber — not by the engine, not
   by tools.
5. **Fan-out is a runtime adapter, not an engine change.** Phase 03's
   event stream is deliberately single-consumer; the runtime is that single
   consumer and re-broadcasts to its subscribers (transcript writer always;
   caller subscribers optionally). Backpressure remains a server-phase
   concern.
6. **Subagent recursion is just `startRun`.** `spawnSubagent` starts a
   child run of kind `subagent` with its own queue/supervisor/toolset and
   maps the child's outcome to `SubagentSettled`. `askAdvisor` is the same
   minus backgrounding: start kind `advisor`, await the outcome, return the
   submission. No second execution path exists.
7. **Port absence shrinks the toolset.** If no `WorkflowPort` is
   configured, workflow tools are not registered (Phase 04 §11 rule) — the
   model never sees tools that cannot work, instead of receiving runtime
   errors.

## 3. Scope

In scope:

- rename `packages/runtime` -> `packages/agent-runtime`
  (`@eos/runtime` -> `@eos/agent-runtime`; the stub is package.json-only),
- `AgentRuntime` (`createAgentRuntime`, `startRun`), run registry,
- real `AgentRunPort`, transcript writer + reader, event fan-out,
- hook config loading with Zod validation,
- disposal and parent-child cancellation,
- the §13 integration suite over `MockLlmClient` + testkit fakes.

Out of scope (named seams in §10):

- real sandbox transport, real workflow backend,
- persistence beyond the transcript JSONL (`@eos/db` records, resume),
- server transports, observability wiring, run-level authn/quotas,
- compaction, scheduling/admission control.

## 4. Composition Root (`runtime.ts`)

```ts
interface AgentRuntimeServices {
  llm: LlmClient;                          // already configured (Phase 02.5)
  sandbox: (runId: AgentRunId) => SandboxPort;   // per-run workspace binding
  workflows?: WorkflowPort;                // absent -> no workflow tools
  hookConfigPath?: string;                 // default: .eos-agents/hooks.json
  dataDir: string;                         // transcript root
}

interface StartRunParams {
  kind: AgentKind;
  prompt: string;
  model: string;
  systemPrompt?: string;
  maxTurns?: number;
  parent?: AgentRunId;                     // set for subagent/advisor children
  signal?: AbortSignal;
}

interface StartedRun {
  run_id: AgentRunId;
  handle: AgentRunHandle;                  // steer / interrupt / outcome
  subscribe(): AsyncIterable<AgentEvent>;  // fan-out tap (§6)
  transcript_path: string;
}
```

`startRun` wiring order (decision 2):

```
1. run_id = mintAgentRunId(); registry.add(run_id, kind, parent)
2. queue = new SystemNotificationQueue()
3. supervisor = new BackgroundSupervisor(queue, drivers)
4. queue.onDrained(refs => supervisor.markDelivered(refs))
5. workspace = new WorkspaceState()
6. runtime: ToolRuntime = { sandbox: services.sandbox(run_id),
     agents: agentRunPort, workflows: services.workflows, supervisor }
7. { tools, toolSpecs } = buildToolset(kind, { runtime, hookEngine,
     workspace, runIdentity, transcriptPath })
8. handle = startAgentRun({ llmClient, tools, toolSpecs,
     notifications: createNotificationSource(queue, supervisor), … })
9. broadcaster = fanOut(handle.events)         // sole stream consumer
   broadcaster.subscribe(transcriptWriter)
10. handle.outcome.finally(() => { supervisor.dispose('run finished');
      registry.settle(run_id) })
```

Step 10 is the only place run-end and supervisor lifecycles meet: on the
success path the submission guard already proved zero live sessions; on
cancel/failure paths `dispose` cancels stragglers through each driver
(subagent children receive `interrupt`, commands are killed, workflows
cancelled).

## 5. Run Registry and `AgentRunPort` (`registry.ts`, `agent-port.ts`)

The registry is one typed map: `Map<AgentRunId, { kind, parent?,
handle, transcript_path, status }>`. Terminal runs stay listed until their
parent (if any) has settled them — transcript reads against finished runs
must keep working — and are evicted with their parent.

`AgentRunPort` implementation:

- `spawnSubagent(req)` -> `runtime.startRun({ kind: 'subagent', parent })`;
  returns `{ run_id, settled }` where `settled` maps the child's
  `AgentRunOutcome` to `SubagentSettled { status, summary, submission? }`
  (summary from the child's submission, or the failure/cancel reason).
- `askAdvisor(req, signal)` -> `startRun({ kind: 'advisor', parent,
  signal })`, `await outcome`, return the advisor's submission `{ answer }`;
  the caller's abort propagates through `signal` into the child run.
- `readTranscript(runId, offset?)` -> byte-offset read of the run's JSONL
  (registry lookup; works for live and finished runs).

The parent's subagent **driver** (Phase 04 §9) consumes `settled`; the
port stays mechanism, the supervisor stays policy.

## 6. Transcript Writer and Event Fan-out (`transcript.ts`, `fan-out.ts`)

`fanOut(events)` consumes the engine stream once and re-emits to N
subscribers (push, per-subscriber buffer; a slow caller tap never blocks
the transcript writer). `subscribe()` after `run_finished` replays nothing
and completes immediately — `outcome` is the completion surface, parity
with Phase 03 §8.

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
`transcript_path` in every Phase 04 `HookPayload` and `ToolCallMeta` — the
hook-state story depends on it existing for every run, including children.

## 7. Hook Config Loading (`hook-config.ts`)

`loadHookConfig(path)`: read `hookConfigPath` (default
`.eos-agents/hooks.json`), `safeParse` against the Phase 04
`HookConfigEntry[]` schema. Missing file -> `[]` (no hooks). Malformed file
-> startup error naming the Zod issues — config errors fail loudly at
`createAgentRuntime`, never silently mid-run. One `HookEngine` is built per
runtime and shared by all runs (hook commands are stateless processes; the
per-call payload carries all identity).

## 8. Disposal and Cancellation

| Trigger | Effect |
| --- | --- |
| run finishes (any status) | `supervisor.dispose` cancels leftover sessions; registry marks terminal |
| parent run disposed with live subagent | subagent driver cancel -> child `handle.interrupt('parent disposed')` -> child's own dispose cascades |
| caller `signal` aborts | engine cancels (Phase 03 semantics); step-10 disposal runs off `outcome` |
| `cancel_background_session` on a subagent | same child-interrupt path, model-initiated |

The cascade is depth-first through drivers; no global kill switch exists —
each run only ever touches sessions it registered.

## 9. Public API (`index.ts`)

```ts
function createAgentRuntime(services: AgentRuntimeServices): AgentRuntime;

interface AgentRuntime {
  startRun(params: StartRunParams): StartedRun;
  getRun(runId: AgentRunId): StartedRun | undefined;
  listRuns(): ReadonlyArray<{ run_id, kind, status, parent? }>;
}
```

Identity stamping of events (Phase 03 §8 deferral) stays deferred: a
`StartedRun` tap serves one run; multiplexed envelopes belong to the server
phase.

## 10. Deferred (named seams)

| Deferred behavior | Seam left by this phase |
| --- | --- |
| Real sandbox transport | `services.sandbox` factory signature |
| Real workflow backend | `services.workflows?` + toolset shrink rule |
| DB-backed run records / resume | `TranscriptLine` + `AgentRunOutcome` carry what a recorder needs |
| Server transport, multiplexed event envelopes | `StartedRun.subscribe()` per run |
| Hook config hot-reload / per-project layering | `loadHookConfig` is one call site |
| Run admission control / concurrency budget | `startRun` is the single entry |

## 11. Workspace Changes

- `packages/runtime/` renamed to `packages/agent-runtime/`; package name
  `@eos/agent-runtime` (`dependencies`: `@eos/contracts`, `@eos/engine`,
  `@eos/tool` via `workspace:*`). Phase 03 §11's `@eos/runtime` references
  resolve to this package.
- `packages/testkit/`: gains a scripted `MockLlmClient` scenario helper if
  the engine's double is promoted (second consumer now exists); otherwise
  the runtime suite keeps a local copy.
- No new third-party dependencies (`node:fs/promises`, `node:crypto`
  suffice).

## 12. Migration Steps

1. Rename the stub package -> verify: `pnpm install` + workspace resolution
   green.
2. Transcript writer + fan-out -> verify: ordered lines, offset reads,
   slow-tap isolation tests.
3. Registry + `AgentRunPort` (spawn recursion, advisor await, transcript
   read) -> verify: §13 cases 3-5.
4. Hook config loading -> verify: missing/valid/malformed cases.
5. `createAgentRuntime` + `startRun` wiring + disposal -> verify: §13
   cases 1-2, 6-8.
6. Workspace wiring -> verify: `pnpm run check` green.
7. Update the migration `index.md` row for this phase.

## 13. Verification

Integration suite over `MockLlmClient` scripts + testkit happy sandbox; no
network, real files only under a temp `dataDir`.

| # | Case | Asserts |
| --- | --- | --- |
| 1 | Wiring order | a `startRun` smoke run produces transcript lines, drains a notification, disposes on finish (spy ordering matches §4) |
| 2 | Submission end-to-end | scripted main run calls `submit_main_outcome`; `outcome.submission` carries the payload; transcript `run_finished` line matches |
| 3 | Subagent round-trip | main spawns subagent (real child run), idles -> auto-wait, `session_settled` notification arrives, parent reads child transcript via the tool, then submits |
| 4 | Advisor ask | `ask_advisor` blocks, child advisor run submits, answer returns in the tool result; caller abort mid-ask cancels the child |
| 5 | Command promotion live | scripted slow command promotes to the supervisor; settle -> notification -> `read_command_transcript` returns full output |
| 6 | Disposal cascade | interrupting the parent cancels the live child run and kills its command session; both registries settle |
| 7 | Hook script over transcript | a real spawned node hook denies a call based on `transcript_path` contents (read-before-write style assertion) |
| 8 | Port absence | runtime without `workflows` registers no workflow tools; specs never mention them |

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
  the §4 wiring in order, with queue/supervisor pairs strictly per-run,
- subagent and advisor execution are both `startRun` recursion (no second
  path), with parent-abort propagation and the §8 disposal cascade covered
  by tests,
- every run (including children) has a readable JSONL transcript that hook
  scripts and `read_agent_run_transcript` consume by offset,
- hook config loads fail loudly at startup and absent config means no
  hooks,
- toolsets shrink when ports are absent,
- the §13 suite passes under `pnpm run check` with no network I/O,
- the Rust `agent-core/` tree is byte-for-byte unchanged,
- and the migration `index.md` lists Phase 04.5 with status and
  verification.

## 16. Progress Tracker

| Step | Status | Required proof |
| --- | --- | --- |
| Package rename | Pending | workspace resolves `@eos/agent-runtime` |
| Transcript + fan-out | Pending | §13 writer/tap tests green |
| Registry + AgentRunPort | Pending | §13 cases 3-5 |
| Hook config loading | Pending | missing/valid/malformed cases green |
| Composition root + disposal | Pending | §13 cases 1-2, 6-8 |
| Workspace wiring | Pending | `pnpm run check` green; `git diff --stat -- agent-core` empty |
| Index updated | Pending | Phase 04.5 row in `index.md` |
