# eos-coding-agent — Host Application Specification

- **Status:** Draft for review
- **Date:** 2026-06-13
- **Depends on:** `eos-agent-core_SPEC.md` (the SDK). This project imports **only** the
  SDK's root package — never its internal packages.
- **Scope:** The product/host that composes the SDK into a coding agent: profiles, every
  tool, the workflow hub, pursuit as the first workflow provider, advisor/subagent
  patterns, hooks, notification rules, config loading, and the composition root.

## 1. Summary

`eos-coding-agent` owns everything the SDK deliberately does not: **vocabulary and
policy**. The SDK knows agent/run/tool/background-task/notification; this project knows
operator, planner, worker, advisor, subagent, workflow, pursuit — and implements all of
them as ordinary code over the SDK's public surface.

Dependency rule (load-bearing):

```
eos-coding-agent ──imports──▶ eos-agent-core (root package only)
        │
        └─ owns: .eos-agents/ profiles · all tools · WorkflowHub + providers ·
                 pursuit (+db +contracts) · hooks/rules content · config loading ·
                 AgentRegistry · composition root
```

A second host (e.g. `eos-research-agent`) would be a sibling of this project, reusing the
SDK and none of this code — that event is also the trigger to consider lifting pursuit
into its own project; until then it lives here.

## 2. Layout

```
eos-coding-agent/
├── .eos-agents/                      profiles · pursuit policy scripts (planner.cjs etc.)
└── packages/
    ├── workflows/
    │   ├── hub/                      WorkflowHub + WorkflowProvider contract (host-owned)
    │   │   └── src/hub.ts · provider.ts (interface + manifest types) · tools.ts
    │   └── pursuit/
    │       ├── src/                  service.ts · pursuit|leg|attempt|plan|work-item/
    │       │                         {state,transition,context}.ts · context-engine/ ·
    │       │                         pursuit-tree.ts · pursuit-context.ts   (= today)
    │       │   ├── outcome-fns.ts    ★ planner/worker agentOutcomeFn via createAgentOutcomeFn
    │       │   ├── launcher.ts       ★ builds planner/worker Agents, starts runs
    │       │   └── provider.ts       ★ WorkflowProvider implementation over PursuitService
    │       ├── db/                   = today's @eos/db (schema · rows · migrations)
    │       └── contracts/            = contracts/pursuit.ts carve-out (entity DTOs,
    │                                   PursuitSettlement; SubmissionBinding deleted)
    ├── agents/                       AgentRegistry: .eos-agents profiles → AgentSpec → Agent
    ├── tools/                        every tool the model can call
    │   ├── coding/                   read · edit · grep · exec_command (yield pattern) …
    │   ├── run-subagent.ts           subagent pattern (foreground + background)
    │   ├── ask-advisor.ts            advisor consult
    │   ├── background-tasks.ts       list_background_task · cancel_background_task
    │   └── read-agent-run-transcript.ts   reads <recordsDir>/<runId>/*.jsonl
    └── app/
        ├── main.ts                   composition root
        ├── config/                   ✂ moved from agent-runtime: config-root.ts ·
        │                             config-file.ts · hook-config.ts ·
        │                             notification-rules-config.ts · profile loading
        ├── hooks/advisor-gate.ts     advisor pattern enforcement (pre-tool hook)
        └── pursuit-context-scripts.ts   ✂ moved verbatim
```

## 3. Composition root

```ts
// app/main.ts
const cfg = loadConfig(".eos-agents");                       // host-owned file discovery
const sdk = createAgentSdk({
  llmClients: cfg.llmClients,
  hooks: cfg.globalHooks,                                    // parsed with SDK schemas
  notificationRules: cfg.triggerRules,
  recordsDir: cfg.recordsDir,
});

const agents = buildAgentRegistry(sdk, cfg.profiles);        // §4
const hub = new WorkflowHub();                               // §6
hub.register(pursuitProvider({ db: openPursuitDb(cfg), sdk, agents, scripts: cfg.pursuitScripts }));

const operator = sdk.createAgent({
  name: "operator",
  llm: cfg.profiles.operator.llm,
  systemPrompt: cfg.profiles.operator.systemPrompt,
  tools: [
    ...codingTools,
    runSubagent(agents),
    askAdvisor(agents.advisor),
    ...hub.toolset({ allow: ["pursuit"] }),
    listBackgroundTask,
    cancelBackgroundTask,
    readAgentRunTranscript(cfg.recordsDir),
  ],
  hooks: [advisorGate({ tool: "submit_main_outcome", advisor: agents.advisor, instruction: SUBMIT_GUIDANCE })],
  agentOutcomeFn: createAgentOutcomeFn({ schema: MainOutcome }),  // trivial validator: no onSubmit
});
```

