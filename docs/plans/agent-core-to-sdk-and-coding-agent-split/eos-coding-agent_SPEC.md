# eos-coding-agent - Host Application Specification

- **Status:** Draft for review
- **Date:** 2026-06-13
- **Depends on:**
  - `docs/plans/agent-core-to-sdk-and-coding-agent-split/eos-agent-sdk_SPEC.md`
  - `docs/plans/agent-core-rust-to-typescript-migration/phase-05.3-pursuit_leg_attempt_SPEC.md`
- **Scope:** The host application that composes `eos-agent-sdk` into the coding-agent
  product. It owns profiles, config files, all tools, the WorkflowHub, pursuit as the
  first registered workflow, advisor/subagent patterns, hooks, notification rules,
  pursuit scripts, and the composition root.

## 1. Source-of-truth Alignment

This document is a split spec, not a second migration vocabulary. It must preserve two
source-of-truth decisions:

1. `eos-agent-sdk` is mechanism-only. It knows only agent, run, outcome, tool,
   background task, notification, and hook. It ships zero tools and no workflow concepts.
2. The active orchestration product vocabulary from Phase 05.3 is pursuit, leg, and
   attempt. The old product-facing workflow/iteration/focus/deferred/archive names must
   not return through this host split.

The WorkflowHub is still required. It is a host infrastructure registry for loading,
discovering, documenting, and delegating configured long-running workflows. The hub does
not make "workflow" a pursuit domain term, and it does not rename pursuit's public
contract back to generic workflow names.

| Surface | Active term in this spec | Notes |
| --- | --- | --- |
| SDK dependency | `eos-agent-sdk` root package only | No deep imports such as `@eos/tool`, `@eos/engine`, or `@eos/pursuit`. |
| Generic host registry | `WorkflowHub` | Required host-owned registry. Not an SDK concept. |
| First registered workflow | `pursuit` | Concrete product vocabulary follows Phase 05.3. |
| Delegate tool | `delegate_pursuit` | Do not reintroduce `delegate_workflow`. |
| Context scripts | `.eos-agents/pursuit/scripts/` | Do not use `.eos-agents/workflow/scripts/` as an active script root. |
| Profile context script field | `pursuit_context_script` | Do not use `workflow_context_script`. |
| Context paths | `pursuit_<id>/leg_<id>/.../superseded/` | Do not render `workflow_<id>`, `iteration_<id>`, `focus.md`, `deferred_goal.md`, or `archived/`. |
| SDK background capability | `BackgroundTaskSupervisor` | This split follows the SDK rename from background session to background task. |

## 2. Boundary Summary

`eos-coding-agent` owns vocabulary and policy. The SDK owns reusable loop
mechanics.

```text
eos-coding-agent
  imports only eos-agent-sdk root package
  owns:
    .eos-agents/ profile, workflow, hook, notification, and pursuit config
    every model-visible tool
    AgentFactory over host profiles
    WorkflowHub and registered workflow instances
    pursuit domain state, store, scripts, context projection, and terminal outcomes
    advisor/subagent host patterns
    composition root
```

A second host, for example `eos-research-agent`, should be a sibling application. It may
reuse `eos-agent-sdk` and the WorkflowHub pattern, but it does not inherit
`eos-coding-agent` tools, profiles, prompts, or pursuit policy by default. A second host
that also wants pursuit is the trigger to consider lifting pursuit out of this repository
into a shared package.

## 3. Target Layout

