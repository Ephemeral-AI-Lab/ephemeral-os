# Claude Code Task-List Tools and Task Notification Policy

Status: Observed
Date: 2026-06-11
Source path: `/Users/yifanxu/machine_learning/LoVC/c c/src`
Migration context: `eos-agent-core/` TypeScript migration reference

Scope: the shared task-list system (`TaskCreate`/`TaskGet`/`TaskList`/
`TaskUpdate`) and every mechanism that notifies or reminds an agent about
tasks. The *other* "task" family — running background work (shells, agents,
remote sessions) managed by `TaskOutput`/`TaskStop` — is covered in
`background-task-tracking.md` and `background-task-spawn-and-cancellation.md`;
it appears here only where its notification path is relevant.

## 1. Task Tool Inventory

Two distinct families share the "Task" prefix:

| Tool | Family | Purpose | Key behavior |
| --- | --- | --- | --- |
| `TaskCreate` (`tools/TaskCreateTool/TaskCreateTool.ts:48`) | task list | Create a `pending` task | Runs `TaskCreated` hooks; a blocking hook deletes the just-created task and fails the call (`:110-113`). Auto-expands the tasks UI panel (`:116-119`). |
| `TaskGet` (`tools/TaskGetTool`) | task list | Full task detail by ID | Prompt tells the model to verify `blockedBy` is empty before starting work. |
| `TaskList` (`tools/TaskListTool`) | task list | Summary of all tasks | Prompt encodes the teammate workflow: find `pending` + unowned + unblocked, prefer lowest ID, claim via `TaskUpdate(owner)`. |
| `TaskUpdate` (`tools/TaskUpdateTool/TaskUpdateTool.ts:88`) | task list | Mutate subject/description/status/owner/deps/metadata; `status: 'deleted'` removes the file | Runs `TaskCompleted` hooks (can veto completion, `:232-265`); writes a mailbox notification on owner change (`:277-298`); appends nudges to the tool result (§5). |
| `TodoWrite` (`tools/TodoWriteTool`) | task list (V1) | Legacy whole-list replace | Active only when `isTodoV2Enabled()` is false — non-interactive sessions without `CLAUDE_CODE_ENABLE_TASKS` (`utils/tasks.ts:133-139`). |
| `TaskOutput` (`tools/TaskOutputTool/TaskOutputTool.tsx`) | background tasks | Read output of a running/finished background task | Deprecated — prompt says "prefer `Read` on the task's output file path". Aliases `AgentOutputTool`, `BashOutputTool`. |
| `TaskStop` (`tools/TaskStopTool`) | background tasks | Kill a running background task by ID | — |

All four task-list tools are `isConcurrencySafe() => true` and gated on
`isTodoV2Enabled()`.

### Storage model (`utils/tasks.ts`)

- Schema (`:76-89`): `{ id, subject, description, activeForm?, owner?,
  status: 'pending'|'in_progress'|'completed', blocks: string[],
  blockedBy: string[], metadata? }`. IDs are incrementing integers as strings.
- One JSON file per task at
  `~/.claude/tasks/<sanitized-taskListId>/<id>.json` (`:221-231`), plus a
  `.highwatermark` file so deleted/reset task IDs are never reused
  (`:91-131`) and a `.lock` file for `proper-lockfile` list-level locking
  with retry backoff sized for ~10 concurrent swarm agents (~2.6 s budget,
  `:102-108`).
- Task list ID resolution (`getTaskListId`, `:199-210`), in priority order:
  `CLAUDE_CODE_TASK_LIST_ID` env → in-process teammate's team name →
  `CLAUDE_CODE_TEAM_NAME` env (process teammates) → leader team name set by
  `TeamCreate` → session ID. So a team shares one list; a standalone session
  gets a per-session list.
- `claimTask` (`:541-612`) is the atomic ownership primitive: fails with
  `task_not_found` / `already_claimed` / `already_resolved` / `blocked`
  (any non-completed task in `blockedBy` blocks), and optionally
  `agent_busy` under a list-level lock when `checkAgentBusy` is set
  (`:618-692`).
- Agent idle/busy status is *derived*, not stored: an agent is `busy` iff it
  owns at least one non-completed task (`getAgentStatuses`, `:763-798`).
- Every mutation fires an in-process `tasksUpdated` signal (`:18`, `:61-67`)
  for immediate UI refresh; cross-process visibility is via the shared files.

## 2. What Fires When an Agent Creates a Task