"No built-in tools" is literal: every entry in `tools:` above is authored in this repo.

## 4. Profiles and the AgentRegistry

- `.eos-agents/` keeps today's profile format; loaders (moved from `agent-runtime`) parse
  profiles into `AgentSpec` objects. The SDK never sees the files.
- `AgentRegistry` (name chosen over "catalog") maps profile names to constructed `Agent`s:

```ts
interface AgentRegistry {
  get(name: string): Agent | undefined;
  names(): string[];                 // launchable-by-subagent subset
  advisor: Agent;                    // a profile name this HOST blesses — not an SDK concept
}
```

- Strictness that used to live in the SDK's profile loader moves here: **pursuit's startup
  validates that its planner/worker profiles carry an outcome schema** (a free-text planner
  would synthesize garbage transitions). Host policy, host enforcement.

## 5. Tools

All tools are `defineTool` over the SDK's `ToolCallContext` capabilities. There is no
`behavior` metadata; what a tool *does* defines it.

**Background-task tools** (pure projections of the run-scoped supervisor):

```ts
export const listBackgroundTask = defineTool({
  name: "list_background_task", schema: z.object({}),
  execute: async (_i, ctx) => ({ output: renderRows(ctx.backgroundTaskSupervisor.list()) }),
});
export const cancelBackgroundTask = defineTool({
  name: "cancel_background_task", schema: z.object({ task_id: z.string() }),
  execute: async (i, ctx) =>
    ({ output: (await ctx.backgroundTaskSupervisor.cancel(i.task_id)) ? "cancelled" : "not found (already completed?)" }),
});
```

**Subagent pattern** — foreground and background through one public API; the completion
message is fully host-authored in `onCompletion`:

```ts
export const runSubagent = (agents: AgentRegistry) => defineTool({
  name: "run_subagent",
  description: `Launch a subagent. Available: ${agents.names().join(", ")}`,
  schema: z.object({ agent: z.string(), prompt: z.string(), wait: z.boolean().default(true) }),
  execute: async (input, ctx) => {
    const agent = agents.get(input.agent);
    if (!agent) return { error: `unknown agent: ${input.agent}` };
    const run = agent.start_agent_run({ messages: [{ role: "user", content: input.prompt }] });

    if (input.wait) return { output: renderOutcome(await run.wait_for_agent_outcome()) };   // foreground

    const { taskId } = ctx.backgroundTaskSupervisor.register({              // background
      toolName: "run_subagent",
      title: `${input.agent}: ${input.prompt.slice(0, 60)}`,
      cancel: () => run.interrupt(),
      done: run.wait_for_agent_outcome().then(toTaskOutcome),
      onCompletion: async (out, { notifier }) => {
        await indexTranscript(run.runId);                                   // side effects welcome
        notifier.publish(out.status === "success"
          ? `subagent ${input.agent} done: ${out.outcome}`
          : `subagent ${input.agent} ${out.status}: ${out.outcome} — transcript: ${recordsDir}/${run.runId}`,
          { key: `subagent:${run.runId}` });
      },
    });
    return { output: `subagent started · task ${taskId}` };
  },
});
```

**Advisor pattern** — an advisor is just an agent you wait on, plus a hook that makes
consultation mandatory at submission boundaries (§7).

```ts
export const askAdvisor = (advisor: Agent) => defineTool({
  name: "ask_advisor", schema: z.object({ question: z.string(), context: z.string().optional() }),
  execute: async (input) =>
    ({ output: renderOutcome(await advisor.start_agent_run({ messages: [asAdvisorAsk(input)] }).wait_for_agent_outcome()) }),
});
```

**exec_command (yield pattern):** run to a yield point (timeout / quiet period / output
cap). Finished → return final output. Still running → `register` a task (cancel = kill
process group; `done` = exit promise; `onCompletion` publishes exit status + tail) and
return partial output + taskId. The engine knows nothing about any of this.

**Conventions (lint-level):**

- A tool that registers a task must not also publish its completion separately —
  `onCompletion` is the single completion publisher; anything else is a double-publish bug.
- Any task the model is expected to *await* MUST publish in `onCompletion`; silent
  completion is strictly for fire-and-forget work. Completed tasks are removed from the
  registry the moment `onCompletion` finishes, so a silent task leaves **no trace the model
  can see** — not even in `list_background_task`.

## 6. WorkflowHub and the provider contract (host-owned)

