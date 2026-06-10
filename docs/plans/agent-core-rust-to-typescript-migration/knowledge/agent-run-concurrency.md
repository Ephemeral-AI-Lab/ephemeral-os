# Claude Code Agent-Run Concurrency (Multiple Requests, Multiple Runs)

Status: Observed
Date: 2026-06-11
Source path: `/Users/yifanxu/machine_learning/LoVC/c c/src`
Migration context: `eos-agent-core/` TypeScript migration reference

Companion docs: `message-steering.md` (queue priorities + drain boundaries),
`tool-execution-pipeline.md` (tool-level batch concurrency),
`background-task-tracking.md` (task data model),
`subagent-state-management.md` (what state each run shares vs owns).

## One Process, Many Query Loops

There is no run scheduler object. A "run" is just an invocation of the
`query()` async generator (`query.ts`), and concurrency is plain promise
interleaving: whoever holds a generator pumps it. One process can host:

| Run kind | Started by | Pumped by | Abort root | Turn semantics |
| --- | --- | --- | --- | --- |
| Main REPL loop | `onQuery` (`screens/REPL.tsx:2869`) | the REPL's for-await | turn's controller | exactly one at a time (QueryGuard) |
| Sync subagent | `AgentTool.call()` else-branch (`AgentTool.tsx:846`) | parent's tool batch executor | shares parent (`runAgent.ts:528`) | blocks the parent turn; N in parallel per batch |
| Async/background agent | `AgentTool.call()` with `run_in_background`/`background: true` (`AgentTool.tsx:686-752`) | detached `void runAsyncAgentLifecycle(...)` (`agentToolUtils.ts:508`) | own unlinked root (`AgentTool.tsx:694-697`) | survives the parent turn and ESC |
| In-process teammate | `spawnTeammate` → `startInProcessTeammate` (`inProcessRunner.ts:1544`) | detached perpetual loop (`runInProcessTeammate`, `inProcessRunner.ts:883`) | lifecycle controller + per-turn `currentWorkAbortController` | never "completes"; idles between prompts |
| Headless/SDK conversation | `QueryEngine.submitMessage()` (`QueryEngine.ts:209`) | the SDK caller's for-await | engine's controller | serial turns per engine; engines can coexist |
| Remote agent / tmux teammate | `isolation: 'remote'`, tmux spawn | separate process | n/a (out of process) | tracked only as a `TaskState` |

```
            user prompt                    Agent tool_use blocks
                 │                                  │
        ┌────────▼────────┐    sync (≤cap 10) ┌─────▼─────┐  async   ┌──────────────┐
        │  QueryGuard      │◄────────────────│ tool batch │─────────►│ void runAsync │
        │  (one main run)  │   blocks turn    │ executor   │ detach   │ AgentLifecycle│
        └────────┬────────┘                  └───────────┘          └──────┬───────┘
                 │ query() loop                                            │ query() loop
                 │                                                         │
                 │   ◄── <task-notification> via command queue ('later') ──┘
```

## The Single-Main-Run Invariant: QueryGuard

Interactive Claude Code never runs two *main* loops concurrently. The
invariant is enforced by `QueryGuard` (`utils/QueryGuard.ts:29`), a
synchronous three-state machine compatible with `useSyncExternalStore`:

```
idle ──reserve()──► dispatching ──tryStart()──► running ──end(gen)──► idle
  └────────────────tryStart()  (direct submit) ───┘        forceEnd() (ESC)
```

- `reserve()` covers the async gap between dequeueing a command and the
  query actually starting (`isActive` is true for both `dispatching` and
  `running`, blocking re-entry from the queue processor).
- `tryStart()` atomically transitions to `running` and returns a
  **generation number**, or `null` if already running. No check-then-set.
- `end(generation)` refuses stale cleanup: if a newer run started (e.g.
  after `forceEnd()` on cancel), the old run's `finally` sees a generation
  mismatch and skips teardown (`QueryGuard.ts:75`).
- Race lost anyway? `onQuery` re-enqueues the message text and returns
  (`REPL.tsx:2869-2886`, `tengu_concurrent_onquery_detected`).

## What Happens When a Second Request Arrives

`handlePromptSubmit.ts:313-351` makes a three-way decision:

| Condition | Action |
| --- | --- |
| `queryGuard.isActive` (or external loading) | **enqueue** (`mode: prompt`, priority `next`); only prompt/bash modes are queueable — others are dropped |
| …and `hasInterruptibleToolInProgress` (all in-flight tools have `interruptBehavior === 'cancel'`, e.g. Sleep) | also `abortController.abort('interrupt')` — interrupt-and-replace |
| guard idle | wrap as a `QueuedCommand` and call `executeUserInput()` directly |

So multiple rapid requests **never fork concurrent main runs** — they
serialize through the module-level `commandQueue`
(`utils/messageQueueManager.ts:53`). The queued input is consumed at two
boundaries (details in `message-steering.md`): mid-turn after tool
execution as `queued_command` attachments (steering, `query.ts:1570`), or
at idle by `useQueueProcessor` → next turn. Genuine parallelism is opt-in
and happens one level down, at the Agent tool.