```
TaskCreate.call                          (TaskCreateTool.ts:80-129)
  ├─ createTask()                        file write under list lock
  │    └─ notifyTasksUpdated()           in-process UI signal only
  ├─ executeTaskCreatedHooks()           hooks.ts:3745-3773
  │    hook_event_name: 'TaskCreated'
  │    payload: task_id, task_subject, task_description,
  │             teammate_name, team_name
  │    └─ blocking error (exit code 2) → deleteTask() + tool error
  │                                      (TaskCreateTool.ts:110-113)
  ├─ setAppState(expandedView:'tasks')   auto-expand task panel in UI
  └─ tool_result: "Task #N created successfully: <subject>"
```

The load-bearing negative observation: **creating a task pushes nothing to
other agents.** There is no broadcast, no mailbox write, no teammate wake-up
on `TaskCreate`. Discovery is pull-based (§4). The only push happens on
explicit *assignment*: when `TaskUpdate` changes `owner` (and swarms are
enabled), a structured JSON message

```json
{"type": "task_assignment", "taskId", "subject", "description",
 "assignedBy", "timestamp"}
```

is written to the new owner's inbox file
`~/.claude/teams/<team>/inboxes/<agent>.json` (`TaskUpdateTool.ts:277-298`,
`utils/teammateMailbox.ts:57-67`). The recipient picks it up either through
its idle mailbox poll (§4.1) or, for the leader, as per-turn
`teammate_mailbox` attachments (`utils/attachments.ts:3590-3669`).

Symmetric lifecycle hooks: `TaskCompleted` runs before a task flips to
`completed` and can veto the transition (`TaskUpdateTool.ts:232-265`,
`hooks.ts:3789+`). When a teammate dies or shuts down,
`unassignTeammateTasks` (`utils/tasks.ts:818-860`) resets its open tasks to
`pending`/unowned and builds a leader notification: "N task(s) were
unassigned: … Use TaskList to check availability and TaskUpdate with owner to
reassign them to idle teammates."

## 3. The Stale-Task Reminder Policy (turn-counter nag)

The "you have tasks, gear toward them" reminder is an *attachment* generated
fresh each turn by the attachment pipeline (`getAttachments` →
`maybe('todo_reminders', …)`, `utils/attachments.ts:893-897`; V2 path
`getTaskReminderAttachments`, `:3375-3432`).

Firing condition — both counters must reach the threshold:

| Knob | Value | Meaning |
| --- | --- | --- |
| `TURNS_SINCE_WRITE` | 10 | Non-thinking assistant turns since the last `TaskCreate`/`TaskUpdate` `tool_use` block |
| `TURNS_BETWEEN_REMINDERS` | 10 | Assistant turns since the last `task_reminder` attachment |

Both counts come from a single backwards scan of the message history
(`getTaskReminderTurnCounts`, `:3319-3373`) — there are no timers and no
persisted reminder state; dedupe falls out of the prior reminder being
visible in the transcript. Suppression gates, checked before counting:

- Todo V2 disabled → falls back to the V1 `todo_reminder` (same thresholds,
  `:3293-3317`).