```text
eos-coding-agent/
  package.json
  .eos-agents/
    profile/
      operator.md
      planner.md
      worker.md
      advisor.md
    workflow.json
    hooks.json
    notification_rules.json
    pursuit/
      scripts/
        planner.cjs
        worker.cjs
        variable_reference_map.cjs
      context/                         machine-written pursuit context mirror
      pursuit.sqlite                   or configured store path
  src/
    main.ts                            composition root
    config/
      config-root.ts
      config-file.ts
      hook-config.ts
      notification-rules-config.ts
      profile-loader.ts
      workflow-config.ts
      pursuit-config.ts
    agents/
      agent-factory.ts
      profiles.ts
      advisory-prompts.ts
    tools/
      agent/
        run-subagent.ts
        ask-advisor.ts
        read-agent-run.ts
      background/
        list-background-task.ts
        cancel-background-task.ts
      workflow/
        list-workflows.ts
        read-workflow-definition.ts
      pursuit/
        delegate-pursuit.ts
      index.ts
    workflows/
      hub.ts                           WorkflowHub
      contract.ts                      WorkflowModule, RegisteredWorkflow
      index.ts
  packages/
    workflows/
      pursuit/
        package.json
        src/
          index.ts                     exports the pursuit WorkflowModule
          service.ts
          agent-launcher.ts
          pursuit-tree.ts
          pursuit-context.ts
          pursuit/
            state.ts
            transition.ts
            context.ts
          leg/
            state.ts
            transition.ts
            context.ts
          attempt/
            state.ts
            transition.ts
            context.ts
          plan/
            state.ts
            transition.ts
          work-item/
            state.ts
            transition.ts
            context.ts
          context-engine/
            composer.ts
            input.ts
            projection/
              listing.ts
              paths.ts
              resolve.ts
              mirror.ts
          store/
            schema.ts
            migrations.ts
          contracts.ts
```

The top-level `src/workflows` folder owns generic hub contracts. The concrete pursuit
implementation lives under `packages/workflows/pursuit` so the former `@eos/pursuit`
boundary remains visible after it leaves the SDK workspace.

## 4. Composition Root

The application bootstrap builds every singleton once and wires only public SDK values.

```ts
import {
  createAgentOutcomeFn,
  createAgentSdk,
} from "eos-agent-sdk";
import { agentFactory, buildAgentFactory, installAgentFactory } from "./agents/agent-factory.js";
import { loadEosConfig } from "./config/config-file.js";
import { cancelBackgroundTask } from "./tools/background/cancel-background-task.js";
import { listBackgroundTask } from "./tools/background/list-background-task.js";
import { askAdvisor } from "./tools/agent/ask-advisor.js";
import { readAgentRun } from "./tools/agent/read-agent-run.js";
import { runSubagent } from "./tools/agent/run-subagent.js";
import { delegatePursuit } from "./tools/pursuit/delegate-pursuit.js";
import { readWorkflowDefinition } from "./tools/workflow/read-workflow-definition.js";
import { listWorkflows } from "./tools/workflow/list-workflows.js";
import { WorkflowHub } from "./workflows/hub.js";
import { pursuitWorkflow } from "../packages/workflows/pursuit/src/index.js";

const cfg = loadEosConfig(".eos-agents");

const sdk = createAgentSdk({
  llmClients: cfg.llmClients,
  hooks: [
    ...cfg.globalHooks,
    ...compileNotificationRules(cfg.notificationRules),
  ],
  recordsDir: cfg.recordsDir,
});

const hub = new WorkflowHub({
  instances: cfg.workflowInstances,
});
await hub.register(pursuitWorkflow);

const availableTools = [
  runSubagent,
  listBackgroundTask,
  cancelBackgroundTask,
  readAgentRun(cfg.recordsDir),
  listWorkflows(hub),
  readWorkflowDefinition(hub),
  delegatePursuit(hub),
];

const agents = buildAgentFactory(sdk, cfg.profiles, {
  availableTools,
  askAdvisor,
  advisoryPrompts: cfg.advisoryPrompts,
});
installAgentFactory(agents);

const mainOutcomeFn = createAgentOutcomeFn({
  name: "submit_main_outcome",
  description: SUBMIT_MAIN_DESCRIPTION,
  schema: MainOutcome,
});

const operator = agentFactory().create("operator", mainOutcomeFn);
```

Rules:

- The composition root imports `eos-agent-sdk` and local host modules only.
- The WorkflowHub is initialized before tool assembly so workflow tools can read it.
- Pursuit is registered through the hub. Do not wire pursuit directly into the operator
  tool list while bypassing the hub.
- The SDK receives parsed objects and callbacks. File discovery, profile loading,
  subprocess wrapping, and schema validation stay in this host.

## 5. Configuration

`.eos-agents/` is the single config root.

```text
.eos-agents/
  profile/*.md
  workflow.json
  hooks.json
  notification_rules.json
  pursuit/scripts/*.cjs
  pursuit/context/
```

### 5.1 Profiles

Profiles are host-owned records. The SDK never reads profile files.

Required profile fields:

- `name`
- LLM client reference
- system prompt
- `allowed_tools`
- optional `terminal_tool`
- optional `pursuit_context_script` for planner and worker profiles

`pursuit_context_script` resolves under `.eos-agents/pursuit/scripts/`. Startup must reject
profiles that still use `workflow_context_script` for active runtime wiring.

### 5.2 Workflow Instances

`workflow.json` is the WorkflowHub instance registry. It is generic host config, not
pursuit context state.

```json
{
  "pursuit": {
    "type": "pursuit",
    "args": {
      "planner": "planner",
      "worker": "worker",
      "store": ".eos-agents/pursuit/pursuit.sqlite",
      "context_root": ".eos-agents/pursuit/context"
    }
  }
}
```

The `planner` and `worker` values are `Agent.name` values. The hub validates the row with
the registered workflow module's `args` schema, then passes the parsed values to
`WorkflowModule.create`.

V1 should configure one pursuit instance named `pursuit`. If multiple pursuit instances
are later required, add an explicit tool naming policy. Do not fall back to
`delegate_workflow` or automatic `${instance}_delegate` names without a spec update.

## 6. AgentFactory

`AgentFactory` is the only place that turns a host profile into an SDK `AgentSpec`.

```ts
interface AgentFactory {
  create<T = string>(name: string, agentOutcomeFn?: AgentOutcomeFn<T>): Agent<T>;
  names(): string[];
}

export function buildAgentFactory(
  sdk: AgentSdk,
  profiles: AgentProfileRegistry,
  deps: {
    availableTools: readonly ToolDefinition[];
    askAdvisor: (prompt: string) => ToolDefinition;
    advisoryPrompts: ReadonlyMap<string, string>;
  },
): AgentFactory;

export function installAgentFactory(factory: AgentFactory): void;
export function agentFactory(): AgentFactory;
```

Creation rules:

- `profile.allowed_tools` selects ordinary tools by `ToolDefinition.name`.
- `ask_advisor` is the only parameterized tool name. If a profile allows it, the factory
  requires a terminal outcome function, resolves the terminal tool name, looks up its
  advisory prompt, and injects `askAdvisor(prompt)`.
- If `profile.terminal_tool` is present, the caller must provide an `AgentOutcomeFn` with
  the same tool name.
- If `profile.terminal_tool` is absent, the caller must not provide an `AgentOutcomeFn`;
  the agent runs in SDK text termination mode.
- Planner and worker startup validation is pursuit-owned: the configured profiles must be
  terminal profiles whose terminal tools can bind to pursuit planner/worker outcome
  functions.

The singleton is read by host tools and pursuit launch code through `agentFactory()`.
Do not pass the factory through `ToolCallContext`, `WorkflowHub`, or workflow service
constructors.

## 7. Tool Surface

Every model-visible tool is authored in this repository with the SDK `defineTool`
contract. The SDK ships none.

| Folder | Tools | Owner notes |
| --- | --- | --- |
| `tools/agent/` | `run_subagent`, `ask_advisor`, `read_agent_run` | Host agent patterns over SDK handles and records. |
| `tools/background/` | `list_background_task`, `cancel_background_task` | Thin projections over `ctx.backgroundTaskSupervisor`. |
| `tools/workflow/` | `list_workflows`, `read_workflow_definition` | Generic WorkflowHub discovery and docs. |
| `tools/pursuit/` | `delegate_pursuit` | Concrete pursuit delegate tool from Phase 05.3. |

There is no coding-agent-specific tool context. Tool code receives only the SDK
`ToolCallContext`:

```ts
interface ToolCallContext {
  runId: AgentRunId;
  toolUseId: ToolUseId;
  signal: AbortSignal;
  llmMessages: readonly Message[];
  backgroundTaskSupervisor: BackgroundTaskSupervisor;
  notifier: Notifier;
}
```

Host state enters tools by closure (`readAgentRun(recordsDir)`, `delegatePursuit(hub)`) or
through host singletons (`agentFactory()`). Do not add `agents`, `workflow`, or
`advisorPromptFor` fields to SDK context.

### 7.1 Background Task Tools

