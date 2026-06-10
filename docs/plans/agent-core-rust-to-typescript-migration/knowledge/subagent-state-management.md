# Claude Code Subagent State Management (One Parent, Many Subagents)

Status: Observed
Date: 2026-06-11
Source path: `/Users/yifanxu/machine_learning/LoVC/c c/src`
Migration context: `eos-agent-core/` TypeScript migration reference

Companion docs: `agent-run-concurrency.md` (how runs start/multiply),
`background-task-tracking.md` (task data model + eviction),
`abort-and-interrupt-handling.md` (controller tree),
`message-steering.md` (inbox drain boundaries).

## The Model: One Store, Forked Contexts, Sidechained History

Claude Code does not give each subagent its own state store. There is
**one global AppState**; isolation comes from three mechanisms applied at
spawn time:

```
parent ToolUseContext
        │  createSubagentContext()        forkedAgent.ts:345
        ▼
child ToolUseContext
  ├─ cloned collections      (readFileState, contentReplacementState, …)
  ├─ no-op'd callbacks       (setAppState, setToolJSX, UI hooks)
  ├─ always-shared channel   (setAppStateForTasks → root store)
  └─ wrapped getter          (agentGetAppState → per-agent VIEW of state)
        │
        ▼
runAgent() → its own query() loop, own messages[], own sidechain JSONL
```

## Identity

- `AgentId` is a branded string (`types/ids.ts`), minted per spawn by
  `createAgentId()` (`runAgent.ts:347`); teammates use deterministic
  `name@team` ids (`spawnInProcess.ts:112`). The id keys every per-agent
  slice: task entry, todos, transcript file, dump/skill state.
- `queryTracking` gives each subagent a fresh `chainId` and
  `depth = parent.depth + 1` (`forkedAgent.ts:451-455`) — the only
  explicit nesting record.
- For analytics/workload attribution the run executes inside
  `runWithAgentContext` (AsyncLocalStorage), and teammates additionally
  inside `runWithTeammateContext` (`utils/teammateContext.ts:59`) so
  concurrent runs can't cross-attribute.

## The Context Fork: `createSubagentContext`

`forkedAgent.ts:345-462` is the single seam where shared-vs-isolated is
decided, field by field:

| Field | Sync subagent | Async subagent / teammate | Why |
| --- | --- | --- | --- |
| `abortController` | parent's own (`runAgent.ts:524-528`) | new unlinked root (background) or explicit override (teammate/task controller) | ESC must kill sync children, not background work |
| `getAppState` | wrapped per-agent view (below) | same + forced `shouldAvoidPermissionPrompts` | permission posture differs per agent |
| `setAppState` | shared (`shareSetAppState: !isAsync`, `runAgent.ts:709`) | **no-op** | async children must not mutate session UI state |
| `setAppStateForTasks` | parent's root channel, always | same | task register/kill must reach the root store even when `setAppState` is stubbed (zombie prevention) |
| `readFileState` | fresh size-limited cache; fork path clones parent's (`runAgent.ts:375-378`) | same | freshness checks are per-agent; forks need parent's view for cache-stable reads |
| `contentReplacementState` | clone of parent's | clone / reconstructed on resume | replacement decisions must match parent's for prompt-cache-identical prefixes |
| `localDenialTracking` | parent's | fresh | denial counters must accumulate somewhere real when setAppState is a no-op |
| `messages` | fresh `initialMessages` (prompt ± forked context) | same | conversation isolation |
| UI callbacks (`setToolJSX`, `addNotification`, …) | undefined | undefined | subagents can't drive the parent terminal |
| `setResponseLength` / `pushApiMetricsEntry` | shared | shared | child tokens/TTFT feed the parent's spinner metrics (`runAgent.ts:710,766`) |
| `options` (tools, model, mcpClients, …) | rebuilt per agent (`runAgent.ts:667-695`) | same | own tool pool, own model, thinking disabled (except fork path) |

`runAgent` itself adds: own MCP connections (additive, cleaned up after,
`runAgent.ts:95-218`), own session-hook registration keyed by agentId
(`runAgent.ts:567-575`), preloaded frontmatter skills, and per-agent
transcript routing.

## One AppState, Per-Agent Views and Slices

The wrapped getter `agentGetAppState` (`runAgent.ts:416-498`) returns the
*shared* state transformed for this agent on every read — no copy is
stored:

- permission `mode` overridden by the agent definition unless the parent
  is `bypassPermissions`/`acceptEdits` (those always win);
- `shouldAvoidPermissionPrompts: true` when the agent can't show UI
  (async default; `bubble` mode and teammates opt back in);
