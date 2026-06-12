# eos-coding-agent — Host Application Specification

- **Status:** Draft for review
- **Date:** 2026-06-13
- **Depends on:** `eos-agent-sdk_SPEC.md` (the SDK). This project imports **only** the
  SDK's root package — never its internal packages.
- **Scope:** The product/host that composes the SDK into a coding agent: profiles, every
  tool, the workflow hub, pursuit as the first workflow, advisor/subagent
  patterns, hooks, notification rules, config loading, and the composition root.

## 1. Summary

`eos-coding-agent` owns everything the SDK deliberately does not: **vocabulary and
policy**. The SDK knows agent/run/outcome/tool/background-task/notification/hook; this
project knows operator, planner, worker, advisor, subagent, workflow, pursuit — and
implements all of them as ordinary code over the SDK's public surface.

Dependency rule (load-bearing):

```
eos-coding-agent ──imports──▶ eos-agent-sdk (root package only)
        │
        └─ owns: .eos-agents/ profiles · all tools · WorkflowHub + workflows ·
                 pursuit (+db +contracts) · hooks/rules content · config loading ·
                 AgentRegistry · composition root
```

A second host (e.g. `eos-research-agent`) would be a sibling of this project, reusing the
SDK and none of this code — that event is also the trigger to consider lifting pursuit
into its own project; until then it lives here.

## 2. Layout