```ts
export const listBackgroundTask = defineTool({
  name: "list_background_task",
  description: "List this run's active background tasks.",
  input: z.object({}),
  execute: async (_input, ctx) => ({
    output: renderBackgroundTaskRows(ctx.backgroundTaskSupervisor.list()),
  }),
});

export const cancelBackgroundTask = defineTool({
  name: "cancel_background_task",
  description: "Cancel one active background task in this run.",
  input: z.object({ task_id: z.string().min(1) }),
  execute: async (input, ctx) => ({
    output: (await ctx.backgroundTaskSupervisor.cancel(input.task_id))
      ? "cancelled"
      : "not found",
  }),
});
```

This split intentionally uses SDK background-task vocabulary. Current Phase 05.3
implementation evidence still mentions background session type `"pursuit"` because it
predates the SDK split. In this host spec, the pursuit delegate registers a background
task with a `toolName` such as `pursuit:pursuit`; no model-facing background-session API
survives.

### 7.2 Subagent Tool

`run_subagent` starts another profile by `Agent.name`. It supports foreground and
background execution through one tool. Background execution registers exactly one
background task, and the task's `onCompletion` is the only completion publisher.

```ts
export const runSubagent = defineTool({
  name: "run_subagent",
  description: "Run another configured agent.",
  input: z.object({
    agent_name: z.string().min(1),
    prompt: z.string().min(1),
    wait: z.boolean().default(true),
  }),
  execute: async (input, ctx) => {
    const child = agentFactory().create(input.agent_name);
    const run = child.start({
      messages: [{ role: "user", content: input.prompt }],
    });

    if (input.wait) {
      return { output: renderAgentOutcome(await run.outcome()) };
    }

    const { taskId } = ctx.backgroundTaskSupervisor.register({
      toolName: "run_subagent",
      title: `${input.agent_name}: ${input.prompt.slice(0, 80)}`,
      cancel: () => run.interrupt(),
      done: run.outcome().then(toBackgroundTaskOutcome),
      onCompletion: (out, { notifier }) => {
        notifier.publish(renderSubagentCompletion(input.agent_name, run.runId, out), {
          key: `subagent:${run.runId}`,
        });
      },
    });

    return { output: `subagent started: ${taskId}` };
  },
});
```

### 7.3 Advisor Tool and Gate

Advisor is a host pattern, not SDK metadata. `ask_advisor` is injected only into profiles
that allow it.

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
    description: "Ask the configured advisor to review a terminal submission.",
    input: z.object({
      tool_name: z.string().min(1),
      payload: z.object({}).passthrough(),
    }),
    execute: async (input) => {
      const advisor = agentFactory().create(ADVISOR_AGENT_NAME, advisorOutcomeFn);
      const run = advisor.start({
        messages: asAdvisorReviewMessages(advisoryPrompt, input),
      });
      return { output: renderAgentOutcome(await run.outcome()) };
    },
  });
}
```

The terminal gate is a `preToolUse` hook installed by `AgentFactory` for profiles whose
ordinary tool list includes `ask_advisor`.

```ts
function advisoryHooksFor(
  profile: AgentProfile,
  agentOutcomeFn?: AgentOutcomeFn<unknown>,
): HookEntry[] {
  if (!agentOutcomeFn || !profile.allowed_tools.includes("ask_advisor")) {
    return [];
  }

  return [
    requireAdvisoryPass({
      toolName: agentOutcomeToolName(agentOutcomeFn),
    }),
  ];
}
```

The hook checks transcript records for the latest exact pass matching
`{ tool_name, payload }`. It never starts an advisor itself. A denial reaches the live
model as a tool error, mutates no pursuit state, and consumes no attempt budget.

Pre/post hooks receive `ToolCallFacts` only, so terminal submission payloads must be
self-contained enough for advisor review.

## 8. WorkflowHub

WorkflowHub is a host-owned registry. It is deliberately retained because the coding agent
needs a common place for workflow discovery, docs, readiness, and future workflow
registration. It is not part of the SDK.

```ts
type WorkflowArgs = Record<string, unknown>;

interface WorkflowModule<A extends WorkflowArgs = WorkflowArgs> {
  type: string;
  args: z.ZodType<A>;
  create(init: {
    instanceName: string;
    args: A;
  }): Promise<RegisteredWorkflow>;
}