- `awaitAutomatedChecksBeforeDialog: true` for background agents that CAN
  prompt (classifier/hooks resolve first, user interrupted only as last
  resort);
- `allowedTools` replaces session allow-rules wholesale (parent approvals
  don't leak; SDK `--allowedTools` cliArg rules preserved,
  `runAgent.ts:469-479`);
- per-agent `effortValue` override.

Slices of the shared store keyed by agent/task id:

| AppState field | Keyed by | Lifecycle |
| --- | --- | --- |
| `tasks` | taskId (== agentId for agents) | registered at spawn; terminal status → grace period → eviction |
| `todos` | agentId | deleted in `runAgent` finally (`runAgent.ts:839-843`) — orphan-leak prevention |
| `agentNameRegistry` (`Map<name, AgentId>`) | spawn `name` param | SendMessage routing |
| `teamContext.teammates` | teammateId | removed on kill (`spawnInProcess.ts:268-275`) |
| `foregroundedTaskId` / `viewingAgentTaskId` | — | which agent the UI is focused on / viewing |
| task `messages` (teammate/retained agents) | per task | capped at 50 for teammates; populated only while UI retains |

## Conversation State: Sidechains, Resume, Fork

Each subagent's history lives in its own file, not the session transcript:

- `subagents/agent-<agentId>.jsonl` under the session dir
  (`sessionStorage.ts:247-258`), written incrementally with a
  `lastRecordedUuid` parent-chain so each append links correctly
  (`runAgent.ts:744-805`); workflow children group under
  `subagents/workflows/<runId>/` via `setAgentTranscriptSubdir`.
- A sidecar `agent-<agentId>.meta.json` stores
  `{agentType, worktreePath?, description?}` (`sessionStorage.ts:260-303`)
  so resume can route to the right definition, restore cwd, and label
  notifications.
- **Resume** (`resumeAgent.ts:63-105`): reload transcript + metadata,
  filter unresolved tool_uses / orphaned thinking / whitespace-only
  assistant messages, and reconstruct `contentReplacementState` from the
  recorded replacements so the resumed run makes byte-identical
  replacement decisions (prompt cache stability). SendMessage to a
  *stopped* agent auto-resumes it this way (`SendMessageTool.ts:824-836`).
- **Fork path** (`forkSubagent.ts`): the cache-sharing variant — child
  inherits the parent's exact system prompt, tool array, thinking config,
  and full message history (`forkContextMessages` filtered through
  `filterIncompleteToolCalls`, `runAgent.ts:866-904`), with placeholder
  tool_results + a per-child directive appended. Everything before the
  directive is byte-identical across siblings → shared prompt cache.

Parent-visible result state is separate from history: the sync path
collects yielded messages into `agentMessages` and reduces them via
`finalizeAgentTool`; the async path stores the result on the task entry
(`task.result`) and emits the `<task-notification>`.

## Abort Topology with Multiple Subagents

```
session root controller
 └─ turn controller (per main-loop turn)
     ├─ sync subagent        — same controller (ESC kills it)
     │    └─ its tool batch  — child controllers per tool
     ├─ background agent     — NEW root (ESC survives; killed via
     │                         task.abortController / TaskStop / exit cleanup)
     └─ teammate             — lifecycle root (kill teammate)
          └─ currentWorkAbortController (abort one turn, teammate idles)
```

Subagents spawned *by* a teammate/agent pass `parentAbortController` so
`createChildAbortController` chains them — killing the teammate kills its
children (`LocalAgentTask.tsx:486`). Every detached run also registers a
process-exit cleanup (`registerCleanup → killAsyncAgent`) and `runAgent`'s
finally kills any background shell/monitor tasks the agent left behind,
keyed by its agentId (`runAgent.ts:847-857`).

## Cross-Agent Messaging State

Three inbox shapes, all drained at the *receiver's* loop boundary (never
mid-stream):

| Channel | Storage | Filled by | Drained by |
| --- | --- | --- | --- |
| `task.pendingMessages` (local agent) | AppState task entry | SendMessage to a busy/running agent (`SendMessageTool.ts:810-814`) | the agent's own query loop → `queued_command` attachments (`attachments.ts:1085-1100`) |
| `task.pendingUserMessages` (teammate) | AppState task entry | transcript-view typing (`InProcessTeammateTask.tsx:68`) | `waitForNextPromptOrShutdown` poll (`inProcessRunner.ts:705-739`) |
| File mailbox | `~/.claude/teams/<team>/inboxes/<name>.json` + lockfile | SendMessage across processes (`teammateMailbox.ts`) | same poll loop, 500 ms; lead prioritized over peers |
| (to parent) `<task-notification>` | global command queue, priority `later` | completion/failure/kill of any tracked run | main loop mid-turn (post-Sleep) or idle drain |

