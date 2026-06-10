# Claude Code Tool Execution Pipeline and Batch Scheduling

Status: Observed
Date: 2026-06-10
Source path: `/Users/yifanxu/machine_learning/LoVC/c c/src`
Migration context: `eos-agent-core/` TypeScript migration reference

Companion docs: `tool-definition-and-registry.md` (the contract being
executed), `tool-hooks.md` (PreToolUse/PostToolUse internals),
`abort-and-interrupt-handling.md` (cancellation tree).

## Two Executors, One Pipeline

The query loop collects `tool_use` blocks while streaming and runs them via
one of two schedulers (`query.ts:1380`):

| | Batch executor (`runTools`) | `StreamingToolExecutor` |
| --- | --- | --- |
| Gate | default | Statsig gate `streamingToolExecution` (`query/config.ts:33`) |
| Start time | after the full model response | as each `tool_use` block finishes streaming (`query.ts:841`) |
| Grouping | pre-partitioned batches | dynamic: a tool starts when current executors allow |
| Result order | batch order | buffered, yielded in arrival order |
| Sibling cancel | none | Bash error aborts in-flight siblings |

Both funnel every tool through the same per-tool pipeline
(`runToolUse`, `toolExecution.ts:337`), so semantics differ only in
scheduling and cancellation.

## Batch Partitioning (runTools)

`partitionToolCalls` (`toolOrchestration.ts:91`) folds the tool_use list
into runs: consecutive concurrency-safe calls merge into one parallel batch;
every unsafe call is its own serial batch. Safety = Zod parse succeeds AND
`tool.isConcurrencySafe(parsed)` returns true (throws → unsafe,
parse failure → unsafe — fail-closed).

```
[Read, Grep, Edit, Read, Read]
  → batch1: [Read, Grep]   parallel, cap = CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY (default 10)
  → batch2: [Edit]         serial
  → batch3: [Read, Read]   parallel
```

Parallelism is generator interleaving: each tool is an async generator and
`all(generators, cap)` (`utils/generators.ts:32`) races `.next()` promises,
yielding whichever produces first — single-threaded concurrency over awaited
I/O, no workers.

`contextModifier` handling differs by lane (`toolOrchestration.ts:31-79`):
serial batches fold modifiers into the context immediately (next tool sees
it); parallel batches queue modifiers per toolUseID and apply them only
after the whole batch, in block order — so parallel tools never observe each
other's context edits.

## Streaming Executor

`StreamingToolExecutor` (`StreamingToolExecutor.ts:40`) keeps a
`TrackedTool[]` with status `queued → executing → completed → yielded`.

- Admission (`canExecuteTool`, `:129`): a tool starts when nothing is
  executing, or when it and everything executing is concurrency-safe. A
  blocked unsafe tool also blocks everything queued behind it (order
  preservation, `:148`).
- Results are buffered per tool and yielded in queue order; progress
  messages bypass buffering and surface immediately (`:367`,
  `getCompletedResults` `:412`).
- Each tool gets a child AbortController under a shared
  `siblingAbortController` (`:301`). Only a **Bash** error trips the sibling
  cascade (`:359`) — bash commands have implicit dependency chains; Read/
  WebFetch failures stay independent. Cancelled siblings get synthetic
  `is_error` tool_results ("Cancelled: parallel tool call X errored").
- Permission-dialog rejection aborts the per-tool controller and explicitly
  bubbles UP to the query controller (`:304`) so the turn actually ends —
  sibling_error reasons are filtered out of the bubble-up.
- `discard()` (`:69`) abandons everything on streaming model-fallback;
  queued tools never start and the query loop builds a fresh executor.
- Steering interrupts respect the tool's `interruptBehavior()`: reason
  `'interrupt'` cancels only `'cancel'` tools (`getAbortReason`, `:210`).

## The Per-Tool Pipeline

`runToolUse` → `checkPermissionsAndCallTool` (`toolExecution.ts:599`) runs
these stages in order; every early exit still emits a `tool_result` (error)
so the conversation stays API-valid:

```
 1. resolve tool by name (alias fallback for renamed tools)   :345
    └─ unknown → is_error "No such tool available"
 2. abort signal already set? → CANCEL_MESSAGE tool_result     :415
 3. Zod inputSchema.safeParse                                  :615
    └─ failure → InputValidationError (+ "schema not sent" hint
       directing the model to ToolSearch for deferred tools    :578)
 4. tool.validateInput(parsed, ctx)  — tool-specific semantic
    validation, informs the model, no UI                       :683
 5. speculative Bash permission-classifier kicked off early
    (parallel with hooks)                                      :746
 6. backfillObservableInput on a CLONE; original input kept
    for call() so transcript/cache bytes stay stable           :783
 7. PreToolUse hooks (stream of typed events: message /
    hookPermissionResult / hookUpdatedInput / preventContinuation /
    stopReason / additionalContext / stop)                     :800
    └─ 'stop' → tool_result stop message, never executes
 8. resolveHookPermissionDecision → canUseTool                 :921
    └─ behavior !== 'allow' → is_error result (+ PermissionDenied
       hooks may grant a retry hint, :1081)
    └─ allow may carry updatedInput → replaces input for call  :1130
 9. tool.call(input, {…ctx, toolUseId, userModified}, canUseTool,
    assistantMessage, onProgress)                              :1207
10. result mapping: mapToolResultToToolResultBlockParam, then
    processToolResultBlock persists oversized output to disk
    (<persisted-output> preview, threshold maxResultSizeChars) :1292,1409
11. PostToolUse hooks                                          :1483
    └─ non-MCP: result emitted BEFORE hooks; hooks append context
    └─ MCP: hooks may REPLACE output (updatedMCPToolOutput);
       result emitted AFTER hooks                              :1540
12. result.newMessages appended                                :1566
13. preventContinuation → 'hook_stopped_continuation' attachment;
    query loop converts it to terminal {reason:'hook_stopped'}  :1572, query.ts:1519
err. catch: AbortError → no error log; PostToolUseFailure hooks
     run with isInterrupt flag; is_error tool_result            :1589-1737
```