interface RegisteredWorkflow<I = unknown> {
  instanceName: string;
  type: string;
  description: string;
  docs: string;
  delegateToolName: string;
  delegatePayload: z.ZodType<I>;
  delegate(input: I): Promise<WorkflowHandle>;
}

interface WorkflowHandle {
  title: string;
  cancel(): void | Promise<void>;
  done: Promise<BackgroundTaskOutcome>;
}

interface WorkflowHub {
  register(module: WorkflowModule): Promise<void>;
  list(): WorkflowListRow[];
  get(instanceName: string): RegisteredWorkflow | undefined;
  getByDelegateTool(toolName: string): RegisteredWorkflow | undefined;
  requireType(type: string): RegisteredWorkflow;
}
```

Registration flow:

```text
load .eos-agents/workflow.json
  -> new WorkflowHub({ instances })
  -> hub.register(pursuitWorkflow)
  -> hub validates rows whose type == "pursuit"
  -> hub stores one RegisteredWorkflow for the configured pursuit instance
```

The hub does not auto-mint delegate tools. A workflow declares its
`delegateToolName`; pursuit declares `delegate_pursuit`. This avoids reintroducing
`delegate_workflow` while still keeping generic hub discovery.

Generic workflow tools:

- `list_workflows()` returns configured instances, type, description, delegate tool name,
  and readiness/error state.
- `read_workflow_definition(name)` returns the registered workflow docs for that instance.
  It mutates nothing.

Concrete delegate tools:

- `delegate_pursuit` lives under `tools/pursuit/` and resolves the registered pursuit
  workflow through the hub.
- Future workflows may add their own concrete delegate tools or a separately specified
  generic delegate pattern. Do not infer that pattern from pursuit.

## 9. Pursuit Registration

Pursuit is the first `WorkflowModule`.

```ts
export const pursuitWorkflow: WorkflowModule<PursuitWorkflowArgs> = {
  type: "pursuit",
  args: PursuitWorkflowArgsSchema,
  async create(init) {
    const service = await openPursuitService({
      instanceName: init.instanceName,
      plannerAgentName: init.args.planner,
      workerAgentName: init.args.worker,
      storePath: init.args.store,
      contextRoot: init.args.context_root,
      scriptsRoot: ".eos-agents/pursuit/scripts",
    });

    return {
      instanceName: init.instanceName,
      type: "pursuit",
      description: "Delegate a multi-leg coding pursuit.",
      docs: renderPursuitManual(),
      delegateToolName: "delegate_pursuit",
      delegatePayload: CreatePursuitInputSchema,
      delegate: (input) => service.createPursuit(input),
    };
  },
};
```

`delegate_pursuit` is a host adapter over the registered pursuit handle:

```ts
export function delegatePursuit(hub: WorkflowHub): ToolDefinition {
  return defineTool({
    name: "delegate_pursuit",
    description: DELEGATE_PURSUIT_DESCRIPTION,
    input: CreatePursuitInputSchema,
    execute: async (input, ctx) => {
      const workflow = hub.requireType("pursuit");
      const handle = await workflow.delegate(input);
      const { taskId } = ctx.backgroundTaskSupervisor.register({
        toolName: `pursuit:${workflow.instanceName}`,
        title: handle.title,
        cancel: () => handle.cancel(),
        done: handle.done,
        onCompletion: (out, { notifier }) => {
          notifier.publish(
            `pursuit ${workflow.instanceName} ${out.status}: ${out.outcome}`,
            { key: `pursuit:${workflow.instanceName}:${taskId}` },
          );
        },
      });
      return { output: `pursuit delegated: ${taskId}` };
    },
  });
}
```

The background task completion message is the single settlement publisher. Pursuit authors
the outcome text in pursuit vocabulary; the hub/tool adapter publishes it.

## 10. Pursuit Domain Contract

This host split must preserve Phase 05.3 behavior.

### 10.1 Creation Input

```ts
type CreatePursuitInput =
  | {
      pursuit_goal: string;
      leg_goal_mode?: "dynamic";
      leg_goals?: undefined;
    }
  | {
      pursuit_goal: string;
      leg_goal_mode?: "predefined";
      leg_goals: readonly [string, ...string[]];
    };
