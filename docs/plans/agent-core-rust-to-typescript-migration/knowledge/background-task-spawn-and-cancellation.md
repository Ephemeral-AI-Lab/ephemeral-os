# Claude Code Background Task Spawn and Cancellation

Status: Observed
Date: 2026-06-10
Source path: `/Users/yifanxu/machine_learning/LoVC/c c/src`
Migration context: `eos-agent-core/` TypeScript migration reference

Companion docs: `background-task-tracking.md` (registry, output offsets,
notification, eviction), `abort-and-interrupt-handling.md` (abort-reason
semantics), `tool-execution-pipeline.md` (sync vs async tool shapes).

## How Work Detaches

There is no persistent shared shell: every Bash call spawns its own child
process wrapped in `ShellCommandImpl` (`utils/ShellCommand.ts`), whose
output goes through a `TaskOutput` writer. Detachment is a *state
transition*, not a different spawn path:

```
Bash tool call
 ├─ run_in_background: true → spawnShellTask() registers LocalShellTaskState
 │   {status:'running', isBackgrounded:true}; shellCommand.background(taskId);
 │   tool returns immediately with {backgroundTaskId}      BashTool.tsx:989
 ├─ foreground → registerForeground() {isBackgrounded:false}
 │   (a running-but-foreground task is NOT a "background task" —
 │    isBackgroundTask() checks both status and the flag, tasks/types.ts:37)
 │   ├─ Ctrl+B (shown after 2 s) → backgroundTask() promotes it
 │   │     LocalShellTask.tsx:293
 │   ├─ timeout + shouldAutoBackground → onTimeout callback promotes it
 │   │     ShellCommand.ts:135
 │   └─ abort with reason 'interrupt' → abort handler returns WITHOUT
 │         killing; caller backgrounds so partial output survives
 │         ShellCommand.ts:186
 └─ AgentTool run_in_background (or agent def background:true, coordinator,
     forceAsync) → registerAsyncAgent() with a FRESH root AbortController —
     deliberately NOT a child of the parent's, so ESC on the main thread
     never propagates                       AgentTool.tsx:567,694;
                                            LocalAgentTask.tsx:466
```

`ShellCommand.background(taskId)` (`ShellCommand.ts:349`) flips status
`running → backgrounded`, removes the foreground timeout/abort listeners,
spills any in-memory output buffer to disk, and starts a size watchdog.
Both spawn paths register cleanup via `registerCleanup()` so process exit
kills whatever is still alive.

## Kill Mechanics per Task Type

Dispatch is polymorphic: `stopTask(taskId)` (`tasks/stopTask.ts:38`)
validates the task exists and is `running`, then calls
`getTaskByType(type).kill(taskId, setAppState)` (`tasks.ts:36`).

| Type | Kill implementation | Mechanism |
| --- | --- | --- |
| `local_bash` | `killShellTasks.ts:16` | `shellCommand.kill()` → `treeKill(pid, 'SIGKILL')` — immediate, whole process tree, no SIGTERM grace (`ShellCommand.ts:337`). State → `killed`, handle nulled, output evicted. |
| `local_agent` | `LocalAgentTask.tsx:273` | `task.abortController.abort()` (no reason) — stops the in-process `query()` generator; AbortError path extracts partial results. |
| `in_process_teammate` | `InProcessTeammateTask.tsx:24` | graceful `shutdownRequested` or hard abort (see `background-task-tracking.md`). |
| `remote_agent` | RemoteAgentTask | `archiveRemoteSession()` via teleport API; polling stops. |
| `dream` | `DreamTask.ts:136` | abort + rewind consolidation-lock mtime so the next session can retry. |

Important nuance in `ShellCommand.ts`: the constants `SIGKILL = 137` and
`SIGTERM = 143` (`:49`) are **reported exit codes** (128+signal), not the
signal sent. Every kill path sends OS `SIGKILL` via tree-kill; what varies
is the exit code resolved to the awaiting caller:

| Trigger | Exit code | Where |
| --- | --- | --- |
| Manual kill (TaskStop, abort handler) | 137 | `ShellCommand.ts:337-346` |
| Foreground timeout (no auto-background) | 143 (`interrupted: false`, reads as timeout) | `:139` |
| Size watchdog: backgrounded output file > 5 GiB (`MAX_TASK_OUTPUT_BYTES`, `diskOutput.ts:30`), polled every 5 s | 137 + `killedForSize` flag | `:239-258` |

