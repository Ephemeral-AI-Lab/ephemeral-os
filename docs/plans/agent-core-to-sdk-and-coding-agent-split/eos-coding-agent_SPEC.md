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
    ├── agents/                       singleton AgentFactory: Agent.name + SDK -> Agent
    │   ├── agent-factory.ts
    │   └── profiles.ts
    ├── tools/                        every model-visible tool: one file per tool
    │   ├── agent/
    │   │   ├── run-subagent.ts       subagent pattern (foreground + background)
    │   │   ├── ask-advisor.ts        advisor consult (ask_advisor)
    │   │   ├── advisory-prompts.ts   terminal tool name -> ask_advisor guidance
    │   │   └── read-agent-run.ts     reads <recordsDir>/<runId>/*.jsonl
    │   ├── background/
    │   │   ├── list-background-task.ts
    │   │   └── cancel-background-task.ts
    │   ├── workflow/
    │   │   ├── list-workflows.ts      generic discovery tool
    │   │   ├── read-workflow-definition.ts
    │   │   ├── delegate-workflow.ts   projects `${instanceName}_delegate`
    │   │   └── index.ts              workflow tool aggregation
    │   └── index.ts                  export aggregation only
    └── workflows/
        ├── registry.ts               WorkflowHub + configured workflow instances
        ├── contract.ts               RegisteredWorkflow · Workflow · defineWorkflow
        └── pursuit/
            ├── service.ts
            ├── pursuit|leg|attempt|plan|work-item/
            │                         {state,transition,context}.ts
            ├── context-engine/
            ├── outcome-fns.ts        planner/worker AgentOutcomeFn builders
            ├── launcher.ts           builds planner/worker Agents via agentFactory()
            ├── workflow.ts           pursuit Workflow over PursuitService
            ├── index.ts              Workflow export; opens pursuit-owned store
            ├── store/                embedded storage (today's @eos/db: schema · rows · migrations)
            └── contracts.ts          entity DTOs; PursuitSettlement; SubmissionBinding deleted
```

## 3. Composition root

```ts
// app/main.ts
import { pursuit } from "./workflows/pursuit/index.js";      // index.ts workflow export

const cfg = loadEosConfig(".eos-agents");                    // ONE root: profiles · hooks · rules · workflows
const sdk = createAgentSdk({
  llmClients: cfg.llmClients,
  hooks: [...cfg.globalHooks,                                // host-validated callbacks
          ...compileNotificationRules(cfg.triggerRules)],    // rule files → turnBoundary entries
  recordsDir: cfg.recordsDir,
});

const hub = new WorkflowHub({                                // §6
  instances: cfg.workflowInstances,                          // parsed workflow.json rows only
});
const availableTools = [
  runSubagent,
  ...workflowTools(hub),                                      // list/read/delegate from tools/workflow/*
  listBackgroundTask,
  cancelBackgroundTask,
  readAgentRun(cfg.recordsDir),
];
const agents = buildAgentFactory(sdk, cfg.profiles, {
  availableTools,
  askAdvisor,
  advisoryPrompts,                                            // terminal tool name -> guidance
});
installAgentFactory(agents);                                  // singleton read by agent tools/workflows
await hub.register(pursuit);                                  // attaches the "pursuit" implementation

const mainOutcomeFn = createAgentOutcomeFn({
  name: "submit_main_outcome",
  schema: MainOutcome,
  description: SUBMIT_MAIN_DESCRIPTION,
});

const operator = agentFactory().create("operator", mainOutcomeFn);
```

"No built-in tools" is literal: every tool name selected by a profile resolves to a
`ToolDefinition` authored in this repo.

## 4. Profiles, Workflow Config, and the AgentFactory

- `.eos-agents/profile/*.md` is the source of truth for agent construction: `name`,
  prompt, LLM client, turn budget, ordinary `allowed_tools`, optional `terminal_tool`,
  and optional context script. Loaders (moved from `agent-runtime`) parse profiles into
  host-owned `AgentProfile` records. The SDK never sees the files.
- `.eos-agents/workflow.json` maps configured workflow instance names to `{type, args}`.
  `type` selects the workflow implementation; `args` is the workflow argument bag. For pursuit,
  `planner` and `worker` are pursuit config fields whose values are `Agent.name`:

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
  the parsed profiles plus the host's plain `availableTools` list, and install it once with
  `installAgentFactory(agents)`. Do not pass it through `WorkflowHub`,
  `Workflow.create`, tool factories, or workflow service constructors. Code that needs to
  start an agent calls `agentFactory()`; tools still use the SDK `ToolCallContext` for
  per-run capabilities. Do **not** re-register or re-parse profiles inside workflow/tool
  files. On config reload, rebuild the app composition root and replace the singleton as a
  unit.
- The singleton owns agent lookup, launchable-agent policy, and shared startup validation.
  An agent has one identity: `Agent.name`. Workflow config refers to agents only by that
  name; there is no secondary workflow identity and no indirection layer.
- `AgentFactory` is the only place that turns a profile into an SDK `AgentSpec`. The
  caller supplies only the runtime terminal binding. In short, the host binding is
  `Agent(profile, agentOutcomeFn)`, exposed as `agentFactory().create(name,
  agentOutcomeFn)`.
- Creation is profile-backed:
  - ordinary model-visible tools come from `profile.allowed_tools`, matched by name
    against `availableTools`;
  - `ask_advisor` is the only parameterized tool name; when the profile allows it, the
    factory calls `askAdvisor(advisoryPrompt)` using the terminal tool name;
  - the terminal tool comes from the provided `agentOutcomeFn`;
  - if `profile.terminal_tool` is present, it must equal
    `agentOutcomeToolName(agentOutcomeFn)`;
  - if `profile.terminal_tool` is absent, the caller must not provide an outcome function.
- The factory does **not** prebuild every `Agent`, because SDK `AgentSpec.tools` and
  `agentOutcomeFn` are fixed at `sdk.createAgent(...)` time:

```ts
interface AgentFactory {
  create<T = string>(name: string, agentOutcomeFn?: AgentOutcomeFn<T>): Agent<T>;
  names(): string[];                 // launchable-by-subagent subset
}
export function installAgentFactory(factory: AgentFactory): void;
export function agentFactory(): AgentFactory;
```

Advisory is not outcome metadata. `AgentOutcomeFn` stays the SDK terminal contract only:
name, description, schema, and `onSubmit`. Advisory is selected by wiring
`askAdvisor(advisoryPrompt)` into an agent's ordinary tool list. The profile controls
whether the tool is present (`allowed_tools` contains `ask_advisor`); the factory derives
the prompt from the terminal tool name through the host advisory prompt registry. Agents
without that tool are not advisory-gated. Agents with that tool must get a matching advisor
pass before their terminal submission; the host enforces that with hooks, not with
outcome-function fields.

- Workflows receive only their parsed args and configured instance name; any agent-valued
  workflow config field already contains `Agent.name`:

```ts
const planner = agentFactory().create(init.args.planner, plannerOutcome(service, target));
```

- Strictness that used to live in the SDK's profile loader moves here: the factory
  validates profile/tool/outcome consistency at agent creation, and **pursuit's startup
  validates that configured planner/worker profiles are terminal profiles** whose
  `terminal_tool` can be bound to pursuit's outcome functions. A free-text planner would
  synthesize garbage transitions. Host policy, host enforcement.

## 5. Tools

All tools are `defineTool` over the SDK's `ToolCallContext` capabilities. There is no
`behavior` metadata; what a tool *does* defines it. Do not define a coding-agent-specific
tool context: the SDK context is fixed and carries only per-call/run capabilities
(`runId`, `toolUseId`, `signal`, `llmMessages`, `backgroundTaskSupervisor`, `notifier`).
Coding-agent services are either captured by a tool factory (`readAgentRun(recordsDir)`,
`workflowTools(hub)`) or read from the composition-root singleton (`agentFactory()`).

There is no separate tool lookup abstraction. The composition root builds a plain
`availableTools: ToolDefinition[]` list and passes it to `buildAgentFactory` once. The
factory uses the existing profile-selection rule: each `profile.allowed_tools` entry must
match one available tool by `tool.name`, except `ask_advisor`, which is intentionally
parameterized. For `ask_advisor`, `AgentFactory` requires a terminal binding, looks up
`advisoryPrompts[agentOutcomeToolName(agentOutcomeFn)]`, and resolves that profile entry as
`askAdvisor(prompt)`. Missing prompt is a startup/config error, not a runtime fallback.
The prompt map is advisor-tool policy and lives next to `ask-advisor.ts`; workflows do not
own advisory prompts.

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

**Subagent pattern** — foreground and background through one public API. The tool is a
static definition; it does not receive an injected `AgentFactory`. The input names the
subagent by `Agent.name`, and execution uses the composition-root `AgentFactory` singleton.
The completion message is fully host-authored in `onCompletion`:

```ts
export const runSubagent = defineTool({
  name: "run_subagent",
  input: z.object({ agent_name: z.string(), prompt: z.string(), wait: z.boolean().default(true) }),
  execute: async (input, ctx) => {
    const agent = agentFactory().create(input.agent_name);
    const run = agent.start({ messages: [{ role: "user", content: input.prompt }] });

    if (input.wait) return { output: renderOutcome(await run.outcome()) };   // foreground

    const { taskId } = ctx.backgroundTaskSupervisor.register({              // background
      toolName: "run_subagent",
      title: `${input.agent_name}: ${input.prompt.slice(0, 60)}`,
      cancel: () => run.interrupt(),
      done: run.outcome().then(toTaskOutcome),
      onCompletion: async (out, { notifier }) => {
        await indexTranscript(run.runId);                                   // side effects welcome
        notifier.publish(out.status === "success"
          ? `subagent ${input.agent_name} done: ${out.outcome}`
          : `subagent ${input.agent_name} ${out.status}: ${out.outcome} — transcript: ${recordsDir}/${run.runId}`,
          { key: `subagent:${run.runId}` });
      },
    });
    return { output: `subagent started · task ${taskId}` };
  },
});
```

**Advisor pattern** — the advisor is always the agent whose `Agent.name` is `advisor`.
The tool definition does not receive a prebuilt advisor agent. It receives only the
advisory prompt for the agent/toolset it is being wired into. Advisory is optional at the
application level: if no configured agent exposes `ask_advisor`, the `advisor` agent does
not need to exist. If any agent exposes `ask_advisor`, startup validation requires an
agent named `advisor` whose `agentOutcomeFn` name is `submit_advisor_outcome`.

```ts
const ADVISOR_AGENT_NAME = "advisor";
const advisorOutcomeFn = createAgentOutcomeFn({
  name: "submit_advisor_outcome",
  description: SUBMIT_ADVISOR_DESCRIPTION,
  schema: AdvisorOutcome,
});

export function askAdvisor(advisoryPrompt: string): ToolDefinition {
  return defineTool({
    name: "ask_advisor",
    input: z.object({
      tool_name: z.string().min(1),
      payload: z.object({}).passthrough().optional(),
    }),
    execute: async (input, ctx) => {
      const advisor = agentFactory().create(ADVISOR_AGENT_NAME, advisorOutcomeFn);
      return { output: renderOutcome(await advisor.start({
        messages: asAdvisorAsk(advisoryPrompt, input),
      }).outcome()) };
    },
  });
}
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
// workflows/contract.ts — coding-agent workflow contracts, NOT SDK contracts
interface RegisteredWorkflow<I = unknown> {
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

export function defineWorkflow<I>(init: RegisteredWorkflow<I>): RegisteredWorkflow;
// mint site: erases I for the hub registry.

// registration: each workflow exports ONE implementation from its index.ts
type WorkflowArgs = Record<string, unknown>;

interface Workflow<A extends WorkflowArgs = WorkflowArgs> {
  type: string;                        // workflow key, e.g. "pursuit"
  args: z.ZodType<A>;                  // validates `.eos-agents/workflow.json` args
  create(init: {
    instanceName: string;              // instance key from workflow.json, e.g. "pursuit1"
    args: A;                           // parsed workflow-specific config
  }): Promise<RegisteredWorkflow>;
}
// For pursuit1, init.args.planner === "planner" and init.args.worker === "worker";
// pursuit passes those Agent.name values
// directly to agentFactory().create(...).
// Workflow-specific storage/scripts remain workflow-owned (e.g. .eos-agents/pursuit/).
```

Only `Workflow` is a real public registration shape here. Separate named
instance/init interfaces are unnecessary: the instance is just a row in the parsed config
file, and the init value is only a parameter bag used once. `WorkflowHub` keeps config rows
internal and passes the three useful values directly to `workflow.create`.

`new WorkflowHub({ instances })` only gives the hub the configured rows from
`.eos-agents/workflow.json`; it does **not** register any workflow implementation. A
separate `hub.register(workflow)` call resolves an implementation against those rows:
every enabled config row whose `type` equals `workflow.type` is passed to the workflow.
The composition root therefore contains **no instance-specific wiring** — one import plus
one `register` line per workflow (§3).
With:

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

the hub creates the `pursuit1` workflow instance from the `pursuit` workflow, and pursuit
builds its planner/worker agents by resolving `init.args` and calling `agentFactory().create(...)`.

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
| `Workflow.type` | hub registration against config rows whose `type` matches |
| `Workflow.args` | hub validation of `.eos-agents/workflow.json` args |
| `instanceName` | instance delegate tools (`pursuit1_delegate`) · list/read rows · task keys |
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
| `tools/workflow/delegate-workflow.ts` | projects one `${instanceName}_delegate` tool per configured workflow instance |

If a workflow later needs workflow-specific read/action tools, those model-visible files
also live under `tools/workflow/{tool-name}.ts` and consume registered workflow/service
capabilities from the hub. Do not add ad hoc tool definitions under `workflows/`.
**Workflow cancellation needs no tool of its own** — delegation registers a background
task, so `cancel_background_task(taskId)` reaches `handle.cancel()` through the task:

```ts
// tools/workflow/delegate-workflow.ts
const delegateTool = <I>(instanceName: string, w: RegisteredWorkflow<I>) => defineTool({
  name: `${instanceName}_delegate`,
  description: `${w.description} Before first use: read_workflow_definition("${instanceName}").`,
  input: w.delegatePayload,
  execute: async (input, ctx) => {
    const handle = await w.delegate(input);              // throw → tool error → in-run correction
    const { taskId } = ctx.backgroundTaskSupervisor.register({
      toolName: `workflow:${instanceName}`,
      title: handle.title,
      cancel: () => handle.cancel(),
      done: handle.done,
      onCompletion: (out, { notifier }) =>               // the ONE settlement publisher
        notifier.publish(`workflow ${instanceName} ${out.status}: ${out.outcome}`,
                         { key: `workflow:${instanceName}:${taskId}` }),
    });
    return { output: `delegated ${instanceName} · task ${taskId}` };
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
`RegisteredWorkflow` whose `delegate` proxies over a wire; the hub, the projections, and
every profile stay untouched.

## 7. Hooks and the advisor gate

The advisor-validates-submission machinery that used to be SDK behavior becomes a host
tool plus a host hook. Ordering does the work:

```
model -> ask_advisor(tool_name, payload) -> advisor agent named "advisor" returns verdict
model -> terminal submission            -> PreToolUse hook verifies a matching pass
                                      -> only then `onSubmit`
```

Advisor guidance is not injected through any outcome function. It is ordinary tool policy:
`askAdvisor(advisoryPrompt)` closes over the prompt, and `ask_advisor(tool_name, payload)`
starts the agent named `advisor` with initial messages containing the caller transcript,
that prompt, and the exact `{tool_name, payload}` target. The advisor's agent outcome is generic
`submit_advisor_outcome`; it only returns the pass/fail verdict.

The hook never runs an advisor itself; the engine is headless. The hook rule is installed
only for profiles whose ordinary tool list contains `ask_advisor`. `AgentFactory` detects
that profile tool and, when the agent has an `agentOutcomeFn`, appends a pre-tool hook for
that terminal tool name. A rejection mutates nothing and burns no terminal budget; the
model corrects in-run.

```ts
function advisoryHooksFor(
  profile: AgentProfile,
  agentOutcomeFn?: AgentOutcomeFn<unknown>,
): HookEntry[] {
  if (!agentOutcomeFn || !profile.allowed_tools.includes("ask_advisor")) {
    return [];
  }
  return [requireAdvisoryPass({ toolName: agentOutcomeToolName(agentOutcomeFn) })];
}

function requireAdvisoryPass({ toolName }: { toolName: string }): HookEntry {
  return {
    event: "preToolUse",
    matcher: { toolName },
    run: (payload) => {
      const latest = latestMatchingAskAdvisorVerdict(
        transcriptPathFor(payload.runId),
        { tool_name: payload.toolName, payload: payload.input },
      );
      return latest.kind === "pass"
        ? { decision: "passthrough" }
        : { decision: "deny", reason: advisoryFailureReason(latest) };
    },
  };
}
```

The gate sees `ToolCallFacts` only — the submission payload, never the conversation (SDK
spec §4.3). Submission schemas must therefore be self-contained enough to vet on their
own: the advisor judges *what* the model submits, not how it got there.

Failure policy is explicit host policy: if a matching `ask_advisor` review is missing,
fails, returns an invalid verdict, targets another payload, or returns `fail`, the hook
denies the terminal submission with model-visible feedback. If the agent is not wired with
`ask_advisor`, the hook is not installed and no `advisor` agent setup is required.

## 8. Pursuit as a workflow

Pursuit moves wholesale; its state machines, transitions, context-engine, tree, db schema,
and reconcile logic are unchanged. The changes are confined to the launch/settle edge:

| Pursuit internal | Before (in eos-agent-core) | After (here) |
|---|---|---|
| `agent-launcher.ts` (`AgentLaunchPort`, `LaunchSettlement`, `LaunchedAgent`) | port implemented by agent-runtime | **deleted** — `launcher.ts` reads `Agent.name` values from parsed workflow args and calls `agentFactory().create(name, outcomeFn).start(...)` directly |
| `PursuitServiceDependencies` | `{db, compose, resolve, launch port}` | `{compose, instanceName, args}` — store and pursuit scripts stay pursuit-owned; configured planner/worker `Agent.name` values are read directly from `.eos-agents/workflow.json` args |
| Submission entry | runtime threads `PursuitAgentSubmissionBinding` into terminal tools | the same transition methods, invoked from `onSubmit` closures in `outcome-fns.ts` over a launch-scoped submission target — transactional, keyed by `ctx.submissionId`; invalid transitions (e.g. refocus in predefined mode) return `{reject}` → in-run correction, budget intact |
| `PursuitAgentSubmissionBinding` | threaded through contracts/engine | deleted (replaced by `onSubmit`) |
| Planner/worker advisory prompts | `@eos/tool/advisory_prompts/` | move to host advisor-tool policy (`tools/agent/advisory-prompts.ts`), registered by terminal tool name; `AgentFactory` feeds the matching prompt to `askAdvisor(prompt)` when the profile allows `ask_advisor` |
| Death / cancel | runtime-synthesized `LaunchSettlement` | `run.outcome().then(...)` → `out.status !== "completed"` → pursuit synthesizes the Failed work item; `cancel()` additionally calls `handle.interrupt()` on live runs |
| Settlement notification | SDK-rendered session message | pursuit resolves the handle's `done` with an outcome string authored in pursuit vocabulary (e.g. "Failed — leg_2 budget exhausted · outcome.md: <path>"); the hub's single completion publisher delivers it (§6). No `silent` option — a delegation is awaited work |

At the launch queue edge, `claim` means "this queued row was atomically claimed for launch."
Do not leak that word into the outcome API. `outcome-fns.ts` closes over a narrower
submission target: the concrete plan/work-item being settled. Launch-queue fencing, such as
the current `launch_token` used to skip stale post-commit launches, stays internal to the
launcher. Outcome submission idempotency and stale-settlement rejection belong in the
service transition using `ctx.submissionId`, `ctx.runId`, and current DB state.

```ts
interface PlannerSubmissionTarget {
  pursuitId: PursuitId;
  attemptId: AttemptId;
  planId: PlanId;
  queueId: number;
}

interface WorkerSubmissionTarget {
  pursuitId: PursuitId;
  attemptId: AttemptId;
  workItemKey: string;
  workItemId: WorkItemId;
  queueId: number;
}

export function plannerOutcome(
  service: PursuitService,
  target: PlannerSubmissionTarget,
): AgentOutcomeFn<PlannerOutcomePayload> {
  return createAgentOutcomeFn({
    name: "submit_planner_outcome",
    description: SUBMIT_PLANNER_DESCRIPTION,
    schema: PlannerOutcomePayloadSchema,
    onSubmit: async (payload, ctx) => {
      const result = await service.submitPlannerOutcome({
        target,
        payload,
        submissionId: ctx.submissionId,
        runId: ctx.runId,
      });
      return result.ok ? { accept: payload } : { reject: result.error };
    },
  });
}

export function workerOutcome(
  service: PursuitService,
  target: WorkerSubmissionTarget,
): AgentOutcomeFn<WorkerOutcomePayload> {
  return createAgentOutcomeFn({
    name: "submit_worker_outcome",
    description: SUBMIT_WORKER_DESCRIPTION,
    schema: WorkerOutcomePayloadSchema,
    onSubmit: async (payload, ctx) => {
      const result = await service.submitWorkerOutcome({
        target,
        payload,
        submissionId: ctx.submissionId,
        runId: ctx.runId,
      });
      return result.ok ? { accept: payload } : { reject: result.error };
    },
  });
}
```

One attempt, after the change:

```ts
const planner = agentFactory().create(
  deps.args.planner,
  plannerOutcome(this, target),                            // onSubmit -> applyPlanSubmission(trx)
);
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
   `agentFactory()`, `instanceName`, and parsed workflow `args` directly (launch port deleted);
   rename the SDK workspace **`eos-agent-core` → `eos-agent-sdk`** (directory, root
   package name, imports).
   *Verify:* leak checks (SDK spec §7); the e2e suite moves with the host
   (`notification-triggers.e2e.ts` is in flight in the current worktree — coordinate).
4. **Hub + workflows** — `WorkflowHub` host-side, pursuit registered as the first
   `Workflow`; `.eos-agents/workflow.json` contains `pursuit1` →
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
- Advisor enforcement demonstrably runs **before** `onSubmit`: `ask_advisor` starts the
  agent named `advisor`, the pre-tool hook verifies a matching pass in the transcript, and
  denial leaves no DB row, spends no terminal budget, and returns model-visible feedback.
- Advisory setup is optional: if no configured agent exposes `ask_advisor`, no `advisor`
  agent is required at startup.
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