```

Dynamic mode:

- The first leg inherits `pursuit_goal`.
- Later legs inherit the previous successful leg's `next_leg_goal`.
- A planner may omit `leg_goal`, submit `leg_goal` to refocus, and submit successor-only
  `next_leg_goal`.

Predefined mode:

- Each leg uses `leg_goals[n]`.
- Planners must not submit `leg_goal` or `next_leg_goal`.
- Non-final successful legs advance to the next predefined leg goal.

### 10.2 Planner Payload

```ts
const PlannerWorkItemSpecSchema = z.object({
  id: z.string().min(1),
  agent_name: z.string().min(1),
  title: z.string().min(1),
  spec: z.string().min(1),
  depends_on: z.array(z.string()).default([]),
});

const PlannerOutcomePayloadSchema = z.object({
  summary: z.string().min(1),
  leg_goal: z.string().min(1).optional(),
  next_leg_goal: z.string().min(1).optional(),
  work_items: z.array(PlannerWorkItemSpecSchema).min(1),
});
```

Validation rules:

- Omitted `leg_goal` means keep the current effective leg goal.
- `next_leg_goal` without `leg_goal` is valid in dynamic mode.
- `leg_goal` without `next_leg_goal` refocuses the leg and clears standing successor
  scope.
- There is no payload shape for clearing `next_leg_goal` without a refocusing `leg_goal`.
- Predefined mode rejects both `leg_goal` and `next_leg_goal` as correctable in-run
  payload errors.
- Work items use `title`, `spec`, and `depends_on`; old `description`,
  `work_item_spec`, and `needs` are rejected.

### 10.3 Dependency and Scheduler Rules

`depends_on` is a hard dependency, not a context hint.

- A work item can launch only when every direct dependency is terminal `Success`.
- A running work item is never converted to `Blocked`.
- Failed or blocked work items propagate `Blocked` only to not-yet-launched dependents.
- Unrelated running or launchable work items continue after a sibling fails.
- An attempt closes `Failed` only after block propagation leaves no work item `Running`
  or `NotStarted`.

`failure_reasons.md` is list-shaped and includes planner/context failures plus failed or
blocked work items.

### 10.4 Context Universe

The rendered path universe is:

```text
pursuit_<id>/
  goal.md
  outcome.md
  leg_<id>/
    leg_goal.md
    next_leg_goal.md
    outcome.md
    attempt_<id>/
      plan_summary.md
      failure_reasons.md
      outcome.md
      work_item_<id>/
        title.md
        spec.md
        summary.md
        outcome.md
    superseded/
      attempt_<id>/
        leg_goal.md
        next_leg_goal.md
        plan_summary.md
        failure_reasons.md
        outcome.md
        work_item_<id>/
          title.md
          spec.md
          summary.md
          outcome.md
```

Rules:

- `Plan` remains DB/launch/submission state and never reappears as a rendered context
  folder.
- `leg_goal.md` exists at leg creation and includes provenance.
- `next_leg_goal.md` appears only when an effective successor exists.
- Search excludes `superseded/` unless explicitly scoped there.
- The mirror root is `.eos-agents/pursuit/context`.

## 11. Pursuit Launch and Submission

The SDK split removes `AgentLaunchPort` / `LaunchSettlement` as an SDK-facing seam.
Pursuit launches planner and worker agents by using the host `AgentFactory` and SDK
handles directly.

```ts
const planner = agentFactory().create(
  plannerAgentName,
  plannerOutcome(service, target),
);

const run = planner.start({
  messages: composePlannerInitialMessages(snapshot),
});