Coordination state for "wait until my teammates finish" is
`task.onIdleCallbacks` + `isIdle` on the teammate task
(`utils/teammate.ts:238-292`) — a promise resolved when every working
teammate flips idle.

## Lifecycle Cleanup Ledger

Spawning hundreds of subagents stays bounded because `runAgent`'s
`finally` (`runAgent.ts:816-859`) reverses every registration:

agent MCP servers disconnected → session hooks cleared (by agentId) →
prompt-cache tracking dropped → `readFileState.clear()` →
`initialMessages.length = 0` (release fork clone) → perfetto unregister →
transcript-subdir mapping cleared → `todos[agentId]` deleted → agent's
background shell/monitor tasks killed. The tool layer adds
`clearInvokedSkillsForAgent` + `clearDumpState`
(`AgentTool.tsx:1186-1193`), and the task framework evicts terminal task
entries after notification + grace period.

## Agent Memory (Cross-Invocation State)

Persistent memory is keyed by **agentType, not AgentId**
(`agentMemory.ts:52-65`): `~/.claude/agent-memory/<agentType>/MEMORY.md`
(user), `.claude/agent-memory/<agentType>/` (project) or
`agent-memory-local` (machine). All concurrent instances of one type
share it; nothing serializes their writes — last write wins. Snapshots
(`agentMemorySnapshot.ts`) support seeding/replacing memory dirs with
sync-state tracked in `.snapshot-synced.json`.

## EOS Migration Takeaways

- Put shared-vs-isolated decisions in **one context-fork function** with
  explicit per-field defaults (clone / no-op / share), not scattered at
  call sites. Claude Code's whole isolation story is ~120 lines in
  `createSubagentContext` plus a wrapped state getter.
- Prefer **views over copies** for global state: a per-agent
  `getAppState` wrapper that overlays permission mode/effort on read
  avoids divergence and needs no sync-back protocol.
- Keep a *separate always-on channel to the root store* for task
  registration/kill, distinct from the general state setter you stub out
  for async children — otherwise detached children leak zombies.
- Per-agent history = own append-only sidechain + metadata sidecar, with
  parent-uuid chaining and replacement-state recording; that combination
  is what makes resume and prompt-cache-stable forks possible.
- Key cleanup by agentId and make spawn paths register their teardown at
  spawn time (exit hooks, todos, child tasks, hooks, MCP); rely on a
  symmetric `finally`, not GC.
- Treat per-type shared memory as a known race surface (fine for advisory
  memory, wrong for anything correctness-bearing); EOS should either
  lock-or-merge writes or key memory by instance.

## Source Anchors

- Context fork: `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/forkedAgent.ts:345-462`
- Subagent spawn core: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/runAgent.ts:248-860`
- Per-agent AppState view: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/runAgent.ts:416-498`
- Abort selection: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/runAgent.ts:524-528`; child chaining `/Users/yifanxu/machine_learning/LoVC/c c/src/tasks/LocalAgentTask/LocalAgentTask.tsx:486`
- File state cache clone/fresh: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/runAgent.ts:375-378`, `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/fileStateCache.ts:122-142`
- Sidechain transcripts + metadata: `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/sessionStorage.ts:247-303,1451-1462`; incremental chain `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/runAgent.ts:732-805`
- Resume: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/resumeAgent.ts:63-105`
- Fork (cache-identical context): `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/forkSubagent.ts:60-169`, `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/AgentTool.tsx:483-541,622-633`
- Incomplete-tool-call filter: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/runAgent.ts:866-904`
- Cleanup ledger: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/runAgent.ts:816-859`, `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/AgentTool.tsx:1150-1202`
- Inboxes: `/Users/yifanxu/machine_learning/LoVC/c c/src/tasks/LocalAgentTask/LocalAgentTask.tsx:162-192`, `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/attachments.ts:1085-1100`, `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/swarm/inProcessRunner.ts:689-868`
- SendMessage routing/auto-resume: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/SendMessageTool/SendMessageTool.ts:802-880`
- Idle coordination: `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/teammate.ts:238-292`
- Agent memory: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/agentMemory.ts:52-177`, `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/AgentTool/agentMemorySnapshot.ts:31-197`
- Teammate identity/ALS: `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/teammateContext.ts:22-64`, `/Users/yifanxu/machine_learning/LoVC/c c/src/tasks/InProcessTeammateTask/types.ts:13-76`