## How Runs Multiply

### 1. Parallel sync subagents inside one turn

`AgentTool.isConcurrencySafe() → true` (`AgentTool.tsx:1273`), so N
`Agent` tool_use blocks in one assistant message land in one parallel
batch (cap `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY`, default 10 — see
`tool-execution-pipeline.md`). Each call runs `runAgent()` → its own
`query()` generator; the batch executor interleaves them. The prompt
explicitly instructs the model to emit one message with multiple blocks
for parallel agents (`AgentTool/prompt.ts:271`).

### 2. Detached background agents

`shouldRunAsync` (`AgentTool.tsx:567`) = `run_in_background === true` OR
agent definition `background: true` OR coordinator mode OR fork-experiment
`forceAsync` OR assistant/proactive mode — all gated by
`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`. The async path:

1. `registerAsyncAgent` (`LocalAgentTask.tsx:466`) creates the
   `LocalAgentTaskState` (status `running`, `isBackgrounded: true`) in
   `AppState.tasks`, with an abort controller **not** linked to the
   parent's ("background agents should survive when the user presses
   ESC", `AgentTool.tsx:694-697`) and a process-exit cleanup hook.
2. Optional `name` registers in `AppState.agentNameRegistry` for
   SendMessage routing (`AgentTool.tsx:703-712`).
3. `void runWithAgentContext(... runAsyncAgentLifecycle(...))` detaches
   the pump (`AgentTool.tsx:733`); the tool returns
   `{status: 'async_launched', agentId, outputFile}` immediately.
4. The pump (`agentToolUtils.ts:554-593`) drives the generator,
   mirroring progress into the task (`updateAsyncAgentProgress`) and
   appending messages to `task.messages` only while the UI retains it.

### 3. Foreground→background promotion (mid-run)

Sync agents are registered as foreground tasks from the start
(`registerAgentForeground`, `LocalAgentTask.tsx:526`) precisely so they
can be promoted later. The sync loop races the generator against a
promotion signal:

```
Promise.race([agentIterator.next(), backgroundSignal])   AgentTool.tsx:886
```

`backgroundAgentTask()` (Ctrl-B / backgroundAll / auto-background timer,
default off, 120 s when enabled) flips `isBackgrounded` and resolves the
signal via a module-level `backgroundSignalResolvers` map
(`LocalAgentTask.tsx:519,620-652`). The promotion path
(`AgentTool.tsx:897-1051`) then:

- closes the foreground iterator (`agentIterator.return()`, 1 s timeout)
  so `runAgent`'s `finally` cleanup runs,
- starts a **fresh** `runAgent` stream with `isAsync: true`, the same
  agentId/task and the task's own abort controller — i.e. the model
  conversation restarts from the original prompt (prompt cache absorbs
  the replay); collected foreground messages only re-seed the progress
  tracker,
- returns `async_launched` to the parent, which carries on its turn.

### 4. Teammates (perpetual runs)

`spawnTeammate` → `spawnInProcessTeammate` (`utils/swarm/spawnInProcess.ts:104`)
registers an `InProcessTeammateTaskState` and fire-and-forgets
`runInProcessTeammate` (`inProcessRunner.ts:1544`). That runner is a
*loop of runs*: execute current prompt via `runAgent(isAsync: true)`,
mark `isIdle: true`, send an idle notification to the lead's mailbox,
block in `waitForNextPromptOrShutdown()` (500 ms poll over
`task.pendingUserMessages` + file mailbox), repeat. Teammate identity
flows through `AsyncLocalStorage` (`utils/teammateContext.ts:59`) so
concurrent teammates in one process don't cross-contaminate. Two abort
levels: `abortController` kills the teammate; `currentWorkAbortController`
aborts only the current turn (`InProcessTeammateTask/types.ts:29-50`).

## Re-entry: How Concurrent Runs Talk Back

Completion does not call back into the parent — it goes through the same
input queue as the user:

1. Terminal transition first (`completeAsyncAgent`/`failAsyncAgent`/
   `killAsyncAgent`) so `TaskOutput(block=true)` unblocks even if
   notification embellishment hangs (`agentToolUtils.ts:599-603`).
2. `enqueueAgentNotification` (`LocalAgentTask.tsx:197`) formats a
   `<task-notification>` XML block (task id, output file, status,
   summary, result, usage, worktree) and enqueues it at priority
   `'later'`, with an atomic `notified` flag preventing duplicates.
3. The **main** loop drains it mid-turn (only after a Sleep tool ran) or
   at idle; the queue is agent-scoped: the main thread takes commands
   with `agentId === undefined`, a subagent's loop takes only
   task-notifications addressed to its own agentId (`query.ts:1570-1578`).