```ts
// workflows/hub/src/provider.ts — a coding-agent contract, NOT an SDK one
interface WorkflowProvider {
  describe(): WorkflowManifest;       // name · description · payloadSchema · titleOf · tool docs
  delegate(payload: unknown): Promise<WorkflowRunId>;
  settle(id: WorkflowRunId): Promise<WorkflowOutcome>;
  cancel(id: WorkflowRunId): Promise<void>;
  query?(id: WorkflowRunId, q: unknown): Promise<unknown>;   // optional
  search?(q: unknown): Promise<unknown>;                     // optional
}
```

The hub projects providers into tools: `list_workflows()`, `read_workflow_definition(name)`,
and one `delegate_<name>(payload)` per registered provider (payload schema from
`describe()`). **Workflow cancellation needs no tool of its own** — delegation registers a
background task, so `cancel_background_task(taskId)` reaches `provider.cancel()` through
the task's `cancel`:

```ts
const delegateTool = (p: WorkflowProvider) => {
  const m = p.describe();
  return defineTool({
    name: `delegate_${m.name}`, description: m.description, schema: m.payloadSchema,
    execute: async (input, ctx) => {
      const id = await p.delegate(input);
      const { taskId } = ctx.backgroundTaskSupervisor.register({
        toolName: `workflow:${m.name}`, title: m.titleOf(input),
        cancel: () => p.cancel(id),
        done: p.settle(id).then(toTaskOutcome),
        onCompletion: m.onCompletion?.(id),        // provider authors its settlement message
      });
      return { output: `delegated ${m.name} · task ${taskId}` };
    },
  });
};
```

Policies like "one open pursuit per supervisor" generalize to host hooks over
`ctx.backgroundTaskSupervisor.count()/list()` — e.g. a pre-tool hook on `delegate_*`
denying when an open `workflow:*` task exists.

If a workflow is ever written in another language, it enters as another
`WorkflowProvider` implementation that proxies over a wire; the hub, the projections, and
every profile stay untouched.

## 7. Hooks and the advisor gate

The advisor-validates-submission machinery that used to be SDK behavior becomes one host
hook. Ordering does the work: **pre-tool hook → advisor verdict → only then `onSubmit`** —
so a rejection mutates nothing and burns no budget; the model corrects in-run.

```ts
export function advisorGate(opts: { tool: string; advisor: Agent; instruction: string }): HookConfigEntry {
  return {
    event: "pre_tool_use",
    matcher: { tool_name: opts.tool },
    command: { type: "callback", run: async (payload) => {
      const verdict = await opts.advisor
        .start_agent_run({ messages: [asReview(opts.instruction, payload.input)] })
        .wait_for_agent_outcome();
      return approves(verdict)
        ? { decision: "passthrough" }
        : { decision: "deny", reason: reasonOf(verdict) };     // fed back to the model in-run
    }},
  };
}
```

Failure policy is explicit host policy: if the advisor run itself dies, choose fail-open
(`passthrough` + warning in the reason channel) or fail-closed (`deny`) **in this
function** — never leave it implicit.

## 8. Pursuit as a workflow provider

Pursuit moves wholesale; its state machines, transitions, context-engine, tree, db schema,
and reconcile logic are unchanged. The changes are confined to the launch/settle edge:

| Pursuit internal | Before (in eos-agent-core) | After (here) |
|---|---|---|
| `agent-launcher.ts` (`AgentLaunchPort`, `LaunchSettlement`, `LaunchedAgent`) | port implemented by agent-runtime | **deleted** — `launcher.ts` calls `sdk.createAgent(...).start(...)` directly |
| `PursuitServiceDependencies` | `{db, compose, resolve, launch port}` | `{db, compose, sdk, profiles: {planner, isWorker}}` (profile content injected by `app/`, never loaded by pursuit) |
| Submission entry | runtime routes `submit_planner_outcome` payloads into service claim methods | the **same claim/transition methods**, invoked from `onSubmit` closures in `outcome-fns.ts` — transactional, keyed by `ctx.submissionId`; invalid transitions (e.g. refocus in predefined mode) return `{reject}` → in-run correction, budget intact |
| `PursuitAgentSubmissionBinding` | threaded through contracts/engine | deleted (replaced by `onSubmit`) |
| Planner/worker advisory prompts | `@eos/tool/advisory_prompts/` | move here; ride `createAgentOutcomeFn({description})` |
| Death / cancel | runtime-synthesized `LaunchSettlement` | `run.wait_for_agent_outcome().then(...)` → `out.status !== "completed"` → pursuit synthesizes the Failed work item; `cancel()` additionally calls `handle.interrupt()` on live runs |
| Settlement notification | SDK-rendered session message | `provider.ts` authors it in pursuit vocabulary inside the task's `onCompletion` (e.g. "pursuit settled: Failed — leg_2 budget exhausted · outcome.md: <path>"), publishing via the provided `notifier` — or staying silent for internal sub-steps |