- `USER_TYPE === 'ant'` → never fires (`:3384-3386`).
- `BriefTool` present in the toolset → never fires; when SendUserMessage is
  the primary channel the TaskUpdate nag conflicts with the brief workflow
  (#20467, `:3392-3397`).
- `TaskUpdate` absent from the toolset → never fires (`:3399-3406`) — never
  nag about a tool the model cannot call.

When it fires, the attachment carries the *entire current task list* and
renders (`utils/messages.ts:3680-3699`) as an `isMeta` user message wrapped
in `<system-reminder>`:

> "The task tools haven't been used recently. If you're working on tasks that
> would benefit from tracking progress, consider using TaskCreate to add new
> tasks and TaskUpdate to update task status (set to in_progress when
> starting, completed when done). Also consider cleaning up the task list if
> it has become stale. Only use these if relevant to the current work. This
> is just a gentle reminder - ignore if not applicable. Make sure that you
> NEVER mention this reminder to the user"
> followed by `#<id>. [<status>] <subject>` lines.

## 4. Pull-Based "Gear Toward Tasks" Mechanisms

Because creation is silent, three pollers turn pending tasks into work:

### 4.1 Teammate idle loop (in-process swarm)

`waitForNextPromptOrShutdown` (`utils/swarm/inProcessRunner.ts:689-868`)
keeps an idle teammate alive on a 500 ms poll. Per iteration, strict
priority order:

1. In-memory pending user messages (transcript-view injection).
2. Shutdown requests anywhere in the unread mailbox (anti-starvation scan).
3. Unread message from the team lead (leader represents user intent).
4. First unread peer message (FIFO).
5. **`tryClaimNextTask`** (`:624-657`): `findAvailableTask` picks the first
   task that is `pending`, unowned, and has every `blockedBy` resolved;
   claims it, immediately sets `in_progress`, and feeds the model a prompt
   from pseudo-sender `task-list`:
   `"Complete all open tasks. Start with task #N:\n\n<subject>\n\n<description>"`.

A teammate also attempts one claim immediately at spawn so the UI shows
activity from the start (`:1016-1019`).

### 4.2 Tasks-mode watcher (single session, externally-fed list)

`useTaskListWatcher` (`hooks/useTaskListWatcher.ts`) enables "tasks mode":
`fs.watch` on the task directory with a 1 s debounce. When the session is
idle (`!isLoading`) and has no current task, it claims the next available
task (same `findAvailableTask` predicate), submits it as a prompt, and
releases the claim if submission is rejected. Default list ID `'tasklist'`
(`utils/tasks.ts:862`).

### 4.3 Idle notification to the leader

When a teammate goes idle (Stop), it writes a structured
`idle_notification` to the leader's mailbox
(`utils/teammateMailbox.ts:391-447`; sender
`inProcessRunner.ts:566-581`): `{ type: 'idle_notification', from,
idleReason: 'available'|'interrupted'|'failed', summary?, completedTaskId?,
completedStatus?, failureReason? }`. The leader's attachment generation
collapses duplicates, keeping only the latest idle notification per agent
(`utils/attachments.ts:3644-3665`). This is the leader-side trigger to
reassign tasks to idle teammates.

### 4.4 Tool-result nudges (zero extra turns)

`TaskUpdate` piggybacks reminders on its own tool result
(`TaskUpdateTool.ts:364-405`):

- Teammate completes a task → appends: "Task completed. Call TaskList now to
  find your next available task or see if your work unblocked others."
- Main agent closes the last of a 3+ task list with no task matching
  `/verif/i` → appends a verification nudge to spawn the verification agent
  (feature-gated, `:333-349`).

## 5. Contrast: Background-Task Notification Path

For the `TaskOutput`/`TaskStop` family the policy is push, not pull: a 1 s
poll (`utils/task/framework.ts`, `POLL_INTERVAL_MS`) plus per-turn
`unified_tasks` attachments (`utils/attachments.ts:961-963`) emit
`task_status` attachments with output deltas, and each task type enqueues its
own completion message via
`enqueuePendingNotification({ mode: 'task-notification' })` — a
`<task-notification>` XML block (task id, type, output file path, status,
summary) that re-enters the query loop as a queued command and can wake an
idle session (`INLINE_NOTIFICATION_MODES`, `attachments.ts:1044-1053`).
Details in `background-task-tracking.md`.

## 6. EOS Migration Takeaways

- Keep the two "task" concepts separate in `eos-agent-core`: a shared
  work-item list (pull-based, file/db-backed, multi-agent) vs. running
  background processes (push notifications). Claude Code's naming overlap is
  a source of confusion worth avoiding.
- Creation is intentionally silent; assignment is the only push. Idle agents
  discover work by polling the list with a claim primitive that is atomic
  under a lock and encodes `blocked`/`already_claimed`/`agent_busy` reasons.
  This maps cleanly onto an EOS notification-rule: trigger on *assignment*
  and *idle*, not on *create*.
- The stale-task reminder needs no scheduler state: two turn counters derived
  from a backwards transcript scan (10 turns since last task-tool use, 10
  since last reminder), with availability gates (don't nag about absent
  tools; don't nag when a competing primary channel like Brief exists). The
  reminder text embeds the full list so the model can act without a
  follow-up read.
- Lifecycle hooks (`TaskCreated`, `TaskCompleted`) are veto points: a
  blocking hook rolls back the mutation (create → delete; complete →
  stay in_progress). EOS's notification trigger engine can adopt the same
  pre-commit gate shape.
- Crash-safety conventions worth porting: high-water mark for ID
  non-reuse, owner auto-set when a teammate marks `in_progress`, and
  unassign-on-death with an explicit reassignment notification to the
  coordinator.