run.outcome().then((outcome) => {
  service.reconcilePlannerRun(target, outcome);
});
```

`onSubmit` is the only successful-submission writer:

```ts
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
        runId: ctx.runId,
        submissionId: ctx.submissionId,
      });
      return result.ok ? { accept: payload } : { reject: result.error };
    },
  });
}
```

Rules:

- `submissionId` is the idempotency key for pursuit transitions.
- Correctable payload errors return `{ reject }` to the live model and consume no attempt
  budget.
- Death, cancellation, and max-turn failures never call `onSubmit`; pursuit observes them
  at `run.outcome()` and synthesizes the appropriate failed or cancelled settlement.
- Do not also mutate pursuit state from the `outcome()` observer after a successful
  `onSubmit`. The observer reconciles and performs death synthesis only.

## 12. Hooks and Notification Rules

Host hooks are callback `HookEntry` values passed to `createAgentSdk`.

- `preToolUse` gates terminal submissions with advisor pass checks.
- `postToolUse` is available for host policy replacement of ordinary tool results.
- `turnBoundary` is where notification rules run and publish through the notifier.

Notification rule files stay host config:

```text
.eos-agents/notification_rules.json
```

The host compiles them into `turnBoundary` callbacks. The SDK never parses rule files and
never publishes notification content by itself.

## 13. Migration Sequencing

1. **SDK extraction:** flatten `eos-agent-core` into `eos-agent-sdk`; remove built-in
   tools, profile/config loading, trigger-rule evaluation, and pursuit from the SDK
   public surface.
2. **Host bootstrap:** create `eos-coding-agent`, move config/profile loaders, hook script
   wrapping, notification rule compilation, records readers, agent tools, and background
   task tools into the host.
3. **WorkflowHub:** add the host hub and keep it in the composition root. Register pursuit
   through the hub, not as direct operator-only wiring.
4. **Pursuit move:** move Phase 05.3 pursuit into
   `packages/workflows/pursuit`; preserve `pursuit/leg/attempt` vocabulary, scripts,
   context mirror, planner/worker payloads, and scheduler semantics.
5. **Tool renames:** expose `delegate_pursuit`; keep `list_workflows` and
   `read_workflow_definition` as generic hub tools; do not expose `delegate_workflow`.
6. **Vocabulary cleanup:** run identifier scans against active TypeScript source,
   profiles, and pursuit scripts to prevent old product terms from leaking back in.

## 14. Acceptance Criteria

- `eos-coding-agent` imports only the `eos-agent-sdk` root package from the SDK.
- `WorkflowHub` remains a first-class host component with `register`, `list`, and docs
  lookup behavior.
- Removing pursuit requires deleting its package import plus one `hub.register(...)` call;
  generic hub tools still exist and report no ready pursuit workflow.
- Every model-visible tool is defined under `src/tools/`; the SDK contains no tool
  implementations.
- `delegate_pursuit` is the concrete pursuit delegate tool. `delegate_workflow` is absent
  from active host tools.
- `.eos-agents/workflow.json` is the only generic workflow instance registry.
- `.eos-agents/pursuit/scripts/` is the only active pursuit initial-message script root.
- Profiles use `pursuit_context_script`; active runtime wiring rejects
  `workflow_context_script`.
- Pursuit context paths use `pursuit_<id>/leg_<id>/superseded/` and never render
  `workflow_<id>`, `iteration_<id>`, `focus.md`, `deferred_goal.md`, `archived/`, or
  `/plan_`.
- Planner payloads use `leg_goal`, `next_leg_goal`, and work-item
  `title`/`spec`/`depends_on`.
- `Plan` remains DB/launch/submission state only; it is not a rendered context entity.
- Advisor enforcement runs before `onSubmit`; denial mutates no pursuit state and consumes
  no attempt budget.
- Background work uses SDK `BackgroundTaskSupervisor`; host tools are
  `list_background_task` and `cancel_background_task`.
- Pursuit settlement notifications are published exactly once by the background task
  `onCompletion` handler.
- The following hygiene checks have no active-source matches outside historical docs or
  explicit migration notes:

```bash
rg -n "delegate_workflow|workflow_context_script|workflow_context|workflow_<id>|iteration_<id>|deferred_goal|archived/|focus\\.md|description\\.md|work_item_spec|\\bneeds\\b" eos-coding-agent .eos-agents/profile .eos-agents/pursuit/scripts
rg -n "@eos/(tool|engine|agent-runtime|pursuit)|packages/workflow|\\.eos-agents/workflow/scripts" eos-coding-agent
git diff --check -- docs/plans/agent-core-to-sdk-and-coding-agent-split
```

## 15. Open Questions

- Whether `list_workflows` should include future disabled/config-error rows or only ready
  rows. V1 should include config-error rows so operators can diagnose startup state.
- Whether future non-pursuit workflows choose concrete delegate tools like
  `delegate_research` or a generic delegate tool. Do not decide this from pursuit alone.
- Whether pursuit should remain a local package forever or move to a shared project when a
  second host needs it.
- Whether `read_agent_run` needs paging before extraction, since SDK records can grow large.