Progress plumbing: `call()`'s `onProgress` callback is bridged into the
async result stream via a `Stream<MessageUpdateLazy>`
(`streamedCheckPermissionsAndCallTool`, `:492`) — progress messages are
`progress`-typed conversation messages keyed by toolUseID, rendered live
but never sent to the API.

Timing metadata recorded around the pipeline: per-phase durations
(`pre_tool_hook_duration_ms`, slow-phase logs ≥2 s, hook timing summaries
>500 ms, `:863-891`), OTel spans `startToolSpan` /
`startToolBlockedOnUserSpan` / `startToolExecutionSpan` separating
user-wait from execution time (`:909-914`, `:1176`), and the
`toolDecisions` map entry consumed and deleted in `finally` (`:1741`).

## Synchronous vs Asynchronous Tools

Every tool's `call()` is async, but the **turn** distinguishes two shapes:

| Shape | Behavior | Examples |
| --- | --- | --- |
| Synchronous (turn-blocking) | `call()` resolves with the final result; the loop holds the turn open until the batch finishes | Read, Edit, Grep, foreground Bash, MCP tools |
| Asynchronous (task-spawning) | `call()` returns quickly with a task id; real work detaches into a background task with an INDEPENDENT abort root, registered via `setAppStateForTasks`; completion re-enters the loop as a queued `task-notification` | Bash `run_in_background`, AgentTool `run_in_background`, teammates, workflows |

The async shape is not a tool-contract flag — it is a convention: the tool
registers a `TaskState` (see `background-task-tracking.md`) and returns
`{data: {taskId, …}}` immediately. Mid-flight promotion also exists:
foreground Bash with reason-`'interrupt'` aborts is left running and
converted to a background task instead of being killed
(`ShellCommand` abort handler — see `abort-and-interrupt-handling.md`).
`TaskOutput`/`TaskStop` then poll or kill by task id.

Long synchronous tools stay interruptible through their context's abort
signal; long asynchronous work is interruptible only through the explicit
task-kill path, by design (user interrupts must not nuke background work).

## EOS Migration Takeaways

- Run one per-tool pipeline with pluggable scheduling on top. The pipeline
  stage order (parse → validate → pre-hooks → permission → call →
  persist/map → post-hooks) is the portable core; both schedulers reuse it
  unchanged.
- Partition by `isConcurrencySafe(input)` with fail-closed parsing, cap
  parallelism (~10), and keep unsafe tools strictly ordered. Generator
  racing (`all()`) gives bounded concurrency without threads.
- Decide a context-mutation rule per lane up front: serial = fold
  immediately, parallel = queue and apply after the batch. Don't let
  parallel tools see each other's context edits.
- Every exit path must synthesize a `tool_result` — unknown tool, parse
  error, validation error, permission deny, hook stop, abort, exception.
  Treat it as an invariant of the executor, not each tool's job.
- Scope error cascades narrowly: only shell-like tools with implicit
  dependency chains should cancel siblings; child→parent abort escalation
  must be explicit and reason-filtered.
- Persist oversized results to disk at the executor layer keyed on a
  per-tool threshold; hooks that can rewrite output (MCP) force
  result-emission ordering to differ — design the hook point so output
  replacement happens before serialization.

## Source Anchors

- Executor selection: `/Users/yifanxu/machine_learning/LoVC/c c/src/query.ts:1366-1408`
- Streaming add/collect during model stream: `/Users/yifanxu/machine_learning/LoVC/c c/src/query.ts:837-862`
- Batch partitioning: `/Users/yifanxu/machine_learning/LoVC/c c/src/services/tools/toolOrchestration.ts:91`
- Concurrency cap: `/Users/yifanxu/machine_learning/LoVC/c c/src/services/tools/toolOrchestration.ts:8`
- Generator racing: `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/generators.ts:32`
- StreamingToolExecutor: `/Users/yifanxu/machine_learning/LoVC/c c/src/services/tools/StreamingToolExecutor.ts:40`
- Sibling cascade (Bash-only): `/Users/yifanxu/machine_learning/LoVC/c c/src/services/tools/StreamingToolExecutor.ts:354-364`
- Per-tool pipeline: `/Users/yifanxu/machine_learning/LoVC/c c/src/services/tools/toolExecution.ts:599`
- Deferred-tool schema hint: `/Users/yifanxu/machine_learning/LoVC/c c/src/services/tools/toolExecution.ts:578`
- Input clone/backfill dance: `/Users/yifanxu/machine_learning/LoVC/c c/src/services/tools/toolExecution.ts:776-793,1183-1205`
- MCP result-after-hooks ordering: `/Users/yifanxu/machine_learning/LoVC/c c/src/services/tools/toolExecution.ts:1477,1540`
- hook_stopped terminal: `/Users/yifanxu/machine_learning/LoVC/c c/src/query.ts:1384-1394,1519`