4. Separately, each agent run drains its *own* `pendingMessages` inbox
   (filled by SendMessage while busy) into `queued_command` attachments
   at its loop boundary (`getAgentPendingMessageAttachments`,
   `utils/attachments.ts:1085-1100`).

## Concurrency Controls (and Deliberate Absences)

| Control | Scope | Value / mechanism |
| --- | --- | --- |
| QueryGuard | main interactive runs | exactly 1 (queue otherwise) |
| Tool batch cap | sync tools incl. sync subagents | `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY`, default 10 |
| StreamingToolExecutor admission | streamed tool starts | safe-with-safe only; unsafe is exclusive |
| Background agent count | local agents, teammates | **no cap** — every spawn detaches |
| AppState writes | all runs | single store, functional updaters serialize |
| Mailbox files | cross-process teammates | `proper-lockfile` (10 retries, 5-100 ms) |
| Teammate transcript mirror | AppState size | `TEAMMATE_MESSAGES_UI_CAP = 50` messages |

The absence of a background cap is a real hazard the code itself
documents: a whale session spawning 292 agents in 2 minutes reached
36.8 GB RSS (~125 MB per concurrent agent,
`InProcessTeammateTask/types.ts:97-101`); mitigations are message caps and
eviction, not admission control. (Workflow scripts add their own cap —
`min(16, cores-2)` per run — but that is feature-gated tooling above this
layer, `tasks/LocalWorkflowTask`.)

## Headless/SDK Path

`QueryEngine` is "one engine per conversation; each `submitMessage()` is
a turn" (`QueryEngine.ts:175-207`). There is no internal queue or guard —
turn serialization is the caller's job (the generator shape makes
overlapping turns unnatural but not impossible). Multiple engines can
coexist in one process, each with a cloned `FileStateCache`
(`QueryEngine.ts:1259`). The QueryGuard/queue machinery is REPL-only.

## EOS Migration Takeaways

- Model "one main run + N detached runs" explicitly. A three-state guard
  with generation numbers (`idle/dispatching/running`, stale-`finally`
  protection) is small and sufficient; don't build a run scheduler.
- Make new-input handling a three-way decision (queue / steer-interrupt /
  start), and route *all* re-entry — user input, task notifications,
  cross-agent messages — through one prioritized queue with per-agent
  scoping, drained only at loop boundaries.
- Detach background runs as plain promises but **always** behind a task
  registry entry with its own abort root, terminal-state-first
  transitions, and an idempotent `notified` flag.
- Decide promotion semantics up front. Claude Code's restart-on-promote
  (fresh stream, same task identity, cache absorbs replay) is much
  simpler than generator hand-off and worth copying unless mid-run state
  transfer is a hard requirement.
- Teammate-style perpetual runs need two abort levels (lifecycle vs
  current-turn) and an idle/inbox contract from day one.
- Pick an explicit background admission policy (cap or budget). Claude
  Code's "no cap + eviction" produced 36 GB whale sessions; EOS should
  bound concurrent runs at spawn time instead.

## Source Anchors

- QueryGuard state machine: `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/QueryGuard.ts:29-122`
- Concurrent-onQuery re-enqueue: `/Users/yifanxu/machine_learning/LoVC/c c/src/screens/REPL.tsx:2866-2886`
- Submit decision (queue/steer/abort): `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/handlePromptSubmit.ts:313-351`
- Idle drain: `/Users/yifanxu/machine_learning/LoVC/c c/src/hooks/useQueueProcessor.ts:28-68`, `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/queueProcessor.ts:52-87`
- Agent tool concurrency-safe: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/AgentTool.tsx:1273-1275`
- Async decision + detach: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/AgentTool.tsx:555-567,686-764`
- Background pump: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/agentToolUtils.ts:508-686`
- Task registration (async/foreground): `/Users/yifanxu/machine_learning/LoVC/c c/src/tasks/LocalAgentTask/LocalAgentTask.tsx:466-614`
- Promotion race + restart: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/AgentTool.tsx:883-1051`, `/Users/yifanxu/machine_learning/LoVC/c c/src/tasks/LocalAgentTask/LocalAgentTask.tsx:620-652`
- Task notification enqueue: `/Users/yifanxu/machine_learning/LoVC/c c/src/tasks/LocalAgentTask/LocalAgentTask.tsx:197-262`
- Agent-scoped queue drain: `/Users/yifanxu/machine_learning/LoVC/c c/src/query.ts:1570-1578`
- Per-agent inbox drain: `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/attachments.ts:1085-1100`
- Teammate runner loop: `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/swarm/inProcessRunner.ts:883-1552`
- Teammate spawn/kill: `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/swarm/spawnInProcess.ts:104-328`
- Memory hazard note: `/Users/yifanxu/machine_learning/LoVC/c c/src/tasks/InProcessTeammateTask/types.ts:97-121`
- QueryEngine turn model: `/Users/yifanxu/machine_learning/LoVC/c c/src/QueryEngine.ts:175-209`