## What an Interrupt Kills (and What Survives)

Esc / Ctrl+C aborts only the **turn's** controller (`useCancelRequest.ts:87`
→ `onCancel()`); a new-message submit aborts with reason `'interrupt'`
(`handlePromptSubmit.ts`). Effect by target:

| Target | Esc (hard abort) | Submit-interrupt (`reason 'interrupt'`) |
| --- | --- | --- |
| In-flight foreground tool | killed (synthetic error result) | killed only if `interruptBehavior() === 'cancel'` |
| Foreground Bash process | tree-killed | NOT killed — backgrounded with partial output (`ShellCommand.ts:186`) |
| Background shell tasks | survive | survive |
| Background agents | survive (fresh root controller) | survive |
| Teammates | survive (independent root) | survive |

Background agents die only through explicit paths:

1. `TaskStop` tool (`TaskStopTool.ts:107`) — accepts `task_id` (or legacy
   `shell_id`), calls `stopTask`; for bash it pre-marks `notified` to
   suppress the noisy exit-code notification, while agent kills keep
   their notification because the AbortError carries a partial result
   (`stopTask.ts:60-95`).
2. The kill-agents chord (`useCancelRequest.ts:225`): two presses within a
   3 s confirm window — first press shows "Press again to stop background
   agents", second calls `killAllRunningAgentTasks`.
3. `registerCleanup()` shutdown hooks at process exit.

Observation tools complete the loop: `TaskOutput` (`TaskOutputTool.tsx:30`)
reads live `TaskOutput` buffers for bash or disk output for agents, with
`block: true` + `timeout` (default 30 s) to wait on completion; `TaskList`
enumerates `appState.tasks`.

## EOS Migration Takeaways

- Make "background" a state transition on a uniformly-spawned process, not
  a separate spawn path — foreground work can then be promoted mid-flight
  (timeout, hotkey, steering interrupt) with output continuity.
- Background-ness is two predicates: terminal-ness comes from `status`,
  detachment from `isBackgrounded`. Keep them separate; a running
  foreground task is not a background task.
- Give detached agents a fresh AbortController root at spawn; wiring it as
  a child of the turn controller is the bug class to design out.
- Kill = polymorphic per-type method dispatched from one `stopTask`
  registry. Shell kill should be tree-kill (children of the child), and
  distinguish *reported exit code* from *signal sent* — callers branch on
  137/143 to classify kill vs timeout.
- Backgrounded output needs a watchdog (size cap + interval poll) because
  nobody is awaiting it; foreground output is bounded by the awaiting tool.
- Suppress kill notifications when the killer is the model itself
  (TaskStop) but keep them when the kill yields a partial result worth
  reporting.

## Source Anchors

- isBackgroundTask predicate: `/Users/yifanxu/machine_learning/LoVC/c c/src/tasks/types.ts:37`
- Bash backgrounding: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/BashTool/BashTool.tsx:905,989`
- Shell task registration/promotion: `/Users/yifanxu/machine_learning/LoVC/c c/src/tasks/LocalShellTask/LocalShellTask.tsx:216,259,293`
- ShellCommand kill/timeout/watchdog/abort: `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/ShellCommand.ts:135,186,239,337,349`
- Output cap: `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/task/diskOutput.ts:30`
- Async agent registration (fresh controller): `/Users/yifanxu/machine_learning/LoVC/c c/src/tasks/LocalAgentTask/LocalAgentTask.tsx:466`
- Agent kill: `/Users/yifanxu/machine_learning/LoVC/c c/src/tasks/LocalAgentTask/LocalAgentTask.tsx:273`
- stopTask dispatch: `/Users/yifanxu/machine_learning/LoVC/c c/src/tasks/stopTask.ts:38`
- TaskStop tool: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools/TaskStopTool/TaskStopTool.ts:107`
- Esc handling + kill-agents chord: `/Users/yifanxu/machine_learning/LoVC/c c/src/hooks/useCancelRequest.ts:87,225`