```
eos-coding-agent/
├── .eos-agents/                      ONE config root: profiles · hooks · notification rules ·
│                                     workflow settings (e.g. pursuit policy scripts, planner.cjs)
└── packages/
    ├── workflows/
    │   ├── hub/                      WorkflowHub + WorkflowDefinition contract (host-owned)
    │   │   └── src/hub.ts · workflow.ts (contract + defineWorkflow) · tools.ts
    │   └── pursuit/
    │       ├── src/                  service.ts · pursuit|leg|attempt|plan|work-item/
    │       │                         {state,transition,context}.ts · context-engine/ ·
    │       │                         pursuit-tree.ts · pursuit-context.ts   (= today)
    │       │   ├── outcome-fns.ts    ★ planner/worker agentOutcomeFn via createAgentOutcomeFn
    │       │   ├── launcher.ts       ★ builds planner/worker Agents, starts runs
    │       │   ├── workflow.ts       ★ WorkflowDefinition over PursuitService
    │       │   └── index.ts          ★ WorkflowModule export — opens the embedded db;
    │       │                           ready for hub.register, no wiring in app/
    │       ├── db/                   embedded storage (today's @eos/db: schema · rows ·
    │       │                         migrations) — opened by pursuit itself, never injected
    │       └── contracts/            = contracts/pursuit.ts carve-out (entity DTOs,
    │                                   PursuitSettlement; SubmissionBinding deleted)
    ├── agents/                       AgentRegistry: .eos-agents profiles → AgentSpec → Agent
    ├── tools/                        every tool the model can call
    │   ├── sandbox/                  read · edit · grep · exec_command (yield pattern) —
    │   │                             specced (§5), NOT implemented in this phase
    │   ├── agent/
    │   │   ├── run_subagent.ts       subagent pattern (foreground + background)
    │   │   ├── advisor.ts            advisor consult (ask_advisor)
    │   │   └── read-agent-run.ts     reads <recordsDir>/<runId>/*.jsonl
    │   ├── background/
    │   │   ├── list_background_tasks.ts    list_background_task projection
    │   │   └── cancel_background_task.ts   cancel_background_task projection
    │   └── test/                     playground tools — manual/e2e testing only
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
import { pursuit } from "@eos/workflow-pursuit";             // index.ts module — one import per workflow

const cfg = loadEosConfig(".eos-agents");                    // ONE root: profiles · hooks · rules · workflows
const sdk = createAgentSdk({
  llmClients: cfg.llmClients,
  hooks: [...cfg.globalHooks,                                // host-validated callbacks
          ...compileNotificationRules(cfg.triggerRules)],    // rule files → turnBoundary entries
  recordsDir: cfg.recordsDir,
});

const agents = buildAgentRegistry(sdk, cfg.profiles);        // §4
const hub = new WorkflowHub({ sdk, agents, settings: cfg.workflows });   // §6 — workflow deps live HERE
await hub.register(pursuit);                                 // no workflow-specific wiring in this file

const operator = sdk.createAgent({
  name: "operator",
  llm: cfg.profiles.operator.llm,
  systemPrompt: cfg.profiles.operator.systemPrompt,
  tools: [
    // ...sandboxTools,                                      // tools/sandbox — later phase (§5)
    runSubagent(agents),
    askAdvisor(agents.advisor),
    ...hub.toolset(),                                        // enable/allow gating from .eos-agents/workflows
    listBackgroundTask,
    cancelBackgroundTask,
    readAgentRun(cfg.recordsDir),
  ],
  hooks: [advisorGate({ tool: "submit_main_outcome", advisor: agents.advisor, instruction: SUBMIT_GUIDANCE })],
  agentOutcomeFn: createAgentOutcomeFn({ name: "submit_main_outcome", schema: MainOutcome }),
  //                                       trivial validator: no onSubmit
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

**Background-task tools** (`tools/background/`, one file per tool — pure projections of
the run-scoped supervisor):

```ts
export const listBackgroundTask = defineTool({
  name: "list_background_task", input: z.object({}),
  execute: async (_i, ctx) => ({ output: renderRows(ctx.backgroundTaskSupervisor.list()) }),
});
export const cancelBackgroundTask = defineTool({
  name: "cancel_background_task", input: z.object({ task_id: z.string() }),
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
  input: z.object({ agent: z.string(), prompt: z.string(), wait: z.boolean().default(true) }),
  execute: async (input, ctx) => {
    const agent = agents.get(input.agent);
    if (!agent) return { error: `unknown agent: ${input.agent}` };
    const run = agent.start({ messages: [{ role: "user", content: input.prompt }] });

    if (input.wait) return { output: renderOutcome(await run.outcome()) };   // foreground

    const { taskId } = ctx.backgroundTaskSupervisor.register({              // background
      toolName: "run_subagent",
      title: `${input.agent}: ${input.prompt.slice(0, 60)}`,
      cancel: () => run.interrupt(),
      done: run.outcome().then(toTaskOutcome),
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
  name: "ask_advisor", input: z.object({ question: z.string(), context: z.string().optional() }),
  execute: async (input) =>
    ({ output: renderOutcome(await advisor.start({ messages: [asAdvisorAsk(input)] }).outcome()) }),
});
```

**exec_command (yield pattern — `tools/sandbox/`, specced now, implemented in a later
phase):** run to a yield point (timeout / quiet period / output cap). Finished → return
final output. Still running → `register` a task (cancel = kill process group; `done` =
exit promise; `onCompletion` publishes exit status + tail) and return partial output +
taskId. The engine knows nothing about any of this.

**Playground tools** (`tools/test/`) are testing-only scaffolding for manual and e2e
runs; they never appear in shipped profiles.

**Conventions (lint-level):**

- A tool that registers a task must not also publish its completion separately —
  `onCompletion` is the single completion publisher; anything else is a double-publish bug.
- Every task declares `onCompletion` or `silent: true` — the SDK's task type forces the
  choice. Any task the model is expected to *await* MUST publish in `onCompletion`;
  `silent: true` is strictly for fire-and-forget work. Completed tasks are removed from
  the registry the moment completion handling finishes, so a silent task leaves **no
  trace the model can see** — not even in `list_background_task`.

## 6. WorkflowHub and the workflow contract (host-owned)

```ts
// workflows/hub/src/workflow.ts — a coding-agent contract, NOT an SDK one
interface WorkflowDefinition<I> {
  name: string;                        // "pursuit" — the tool-family prefix
  description: string;                 // one line; rides the delegate tool + list_workflows row
  docs: string;                        // the manual; served by read_workflow_definition
  delegatePayload: z.ZodType<I>;       // written once; I and the model-facing schema derive from it
  delegate(payload: I): Promise<WorkflowHandle>;   // async (delegation does I/O);
                                                   //   throw = refuse → in-run tool error
  tools?: ToolDefinition[];            // optional read tools (query/search …), named `${name}_*`
}

interface WorkflowHandle {
  title: string;
  cancel(): void | Promise<void>;      // idempotent; no-op after settlement
  done: Promise<BackgroundTaskOutcome>;// workflow authors the outcome string in its own vocabulary
}

export function defineWorkflow<I>(init: WorkflowDefinition<I>): RegisteredWorkflow;
// mint site: erases I for the hub registry; enforces `${name}_*` naming on `tools`

// registration: each workflow package exports ONE module from its index.ts
interface WorkflowModule {
  name: string;                        // gate key — .eos-agents/workflows/<name>
  create(init: WorkflowInit): Promise<RegisteredWorkflow>;
}
interface WorkflowInit { sdk: AgentSdk; agents: AgentRegistry; settings: JsonObject }
// `settings` is the workflow's .eos-agents slice (e.g. pursuit policy scripts).
// Storage is embedded: pursuit opens its own db inside create() — never injected by app/.
```

`hub.register(module)` resolves the module against `.eos-agents/workflows`: disabled →
no-op; enabled → `create()` runs with the hub-held `sdk`/`agents` and the workflow's
settings slice. The composition root therefore contains **no workflow-specific wiring** —
one import plus one `register` line per workflow (§3).

The shape mirrors `defineTool` (`delegatePayload`/`delegate` ↔ `input`/`execute`, same
single-source inference); each divergence is a real property of workflows — `docs` because
a workflow needs a manual, a handle because the work outlives the call, `tools` because a
workflow is a family. `WorkflowHandle` is `BackgroundTask` minus the two hub-owned fields
(`toolName`, `onCompletion`); that minus is the hub/workflow ownership line. There is no
`WorkflowRunId`, no `settle`, no per-workflow `onCompletion`, no `silent`: the handle
subsumes the first two, settlement publishing is hub-owned (below), and a delegation is
awaited work — the §5 task rule forbids it being silent.

| Field | Sole consumer |
|---|---|
| `name` | tool naming (`pursuit_delegate`) · `list_workflows` · task `toolName` |
| `description` | delegate tool description + `list_workflows` row |
| `docs` | `read_workflow_definition` |
| `delegatePayload` | delegate tool `input` (schema in the toolset; SDK-validated pre-`delegate`) |
| `delegate` | delegate tool `execute` |
| `title` / `cancel` / `done` | spread into `backgroundTaskSupervisor.register` |
| `tools` | appended to the hub toolset |

The hub projects two always-present discovery tools — `list_workflows()` (one row per
workflow: name · description · tool names · ready state) and
`read_workflow_definition(name)` (returns `docs`; unknown name → error listing the valid
names) — plus, per workflow, one delegate tool and its `tools` entries.
**Workflow cancellation needs no tool of its own** — delegation registers a background
task, so `cancel_background_task(taskId)` reaches `handle.cancel()` through the task:

```ts
const delegateTool = <I>(w: WorkflowDefinition<I>) => defineTool({
  name: `${w.name}_delegate`,
  description: `${w.description} Before first use: read_workflow_definition("${w.name}").`,
  input: w.delegatePayload,
  execute: async (input, ctx) => {
    const handle = await w.delegate(input);              // throw → tool error → in-run correction
    const { taskId } = ctx.backgroundTaskSupervisor.register({
      toolName: `workflow:${w.name}`,
      title: handle.title,
      cancel: () => handle.cancel(),
      done: handle.done,
      onCompletion: (out, { notifier }) =>               // the ONE settlement publisher
        notifier.publish(`workflow ${w.name} ${out.status}: ${out.outcome}`,
                         { key: `workflow:${w.name}:${taskId}` }),
    });
    return { output: `delegated ${w.name} · task ${taskId}` };
  },
});
```

```
model ── pursuit_delegate(payload) ─▶ hub tool ── w.delegate(payload) ─▶ workflow I/O → handle
              │                                       (throw = refusal, in-run error)
              └─ register({title, cancel, done, onCompletion}) ─▶ "delegated · task <id>"
model ── cancel_background_task(taskId) ─────────▶ handle.cancel()    (no per-workflow cancel tool)
settlement: done resolves ─▶ hub onCompletion publishes the workflow-authored
            outcome string ─▶ notifier ─▶ inbox, drained at the next turn boundary
```

**Preload, not deferral.** Every workflow tool's schema is declared in the toolset from
turn 1; descriptions stay terse and point at the manual; the prose (payload semantics,
examples, settlement shape, cancellation path) lives behind `read_workflow_definition`.
This works on every model and wire, and keeps the encoded tools param byte-stable across a
run, so prompt caching holds. Platform deferral (Anthropic `defer_loading`, OpenAI
`tool_search`) is model/API-gated and is the documented growth path, not v1: if workflow
or MCP schemas ever crowd context (≈10% of the window — the threshold Claude Code uses),
a `defer` flag and a reveal capability slot in beneath this same protocol
(`read_workflow_definition` regains a loading side effect); profiles, prompts, and the hub
surface do not change.

Policies like "one open pursuit per supervisor" generalize to host hooks over
`ctx.backgroundTaskSupervisor.list()` — e.g. a pre-tool hook on the delegate tools denying
when an open `workflow:*` task exists.

If a workflow is ever written in another language, it enters as another
`WorkflowDefinition` whose `delegate` proxies over a wire; the hub, the projections, and
every profile stay untouched.

## 7. Hooks and the advisor gate

The advisor-validates-submission machinery that used to be SDK behavior becomes one host
hook. Ordering does the work: **pre-tool hook → advisor verdict → only then `onSubmit`** —
so a rejection mutates nothing and burns no budget; the model corrects in-run.

```ts
export function advisorGate(opts: { tool: string; advisor: Agent; instruction: string }): HookEntry {
  return {
    event: "preToolUse",
    matcher: { toolName: opts.tool },
    run: async (call) => {
      const verdict = await opts.advisor
        .start({ messages: [asReview(opts.instruction, call.input)] })
        .outcome();
      return approves(verdict)
        ? { decision: "passthrough" }
        : { decision: "deny", reason: reasonOf(verdict) };     // fed back to the model in-run
    },
  };
}
```

The gate sees `ToolCallFacts` only — the submission payload, never the conversation (SDK
spec §4.3). Submission schemas must therefore be self-contained enough to vet on their
own: the advisor judges *what* the model submits, not how it got there.

Failure policy is explicit host policy: if the advisor run itself dies, choose fail-open
(`passthrough` + warning in the reason channel) or fail-closed (`deny`) **in this
function** — never leave it implicit.

## 8. Pursuit as a workflow

Pursuit moves wholesale; its state machines, transitions, context-engine, tree, db schema,
and reconcile logic are unchanged. The changes are confined to the launch/settle edge:

| Pursuit internal | Before (in eos-agent-core) | After (here) |
|---|---|---|
| `agent-launcher.ts` (`AgentLaunchPort`, `LaunchSettlement`, `LaunchedAgent`) | port implemented by agent-runtime | **deleted** — `launcher.ts` calls `sdk.createAgent(...).start(...)` directly |
| `PursuitServiceDependencies` | `{db, compose, resolve, launch port}` | `{compose, sdk, profiles: {planner, isWorker}}` — db embedded, opened inside the module's `create()` (§6); profile content arrives via `WorkflowInit`, never loaded by pursuit |
| Submission entry | runtime routes `submit_planner_outcome` payloads into service claim methods | the **same claim/transition methods**, invoked from `onSubmit` closures in `outcome-fns.ts` — transactional, keyed by `ctx.submissionId`; invalid transitions (e.g. refocus in predefined mode) return `{reject}` → in-run correction, budget intact |
| `PursuitAgentSubmissionBinding` | threaded through contracts/engine | deleted (replaced by `onSubmit`) |
| Planner/worker advisory prompts | `@eos/tool/advisory_prompts/` | move here; ride `createAgentOutcomeFn({description})` |
| Death / cancel | runtime-synthesized `LaunchSettlement` | `run.outcome().then(...)` → `out.status !== "completed"` → pursuit synthesizes the Failed work item; `cancel()` additionally calls `handle.interrupt()` on live runs |
| Settlement notification | SDK-rendered session message | pursuit resolves the handle's `done` with an outcome string authored in pursuit vocabulary (e.g. "Failed — leg_2 budget exhausted · outcome.md: <path>"); the hub's single completion publisher delivers it (§6). No `silent` option — a delegation is awaited work |

One attempt, after the change:

```ts
const planner = deps.sdk.createAgent({
  name: deps.profiles.planner.name,
  llm: deps.profiles.planner.llm,
  systemPrompt: deps.profiles.planner.systemPrompt,
  tools: deps.profiles.plannerTools,                       // injected by app/
  agentOutcomeFn: plannerOutcomeFn(this),                  // outcome-fns.ts: name + schema + description +
});                                                        //   onSubmit → applyPlanSubmission(trx)
const run = planner.start({ messages: composeLaunchContext(tree) });  // context-engine, unchanged
run.outcome().then((out) => this.reconcileAfterRun(attemptId, out));  // death synthesis
// success already mutated state inside onSubmit — reconcile only reads back
```

Discipline carried over unchanged: **`onSubmit` is the only writer; loops reconcile.**
Anyone "fixing a bug" by also mutating at the `outcome()` site reintroduces the dual-write
drift the original design exists to prevent.

## 9. Configuration loading

`.eos-agents/` is the **single configuration root**: profiles, hooks, notification rules,
and per-workflow settings (the `.eos-agents/workflows/<name>` slice handed to
`WorkflowModule.create`, §6) all live under it — nothing registers from scattered
locations. All file I/O for configuration lives in `app/config/` (files moved verbatim
from `agent-runtime`): discovery (`config-root`), parsing (`config-file`), hooks
(`hook-config`), trigger rules (`notification-rules-config`), profiles, workflow
settings. Parsed objects are
validated by host-owned schemas (the SDK exports none) and passed into `createAgentSdk` /
`AgentSpec`. Trigger rules compile into `turnBoundary` hook entries
(`compileNotificationRules`, §3). `recordsDir` is chosen here. Reload/watch semantics,
layering (user vs project), and defaults are host policy.

## 10. Migration sequencing

1. **Terminal-contract inversion inside eos-agent-core** — add `createAgentOutcomeFn` +
   `onSubmit`; rewire planner/worker bindings through it; delete
   `PursuitAgentSubmissionBinding`. Net-negative SDK change; pursuit still in-tree.
   *Verify:* invariants 2–4 of the SDK spec.
2. **Capability handles** — per-run `BackgroundTaskSupervisor` + `Notifier` on handle and
   `ToolCallContext`; `backgroundSession` → `backgroundTask` renames; settlement →
   `onCompletion` (supervisor stops publishing); remove-on-completion registry
   (`register/list/cancel`, explicit-silence task contract); run-end disposal;
   `task_registered`/`task_settled` events.
   *Verify:* invariants 5–9 and 12.
3. **Extract eos-coding-agent** — move pursuit (+db +contracts), tool families, advisory
   prompts, config loaders, profile loading, context scripts; create
   `agents/ tools/ workflows/ app/`; pursuit consumes the SDK directly (port deleted);
   rename the SDK workspace **`eos-agent-core` → `eos-agent-sdk`** (directory, root
   package name, imports).
   *Verify:* leak checks (SDK spec §7); the e2e suite moves with the host
   (`notification-triggers.e2e.ts` is in flight in the current worktree — coordinate).
4. **Hub + workflows** — `WorkflowHub` host-side, pursuit registered as the first
   `WorkflowDefinition`; delegation rides background tasks.
   *Verify:* operator can `list_workflows` → `read_workflow_definition("pursuit")` →
   `pursuit_delegate`, observe via `list_background_task`, cancel via
   `cancel_background_task`; the settlement notification carries pursuit-authored outcome
   text through the hub's completion publisher.

## 11. Acceptance criteria

- This repo imports only the SDK root package `eos-agent-sdk` (no `@eos/engine`,
  `@eos/tool`, … internals).
- Deleting `packages/workflows/pursuit` requires removing exactly one import and one
  `hub.register` line in `app/main.ts` — nothing else; the hub then registers nothing and
  the operator keeps every non-workflow tool. The composition root contains no other
  workflow-specific code.
- Every tool the operator sees is defined in `packages/tools/` or projected by
  `packages/workflows/hub` — `grep` the SDK for tool definitions → none.
- Advisor enforcement demonstrably runs **before** `onSubmit` (test: advisor denies → no
  DB row, no budget spent, model receives the denial in-run).
- `read_workflow_definition` is a pure docs tool: it returns `docs` and mutates nothing;
  the operator toolset (and therefore the encoded tools param) is identical on every turn
  of a run.
- Pursuit behavior parity: existing pursuit service tests pass against the SDK-backed
  launcher with only dependency-shape changes.
- Inbox exhaustiveness: in a full e2e run, every notification is traceable to a host
  `publish` (incl. `onCompletion`) or a configured trigger rule.

## 12. Open questions

- `AgentRegistry.names()` scoping — flat allowlist vs per-profile launchable sets (decide
  with real subagent usage).
- Whether `read_agent_run` should page/filter (records can be large) — tool design, not
  contract.
- Pursuit context-exploration tools (planned in the operator manual §07) — they become
  ordinary host tools over the pursuit context store, riding pursuit's `tools` array on
  its `WorkflowDefinition`; spec separately when prioritized.
- If a second large tool source lands (e.g. MCP servers wrapped as host tool families),
  generalize `list_workflows` / `read_workflow_definition` into group-based discovery and
  revisit the §6 deferral growth path against the ≈10%-of-context threshold.
- Timing of lifting pursuit to a standalone project — trigger is a second host wanting it,
  not before.