One attempt, after the change:

```ts
const planner = deps.sdk.createAgent({
  name: deps.profiles.planner.name,
  llm: deps.profiles.planner.llm,
  systemPrompt: deps.profiles.planner.systemPrompt,
  tools: deps.profiles.plannerTools,                       // injected by app/
  agentOutcomeFn: plannerOutcomeFn(this),                  // outcome-fns.ts: schema + description +
});                                                        //   onSubmit → applyPlanSubmission(trx)
const run = planner.start_agent_run({ messages: composeLaunchContext(tree) });  // context-engine, unchanged
run.wait_for_agent_outcome().then((out) => this.reconcileAfterRun(attemptId, out));  // death synthesis
// success already mutated state inside onSubmit — reconcile only reads back
```

Discipline carried over unchanged: **`onSubmit` is the only writer; loops reconcile.**
Anyone "fixing a bug" by also mutating at the `outcome()` site reintroduces the dual-write
drift the original design exists to prevent.

## 9. Configuration loading

All file I/O for configuration lives in `app/config/` (files moved verbatim from
`agent-runtime`): discovery (`config-root`), parsing (`config-file`), hooks
(`hook-config`), trigger rules (`notification-rules-config`), profiles. Parsed objects are
validated with the SDK-exported schemas and passed into `createAgentSdk` / `AgentSpec`.
`recordsDir` is chosen here. Reload/watch semantics, layering (user vs project), and
defaults are host policy.

## 10. Migration sequencing

1. **Terminal-contract inversion inside eos-agent-core** — add `createAgentOutcomeFn` +
   `onSubmit`; rewire planner/worker bindings through it; delete
   `PursuitAgentSubmissionBinding`. Net-negative SDK change; pursuit still in-tree.
   *Verify:* invariants 2–4 of the SDK spec.
2. **Capability handles** — per-run `BackgroundTaskSupervisor` + `Notifier` on handle and
   `ToolCallContext`; `backgroundSession` → `backgroundTask` renames; settlement →
   `onCompletion` (supervisor stops publishing); remove-on-completion registry with single
   `count()`; run-end disposal; `task_registered`/`task_settled` events.
   *Verify:* invariants 5–9 and 12.
3. **Extract eos-coding-agent** — move pursuit (+db +contracts), tool families, advisory
   prompts, config loaders, profile loading, context scripts; create
   `agents/ tools/ workflows/ app/`; pursuit consumes the SDK directly (port deleted).
   *Verify:* leak checks (SDK spec §7); the e2e suite moves with the host
   (`notification-triggers.e2e.ts` is in flight in the current worktree — coordinate).
4. **Hub + providers** — `WorkflowHub` host-side, pursuit registered as the first provider;
   delegate/cancel ride background tasks.
   *Verify:* operator can delegate, observe via `list_background_task`, cancel via
   `cancel_background_task`; pursuit settlement message arrives via `onCompletion`.

## 11. Acceptance criteria

- This repo imports only the SDK root package (no `@eos/engine`, `@eos/tool`, … internals).
- Deleting `packages/workflows/pursuit` still compiles `app/` (hub registers nothing).
- Every tool the operator sees is defined in `packages/tools/` or projected by
  `packages/workflows/hub` — `grep` the SDK for tool definitions → none.
- Advisor enforcement demonstrably runs **before** `onSubmit` (test: advisor denies → no
  DB row, no budget spent, model receives the denial in-run).
- Pursuit behavior parity: existing pursuit service tests pass against the SDK-backed
  launcher with only dependency-shape changes.
- Inbox exhaustiveness: in a full e2e run, every notification is traceable to a host
  `publish` (incl. `onCompletion`) or a configured trigger rule.

## 12. Open questions

- `AgentRegistry.names()` scoping — flat allowlist vs per-profile launchable sets (decide
  with real subagent usage).
- Whether `read_agent_run_transcript` should page/filter (records can be large) — tool
  design, not contract.
- Pursuit context-exploration tools (planned in the operator manual §07) — they become
  ordinary host tools over the pursuit context store; spec separately when prioritized.
- Timing of lifting pursuit to a standalone project — trigger is a second host wanting it,
  not before.
