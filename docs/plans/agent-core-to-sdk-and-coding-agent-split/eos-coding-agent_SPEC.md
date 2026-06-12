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
        └─ owns: .eos-agents/ profiles + workflow.json · all tools ·
                 WorkflowHub + workflows ·
                 pursuit (+db +contracts) · hooks/rules content · config loading ·
                 AgentFactory · composition root
```

A second host (e.g. `eos-research-agent`) would be a sibling of this project, reusing the
SDK and none of this code — that event is also the trigger to consider lifting pursuit
into its own project; until then it lives here.

## 2. Layout

```
eos-coding-agent/
├── .eos-agents/                      ONE config root: profiles · workflow.json · hooks ·
│   ├── workflow.json                 workflow instance map; e.g. pursuit1:
│   │                                 {type:"pursuit", args:{planner, worker}}
│   ├── profile/                      agent profile files
│   ├── hooks.json                    hook config
│   ├── notification_rules.json       notification-rule config
│   └── pursuit/                      pursuit-owned scripts/settings
└── src/
    ├── main.ts                       composition root
    ├── config/                       ✂ moved from agent-runtime: config-root.ts ·
    │                                 config-file.ts · hook-config.ts ·
    │                                 notification-rules-config.ts · profile/workflow loading
    ├── agents/                       singleton AgentFactory: agent name + SDK -> Agent
    │   ├── agent-factory.ts
    │   └── profiles.ts
    ├── tools/                        every model-visible tool: one file per tool
    │   ├── agent/
    │   │   ├── run-subagent.ts       subagent pattern (foreground + background)
    │   │   ├── ask-advisor.ts        advisor consult (ask_advisor)
    │   │   └── read-agent-run.ts     reads <recordsDir>/<runId>/*.jsonl
    │   ├── background/
    │   │   ├── list-background-task.ts
    │   │   └── cancel-background-task.ts
    │   ├── workflow/
    │   │   ├── list-workflows.ts      generic discovery tool
    │   │   ├── read-workflow-definition.ts
    │   │   ├── delegate-workflow.ts   projects `${instance.name}_delegate`
    │   │   └── index.ts              workflow tool aggregation
    │   └── index.ts                  export aggregation only
    └── workflows/
        ├── registry.ts               WorkflowHub + configured workflow instances
        ├── workflow.ts               WorkflowDefinition contract + defineWorkflow
        └── pursuit/
            ├── service.ts
            ├── pursuit|leg|attempt|plan|work-item/
            │                         {state,transition,context}.ts
            ├── context-engine/
            ├── outcome-fns.ts        planner/worker agentOutcomeFn via createAgentOutcomeFn
            ├── launcher.ts           builds planner/worker Agents from AgentFactory
            ├── workflow.ts           WorkflowDefinition over PursuitService
            ├── index.ts              WorkflowModule export; opens pursuit-owned store
            ├── store/                embedded storage (today's @eos/db: schema · rows · migrations)
            └── contracts.ts          entity DTOs; PursuitSettlement; SubmissionBinding deleted
```

## 3. Composition root

```ts
// app/main.ts
import { pursuit } from "./workflows/pursuit/index.js";      // index.ts module — one import per workflow

const cfg = loadEosConfig(".eos-agents");                    // ONE root: profiles · hooks · rules · workflows
const sdk = createAgentSdk({
  llmClients: cfg.llmClients,
  hooks: [...cfg.globalHooks,                                // host-validated callbacks
          ...compileNotificationRules(cfg.triggerRules)],    // rule files → turnBoundary entries
  recordsDir: cfg.recordsDir,
});

const agents = buildAgentFactory(sdk, cfg.profiles);         // §4: one factory per app bootstrap
const hub = new WorkflowHub({ agents, workflows: cfg.workflow }); // §6
await hub.register(pursuit);                                 // no instance-specific wiring in this file
const advisor = agents.create("advisor", { tools: [] });

const operator = sdk.createAgent({
  name: "operator",
  llm: cfg.profiles.operator.llm,
  systemPrompt: cfg.profiles.operator.systemPrompt,
  tools: [
    runSubagent(agents),
    askAdvisor(advisor),
    ...workflowTools(hub),                                   // list/read/delegate from tools/workflow/*
    listBackgroundTask,
    cancelBackgroundTask,
    readAgentRun(cfg.recordsDir),
  ],
  hooks: [advisorGate({ tool: "submit_main_outcome", advisor, instruction: SUBMIT_GUIDANCE })],
  agentOutcomeFn: createAgentOutcomeFn({ name: "submit_main_outcome", schema: MainOutcome }),
  //                                       trivial validator: no onSubmit
});
```

"No built-in tools" is literal: every entry in `tools:` above is authored in this repo.

## 4. Profiles, Workflow Config, and the AgentFactory

- `.eos-agents/` keeps today's profile format; loaders (moved from `agent-runtime`) parse
  profiles into `AgentSpec` objects. The SDK never sees the files.
- `.eos-agents/workflow.json` maps configured workflow instance names to `{type, args}`.
  `type` selects the workflow module; `args` is the workflow argument bag. For pursuit,
  `planner` and `worker` are pursuit config fields whose values are agent names:

```json
{
  "pursuit1": {
    "type": "pursuit",
    "args": {
      "planner": "planner",
      "worker": "worker"
    }
  }
}
```

- `AgentFactory` is a composition-root singleton: build it once during app bootstrap from
  the parsed profiles and pass that same object to tools, hooks, `WorkflowHub`, and workflow
  modules. Do **not** re-register or re-parse profiles inside workflow/tool files. On config
  reload, rebuild the app composition root and replace the singleton as a unit.
- The singleton owns agent lookup, launchable-agent policy, and shared startup validation.
  An agent has one identity: `Agent.name`. Workflow config refers to agents only by that
  name; there is no secondary workflow identity and no indirection layer.
  `AgentFactory` constructs an `Agent` by name only at the point the caller has the final
  tools and terminal contract. It does **not** prebuild
  every `Agent`, because SDK `AgentSpec.tools` and `agentOutcomeFn` are fixed at
  `sdk.createAgent(...)` time:

```ts
type AgentName = string;

interface AgentFactory {
  create<T = string>(name: AgentName, init: AgentBuildInit<T>): Agent<T>;
  names(): string[];                 // launchable-by-subagent subset
}

interface AgentBuildInit<T = string> {
  tools: ToolDefinition[];
  agentOutcomeFn?: AgentOutcomeFn<T>;
  hooks?: HookEntry[];
}
```

- There is no `WorkflowAgentFactory`. Workflow modules receive the same `AgentFactory`
  plus their typed `WorkflowInstance`; any agent-valued workflow config field already
  contains an agent name:

```ts
const planner = init.agents.create(init.instance.args.planner, { tools, agentOutcomeFn });
```

- Strictness that used to live in the SDK's profile loader moves here: **pursuit's startup
  validates that the configured planner/worker agents carry an outcome schema** (a
  free-text planner would synthesize garbage transitions). Host policy, host enforcement.

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
export const runSubagent = (agents: AgentFactory) => defineTool({
  name: "run_subagent",
  description: `Launch a subagent. Available: ${agents.names().join(", ")}`,
  input: z.object({ agent: z.string(), prompt: z.string(), wait: z.boolean().default(true) }),
  execute: async (input, ctx) => {
    if (!agents.names().includes(input.agent)) return { error: `unknown agent: ${input.agent}` };
    const agent = agents.create(input.agent, { tools: subagentTools(input.agent) });
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
  name: string;                        // "pursuit" — the workflow module key
  description: string;                 // one line; rides the delegate tool + list_workflows row
  docs: string;                        // the manual; served by read_workflow_definition
  delegatePayload: z.ZodType<I>;       // written once; I and the model-facing schema derive from it
  delegate(payload: I): Promise<WorkflowHandle>;   // async (delegation does I/O);
                                                   //   throw = refuse → in-run tool error
}

interface WorkflowHandle {
  title: string;
  cancel(): void | Promise<void>;      // idempotent; no-op after settlement
  done: Promise<BackgroundTaskOutcome>;// workflow authors the outcome string in its own vocabulary
}

export function defineWorkflow<I>(init: WorkflowDefinition<I>): RegisteredWorkflow;
// mint site: erases I for the hub registry and keeps module/instance metadata typed.

// registration: each workflow exports ONE module from its index.ts
type WorkflowArgs = Record<string, unknown>;

interface WorkflowModule<A extends WorkflowArgs = WorkflowArgs> {
  name: string;                        // module key, e.g. "pursuit"
  args: z.ZodType<A>;                  // validates `.eos-agents/workflow.json` args
  create(init: WorkflowInit<A>): Promise<RegisteredWorkflow>;
}
interface WorkflowInstance<A extends WorkflowArgs = WorkflowArgs> {
  name: string;                        // instance key from workflow.json, e.g. "pursuit1"
  type: string;                        // module key, e.g. "pursuit"
  args: A;                             // workflow-specific config
}
interface WorkflowInit<A extends WorkflowArgs = WorkflowArgs> {
  instance: WorkflowInstance<A>;
  agents: AgentFactory;                // the same singleton from the composition root
}
// For pursuit1, init.instance.args.planner === "planner" and
// init.instance.args.worker === "worker"; pursuit passes those agent names
// directly to init.agents.create(...).
// Workflow-specific storage/scripts remain module-owned (e.g. .eos-agents/pursuit/).
```

`hub.register(module)` resolves the module against `.eos-agents/workflow.json`: every
enabled instance whose `type` equals `module.name` is passed to the module with the same
singleton `AgentFactory`. The composition root therefore contains **no instance-specific
wiring** — one import plus one `register` line per workflow module (§3). With:

```json
{
  "pursuit1": {
    "type": "pursuit",
    "args": {
      "planner": "planner",
      "worker": "worker"
    }
  }
}
```

the hub creates the `pursuit1` workflow instance from the `pursuit` module, and pursuit
builds its planner/worker agents by resolving `init.instance.args` and calling
`init.agents.create(...)`.

The shape mirrors `defineTool` (`delegatePayload`/`delegate` ↔ `input`/`execute`, same
single-source inference); each divergence is a real property of workflows — `docs` because
a workflow needs a manual, and a handle because the work outlives the call.
`WorkflowHandle` is `BackgroundTask` minus the two hub-owned fields
(`toolName`, `onCompletion`); that minus is the hub/workflow ownership line. There is no
`WorkflowRunId`, no `settle`, no per-workflow `onCompletion`, no `silent`: the handle
subsumes the first two, settlement publishing is hub-owned (below), and a delegation is
awaited work — the §5 task rule forbids it being silent.

| Field | Sole consumer |
|---|---|
| `name` | module naming (`pursuit`) · instance delegate tools (`pursuit1_delegate`) |
| `description` | delegate tool description + `list_workflows` row |
| `docs` | `read_workflow_definition` |
| `delegatePayload` | `tools/workflow/delegate-workflow.ts` input schema (SDK-validated pre-`delegate`) |
| `delegate` | `tools/workflow/delegate-workflow.ts` execute handler |
| `title` / `cancel` / `done` | spread into `backgroundTaskSupervisor.register` |

`WorkflowHub` is not a tool package. It owns registration and instance lookup. The
model-visible workflow tools live in `src/tools/workflow/`, one file per tool:

| Tool file | Tool |
|---|---|
| `tools/workflow/list-workflows.ts` | `list_workflows()` — one row per configured instance: name · workflow · description · tool names · ready state |
| `tools/workflow/read-workflow-definition.ts` | `read_workflow_definition(name)` — returns `docs`; unknown name errors with valid instance names |
| `tools/workflow/delegate-workflow.ts` | projects one `${instance.name}_delegate` tool per configured workflow instance |

If a workflow later needs workflow-specific read/action tools, those model-visible files
also live under `tools/workflow/{tool-name}.ts` and consume registered workflow/service
capabilities from the hub. Do not add ad hoc tool definitions under `workflows/`.
**Workflow cancellation needs no tool of its own** — delegation registers a background
task, so `cancel_background_task(taskId)` reaches `handle.cancel()` through the task:

```ts
// tools/workflow/delegate-workflow.ts
const delegateTool = <I>(instance: WorkflowInstance, w: WorkflowDefinition<I>) => defineTool({
  name: `${instance.name}_delegate`,
  description: `${w.description} Before first use: read_workflow_definition("${instance.name}").`,
  input: w.delegatePayload,
  execute: async (input, ctx) => {
    const handle = await w.delegate(input);              // throw → tool error → in-run correction
    const { taskId } = ctx.backgroundTaskSupervisor.register({
      toolName: `workflow:${instance.name}`,
      title: handle.title,
      cancel: () => handle.cancel(),
      done: handle.done,
      onCompletion: (out, { notifier }) =>               // the ONE settlement publisher
        notifier.publish(`workflow ${instance.name} ${out.status}: ${out.outcome}`,
                         { key: `workflow:${instance.name}:${taskId}` }),
    });
    return { output: `delegated ${instance.name} · task ${taskId}` };
  },
});
```

```
model ── pursuit1_delegate(payload) ─▶ hub tool ── w.delegate(payload) ─▶ workflow I/O → handle
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
| `agent-launcher.ts` (`AgentLaunchPort`, `LaunchSettlement`, `LaunchedAgent`) | port implemented by agent-runtime | **deleted** — `launcher.ts` reads agent names from `init.instance.args` and calls `init.agents.create(name, ...).start(...)` directly |
| `PursuitServiceDependencies` | `{db, compose, resolve, launch port}` | `{compose, agents, instance}` — store and pursuit scripts stay pursuit-owned; configured planner/worker agent names are read directly from `.eos-agents/workflow.json` via `WorkflowInstance.args` |
| Submission entry | runtime routes `submit_planner_outcome` payloads into service claim methods | the **same claim/transition methods**, invoked from `onSubmit` closures in `outcome-fns.ts` — transactional, keyed by `ctx.submissionId`; invalid transitions (e.g. refocus in predefined mode) return `{reject}` → in-run correction, budget intact |
| `PursuitAgentSubmissionBinding` | threaded through contracts/engine | deleted (replaced by `onSubmit`) |
| Planner/worker advisory prompts | `@eos/tool/advisory_prompts/` | move here; ride `createAgentOutcomeFn({description})` |
| Death / cancel | runtime-synthesized `LaunchSettlement` | `run.outcome().then(...)` → `out.status !== "completed"` → pursuit synthesizes the Failed work item; `cancel()` additionally calls `handle.interrupt()` on live runs |
| Settlement notification | SDK-rendered session message | pursuit resolves the handle's `done` with an outcome string authored in pursuit vocabulary (e.g. "Failed — leg_2 budget exhausted · outcome.md: <path>"); the hub's single completion publisher delivers it (§6). No `silent` option — a delegation is awaited work |

One attempt, after the change:

```ts
const planner = deps.agents.create(deps.instance.args.planner, {
  tools: plannerTools(),
  agentOutcomeFn: plannerOutcomeFn(this),                  // outcome-fns.ts: name + schema + description +
});                                                        //   onSubmit -> applyPlanSubmission(trx)
const run = planner.start({ messages: composeLaunchContext(tree) });  // context-engine, unchanged
run.outcome().then((out) => this.reconcileAfterRun(attemptId, out));  // death synthesis
// success already mutated state inside onSubmit — reconcile only reads back
```

Discipline carried over unchanged: **`onSubmit` is the only writer; loops reconcile.**
Anyone "fixing a bug" by also mutating at the `outcome()` site reintroduces the dual-write
drift the original design exists to prevent.

## 9. Configuration loading

`.eos-agents/` is the **single configuration root**: profiles, `workflow.json`, hooks,
notification rules, and workflow-owned support files all live under it — nothing registers
from scattered locations. `workflow.json` is the host-owned workflow instance registry:

```json
{
  "pursuit1": {
    "type": "pursuit",
    "args": {
      "planner": "planner",
      "worker": "worker"
    }
  }
}
```

All file I/O for shared host configuration lives in `app/config/` (files moved verbatim
from `agent-runtime`): discovery (`config-root`), parsing (`config-file`), hooks
(`hook-config`), trigger rules (`notification-rules-config`), profiles, and
`workflow.json`. Parsed objects are validated by host-owned schemas (the SDK exports none)
and passed into `createAgentSdk` / `AgentFactory` / `WorkflowHub`. Trigger rules compile
into `turnBoundary` hook entries (`compileNotificationRules`, §3). `recordsDir` is chosen
here. Reload/watch semantics, layering (user vs project), and defaults are host policy.

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
   `src/agents`, `src/tools`, `src/workflows`, and `src/main.ts`; pursuit consumes
   `WorkflowInit.agents` and `WorkflowInit.instance.args` directly (launch port deleted);
   rename the SDK workspace **`eos-agent-core` → `eos-agent-sdk`** (directory, root
   package name, imports).
   *Verify:* leak checks (SDK spec §7); the e2e suite moves with the host
   (`notification-triggers.e2e.ts` is in flight in the current worktree — coordinate).
4. **Hub + workflows** — `WorkflowHub` host-side, pursuit registered as the first
   `WorkflowDefinition`; `.eos-agents/workflow.json` contains `pursuit1` →
   `{type:"pursuit", args:{planner:"planner", worker:"worker"}}`; delegation rides
   background tasks.
   *Verify:* operator can `list_workflows` → `read_workflow_definition("pursuit1")` →
   `pursuit1_delegate`, observe via `list_background_task`, cancel via
   `cancel_background_task`; the settlement notification carries pursuit-authored outcome
   text through the hub's completion publisher.

## 11. Acceptance criteria

- This repo imports only the SDK root package `eos-agent-sdk` (no `@eos/engine`,
  `@eos/tool`, … internals).
- Deleting `src/workflows/pursuit` requires removing exactly one import and one
  `hub.register` line in `app/main.ts` — nothing else; the hub then registers nothing and
  the operator keeps every non-workflow tool. The composition root contains no other
  workflow-specific code.
- Every tool the operator sees is defined in one file under `src/tools/`, including
  workflow tools under `src/tools/workflow/{tool-name}.ts` — `grep` the SDK for tool
  definitions → none.
- `.eos-agents/workflow.json` is the only shared workflow-instance registry; there is no
  `.eos-agents/workflows/<name>` config directory convention.
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

- `AgentFactory.names()` scoping — flat allowlist vs per-agent launchable sets (decide
  with real subagent usage).
- Whether `read_agent_run` should page/filter (records can be large) — tool design, not
  contract.
- Pursuit context-exploration tools (planned in the operator manual §07) — they become
  ordinary host tools under `src/tools/workflow/{tool-name}.ts` over the registered pursuit
  context store; spec separately when prioritized.
- If a second large tool source lands (e.g. MCP servers wrapped as host tool families),
  generalize `list_workflows` / `read_workflow_definition` into group-based discovery and
  revisit the §6 deferral growth path against the ≈10%-of-context threshold.
- Timing of lifting pursuit to a standalone project — trigger is a second host wanting it,
  not before.
