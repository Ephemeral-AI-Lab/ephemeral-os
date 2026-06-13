# eos-coding-agent - Host Application Specification

- **Status:** Draft for review
- **Date:** 2026-06-13
- **Depends on:**
  - `docs/plans/agent-core-to-sdk-and-coding-agent-split/eos-agent-sdk_SPEC.md`
  - `docs/plans/agent-core-rust-to-typescript-migration/phase-05.3-pursuit_leg_attempt_SPEC.md`
- **Scope:** The host application that composes `eos-agent-sdk` into the coding-agent
  product. It owns profiles, config files, all tools, the WorkflowHub, pursuit as the
  first registered workflow, advisor/subagent patterns, hooks, pursuit scripts, and the
  composition root.

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
| SDK dependency | `eos-agent-sdk` root package only | No deep imports past the SDK package's `exports` field. |
| Generic host registry | `WorkflowHub` | Required host-owned registry. Not an SDK concept. |
| First registered workflow | `pursuit` | Concrete product vocabulary follows Phase 05.3. |
| Workflow delegation tool | `delegate_workflow` | One generic hub tool. Do not create per-workflow or per-instance delegate tools. |
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
    .eos-agents/ profile, workflow, hook, and pursuit config
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
      subagent.md                    launchable only when another profile lists it
    llm_clients.json
    workflow.json
    hooks.json
    hooks/                           .cjs command hook scripts
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
      profile-loader.ts
      workflow-config.ts
    agents/
      agent-factory.ts
      profiles.ts
    tools/
      agent/
        run-subagent.ts
        ask-advisor.ts
        read-agent-run.ts
      background/
        list-background-tasks.ts
        cancel-background-task.ts
      workflow/
        list-workflows.ts
        read-workflow-definition.ts
        delegate-workflow.ts
      index.ts
    workflows/
      hub.ts                           WorkflowHub
      contract.ts                      WorkflowModule, WorkflowInstanceConfig, WorkflowHandle
      index.ts
      pursuit/
        index.ts                       exports the pursuit WorkflowModule
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

The `src/workflows` folder owns both generic hub contracts and host-local workflow
implementations. Pursuit lives under `src/workflows/pursuit/`; it is not a separate
package and it does not need a standalone config loader.

## 4. Composition Root

The application bootstrap builds each composition-root value once and wires only public SDK
values.

```ts
import {
  createAgentOutcomeFn,
  createAgentSdk,
} from "eos-agent-sdk";
import { buildAgentFactory } from "./agents/agent-factory.js";
import { loadEosConfig } from "./config/config-file.js";
import { WorkflowHub } from "./workflows/hub.js";
import { pursuitWorkflow } from "./workflows/pursuit/index.js";

const cfg = loadEosConfig(".eos-agents");

const sdk = createAgentSdk({
  llmClients: cfg.llmClients,
  hooks: cfg.hooks,
  recordsDir: cfg.recordsDir,
});

const hub = new WorkflowHub({
  configs: cfg.workflowInstanceConfigs,
  modules: [pursuitWorkflow],
});

const agents = buildAgentFactory(sdk, cfg.profiles, cfg.recordsDir, hub);

const mainOutcomeFn = createAgentOutcomeFn({
  name: "submit_main_outcome",
  description: SUBMIT_MAIN_DESCRIPTION,
  schema: MainOutcome,
});

const operator = agents.create("operator", mainOutcomeFn, MAIN_ADVISOR_PROMPT);
```

Rules:

- The composition root imports `eos-agent-sdk` and local host modules only.
- `new WorkflowHub({ configs, modules })` does construction only: it pairs each instance
  config with the module whose `type` matches and validates `args` synchronously. Unknown
  types and invalid args are startup errors. Opening workflow state (for example the
  pursuit store) happens lazily inside the module on first delegation.
- Advisory prompts are caller-supplied at `create` time beside the outcome function
  (`MAIN_ADVISOR_PROMPT` lives beside `SUBMIT_MAIN_DESCRIPTION`); there is no advisory
  prompt registry.
- The WorkflowHub is initialized before `AgentFactory` construction so profiles can bind
  workflow tools to only the workflow instances they declare.
- Pursuit is registered through the hub. Do not wire pursuit directly into the operator
  tool list while bypassing the hub.
- The SDK receives parsed objects and callbacks. File discovery, profile loading,
  subprocess wrapping, and schema validation stay in this host.

## 5. Configuration

`.eos-agents/` is the single config root.

```text
.eos-agents/
  profile/*.md
  llm_clients.json
  workflow.json
  hooks.json
  hooks/*.cjs
  pursuit/scripts/*.cjs
  pursuit/context/
```

### 5.1 Profiles

Profiles are host-owned records. The SDK never reads profile files.

Profile fields:

- `name`
- `description`
- `llm_client_id`
- `max_turns`
- system prompt (the markdown body)
- `allowed_tools`
- optional `workflows`
- optional `subagents`
- optional `terminal_tool`
- optional `pursuit_context_script` for planner and worker profiles

`pursuit_context_script` resolves under `.eos-agents/pursuit/scripts/`. Startup must reject
profiles that still use `workflow_context_script` for active runtime wiring.

`workflows` is profile policy, not hub policy. It lists workflow instance names from
`.eos-agents/workflow.json` that this profile may inspect or delegate:

```yaml
name: operator
workflows:
  - pursuit
subagents:
  - subagent
allowed_tools:
  - list_workflows
  - read_workflow_definition
  - delegate_workflow
  - run_subagent
```

Startup validation must reject any profile `workflows` entry that is not a configured
workflow instance. If a profile exposes `delegate_workflow` or `read_workflow_definition`, it
must declare at least one workflow instance. `list_workflows` is generated from the same
profile list, so two agents can have different visible workflows even though they share one
host hub.

`subagents` is also profile policy. It lists profile names that this profile may launch
through `run_subagent`. Startup validation must reject unknown names and names of
terminal profiles, because a subagent launch supplies no outcome function. If a profile exposes
`run_subagent`, it must declare at least one subagent name. The target profile is still a
normal profile; there is no target role field, subagent registry, or kind classifier.

`subagent.md` is a normal profile file. It becomes a subagent only from the caller's
allow-list:

```yaml
name: subagent
allowed_tools:
  - read_agent_run
```

### 5.2 Workflow Instances

`workflow.json` declares the workflow instances available at startup. It is generic host
config, not profile policy and not pursuit context state.

```json
{
  "pursuit": {
    "type": "pursuit",
    "args": {
      "planner": "planner",
      "worker": "worker",
      "store": ".eos-agents/pursuit/pursuit.sqlite",
      "context_root": ".eos-agents/pursuit/context",
      "scripts_root": ".eos-agents/pursuit/scripts",
      "default_max_attempts": 2
    }
  }
}
```

The `planner` and `worker` values are agent names (profile names; they become
`AgentSpec.name`). The hub validates each instance config with the matching module's
`args` schema at construction; the parsed values reach the module's `delegate` as
`instance.args`. `scripts_root` and `default_max_attempts` are schema-defaulted to the
values shown and may be omitted.

Registering an instance here does not expose it to every agent. Exposure happens only when
an agent profile lists that instance in `workflows` and selects one or more workflow tools
in `allowed_tools`.

V1 should configure one pursuit instance named `pursuit`. If multiple pursuit instances
are later required, they are still delegated through `delegate_workflow`; the workflow
instance name selects which registered schema validates the payload. Do not add
per-workflow or per-instance delegate tools.

## 6. AgentFactory

`AgentFactory` is the only place that turns a host profile into an SDK `AgentSpec`.

```ts
interface AgentFactory {
  create<T = string>(
    name: string,
    agentOutcomeFn?: AgentOutcomeFn<T>,
    advisoryPrompt?: string,
  ): Agent<T>;
}

export function buildAgentFactory(
  sdk: AgentSdk,
  profiles: AgentProfileRegistry,
  recordsDir: string,
  workflowHub: WorkflowHub,
): AgentFactory;

```

Creation rules:

- `profile.allowed_tools` in the markdown is the source of truth for tool selection.
- `AgentFactory` resolves each selected name against the host's built-in tool definitions
  in `src/tools/`; no extra bootstrap tool wiring is required.
- For workflow tools, `AgentFactory` first asks the hub for a view scoped to
  `profile.workflows`, then builds `list_workflows`, `read_workflow_definition`, and
  `delegate_workflow` from that view. It must not pass the whole hub registry to model-visible
  tools.
- `ask_advisor` is an ordinary host tool selected by `allowed_tools`. Its advisory prompt
  is supplied by the caller of `create`, at the same call site as the outcome function;
  there is no advisory prompt registry or bootstrap parameterization.
- Startup/profile validation must still reject a profile that exposes `ask_advisor`
  without a terminal tool, because the advisor gate protects terminal submissions.
- If the profile lists `ask_advisor`, the caller must supply both `agentOutcomeFn` and
  `advisoryPrompt`; the factory wires `askAdvisor(agents, advisoryPrompt)` into the
  toolset and installs the `preToolUse` gate on `profile.terminal_tool`. Supplying an
  `advisoryPrompt` for a profile that does not list `ask_advisor` is a creation error.
- If `profile.terminal_tool` is present, the caller must provide an `AgentOutcomeFn` with
  the same tool name (checked with the SDK's `agentOutcomeToolName`).
- If `profile.terminal_tool` is absent, the caller must not provide an `AgentOutcomeFn`;
  the agent runs in SDK text termination mode.
- Planner and worker startup validation is pursuit-owned: the configured profiles must be
  terminal profiles whose terminal tools can bind to pursuit planner/worker outcome
  functions.
- `run_subagent` target validation is caller-profile-owned: the tool's input schema
  enumerates that profile's `subagents` list, and startup validation guarantees each entry
  resolves to a known, non-terminal profile. Profiles not listed by the caller are not
  launchable through that caller's `run_subagent` tool.

`AgentFactory` is a composition-root value, not a singleton. Tools that need to launch
agents are tool factories closed over it, and workflows that launch agents receive it in
`WorkflowDelegateContext` when `delegate_workflow` runs. Do not put `AgentFactory` on SDK
`ToolCallContext`.

## 7. Tool Surface

Every model-visible tool is authored in this repository with the SDK `defineTool`
contract. The SDK ships none.

| Folder | Tools | Owner notes |
| --- | --- | --- |
| `tools/agent/` | `run_subagent`, `ask_advisor`, `read_agent_run` | Host agent patterns over SDK handles and records. |
| `tools/background/` | `list_background_tasks`, `cancel_background_task` | Thin projections over `ctx.backgroundTaskSupervisor`. |
| `tools/workflow/` | `list_workflows`, `read_workflow_definition`, `delegate_workflow` | Generic WorkflowHub discovery, docs, and delegation action. |

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

Host resource state enters tool definitions inside `AgentFactory` assembly, using the
`recordsDir`, `workflowHub`, and `AgentFactory` values from bootstrap. Do not add
`agents`, `workflow`, or advisory-prompt fields to SDK context.

### 7.1 Background Task Tools

```ts
export const listBackgroundTasks = defineTool({
  name: "list_background_tasks",
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
predates the SDK split. In this host spec, `delegate_workflow` registers the returned
workflow handle as a background task with a `toolName` such as `workflow:pursuit`;
no model-facing background-session API
survives.

### 7.2 Subagent Tool

`run_subagent` starts another profile by agent name, but only when the caller profile
lists that target in `subagents`. It supports foreground and background execution through
one tool. Background execution registers exactly one background task, and the task's
`onCompletion` is the only completion publisher.

```ts
export function runSubagent(
  agents: AgentFactory,
  subagents: readonly [string, ...string[]],
): ToolDefinition {
  return defineTool({
    name: "run_subagent",
    description: "Run another configured agent.",
    input: z.object({
      agent_name: z.enum(subagents),
      prompt: z.string().min(1),
      wait: z.boolean().default(true),
    }),
    execute: async (input, ctx) => {
      const child = agents.create(input.agent_name);
      const run = child.start({
        messages: [{ role: "user", content: input.prompt }],
      });
      ctx.signal.addEventListener("abort", () => run.interrupt());

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
}
```

### 7.3 Advisor Tool and Gate

Advisor is a host pattern, not SDK metadata. Profiles opt in by listing `ask_advisor` in
their markdown `allowed_tools`.

```ts
const ADVISOR_AGENT_NAME = "advisor";

const AdvisorVerdict = z.object({
  verdict: z.enum(["pass", "fail"]),
  tool_name: z.string().min(1),
  payload: z.object({}).passthrough(),
  reason: z.string().min(1),
});

const advisorOutcomeFn = createAgentOutcomeFn({
  name: "submit_advisor_outcome",
  description: SUBMIT_ADVISOR_DESCRIPTION,
  schema: AdvisorVerdict,
});

export function askAdvisor(
  agents: AgentFactory,
  advisoryPrompt: string,
): ToolDefinition {
  return defineTool({
    name: "ask_advisor",
    description: "Ask the configured advisor to review a terminal submission.",
    input: z.object({
      tool_name: z.string().min(1),
      payload: z.object({}).passthrough(),
    }),
    execute: async (input, ctx) => {
      const advisor = agents.create(ADVISOR_AGENT_NAME, advisorOutcomeFn);
      const run = advisor.start({
        messages: [
          userText(renderCallerMessages(ctx.llmMessages)),
          userText(`${advisoryPrompt} Verify against the exact target below.\n${canonicalJson(input)}`),
        ],
      });
      ctx.signal.addEventListener("abort", () => run.interrupt());
      return { output: renderAdvisorVerdict(await run.outcome()) };
    },
  });
}
```

Because the prompt is bound per launch, an agent's `ask_advisor` always carries the review
standard for its own terminal tool. `input.tool_name` stays in the input because the
gate's exact-match contract needs the model to state the tool and payload it intends to
submit. `renderAdvisorVerdict` embeds the verdict as canonical JSON, because the gate
re-parses it from the run's records.

The terminal gate is a `preToolUse` hook installed by `AgentFactory` for profiles whose
ordinary tool list includes `ask_advisor`.

```ts
function advisoryHooksFor(profile: AgentProfile, recordsDir: string): HookEntry[] {
  if (!profile.allowed_tools.includes("ask_advisor")) {
    return [];
  }

  return [
    requireAdvisoryPass({
      toolName: profile.terminal_tool,
      recordsDir,
    }),
  ];
}
```

The hook reads the run's records (`messages.jsonl`) for the latest exact pass matching
`{ tool_name, payload }` under `canonicalJson` deep-equality. It never starts an advisor
itself. A denial reaches the live
model as a tool error, mutates no pursuit state, and consumes no attempt budget.

Pre/post hooks receive `ToolCallFacts` only, so terminal submission payloads must be
self-contained enough for advisor review.

## 8. WorkflowHub

WorkflowHub is a host-owned registry. It is deliberately retained because the coding agent
needs a common place for workflow discovery, docs, readiness, and future workflow
registration. It is not part of the SDK.

```ts
type WorkflowArgs = Record<string, unknown>;

interface WorkflowInstanceConfig {
  instanceName: string;               // key of the .eos-agents/workflow.json entry
  type: string;
  args: unknown;                      // validated by the matching module's args schema
}

interface WorkflowDelegateContext {
  agents: AgentFactory;
}

interface WorkflowModule<A extends WorkflowArgs = WorkflowArgs, I = unknown> {
  type: string;
  args: z.ZodType<A>;
  description: string;
  docs: string;
  delegatePayload: z.ZodType<I>;
  delegate(
    instance: { instanceName: string; args: A },
    input: I,
    ctx: WorkflowDelegateContext,
  ): Promise<WorkflowHandle>;
}

interface WorkflowHandle {
  title: string;
  cancel(): void | Promise<void>;
  done: Promise<BackgroundTaskOutcome>;
}

// new WorkflowHub({ configs, modules }) pairs each config with the module whose
// type matches and validates args synchronously; it performs no I/O and holds no
// workflow state.
interface WorkflowHub {
  workflowNames(): readonly string[];
  forProfile(instanceNames: readonly string[]): ProfileWorkflowView;
}

interface ProfileWorkflowView {
  list(): WorkflowListRow[];
  readDefinition(instanceName: string): string;
  delegateWorkflowInputSchema(): z.ZodType<DelegateWorkflowInput>;
  delegate(
    input: DelegateWorkflowInput,
    ctx: WorkflowDelegateContext,
  ): Promise<WorkflowHandle>;
}
```

Registration flow:

```text
load .eos-agents/workflow.json
  -> new WorkflowHub({ configs, modules: [pursuitWorkflow] })
       pairs each instance config with the module whose type matches
       validates args synchronously; no I/O, no workflow state
load .eos-agents/profile/operator.md
  -> profile.workflows == ["pursuit"]
  -> hub.forProfile(["pursuit"])
  -> workflow tools see and delegate only that scoped view
```

The hub does not auto-mint tools. There is one model-visible delegation tool,
`delegate_workflow`. Its input schema is generated from the current profile's workflow
view, not from every configured instance:

```ts
type DelegateWorkflowInput =
  | { name: "pursuit"; payload: CreatePursuitInput }
  // one union arm per workflow instance declared by this profile
```

Implementation rule: `ProfileWorkflowView.delegateWorkflowInputSchema()` builds a
discriminated union on `name`. Each arm uses the exact `delegatePayload` schema from the
module backing the instance selected by the profile. The SDK therefore validates `delegate_workflow`
like any other tool before `execute(...)` runs: workflows outside the profile and
malformed payloads return normal in-run tool errors and register no background task.

Generic workflow tools:

- `list_workflows()` returns only the current profile's workflow instances, including
  type, description, and readiness/error state.
- `read_workflow_definition(name)` returns docs only for instances in the current
  profile's workflow view. It mutates nothing.
- `delegate_workflow(name, payload)` uses the generated per-instance schema, delegates to
  the selected profile-visible workflow, and registers the returned handle as one SDK
  background task.

There are no `delegate_pursuit`, `delegate_research`, or `${workflow}_delegate` tools.
Adding a workflow means registering a `WorkflowModule`; it does not add a tool.

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
      delegatePayload: CreatePursuitInputSchema,
      delegate: (input, ctx) => service.createPursuit(input, { agents: ctx.agents }),
    };
  },
};
```

`delegate_workflow` is the single host adapter over profile-visible workflow handles:

```ts
export function delegateWorkflow(
  workflows: ProfileWorkflowView,
  agents: AgentFactory,
): ToolDefinition {
  return defineTool({
    name: "delegate_workflow",
    description: DELEGATE_WORKFLOW_DESCRIPTION,
    input: workflows.delegateWorkflowInputSchema(),
    execute: async (input, ctx) => {
      const handle = await workflows.delegate(input, { agents });
      const { taskId } = ctx.backgroundTaskSupervisor.register({
        toolName: `workflow:${input.name}`,
        title: handle.title,
        cancel: () => handle.cancel(),
        done: handle.done,
        onCompletion: (out, { notifier }) => {
          notifier.publish(
            `workflow ${input.name} ${out.status}: ${out.outcome}`,
            { key: `workflow:${input.name}:${taskId}` },
          );
        },
      });
      return { output: `workflow delegated: ${taskId}` };
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
      leg_goals?: undefined;
    }
  | {
      pursuit_goal: string;
      leg_goals: readonly [string, ...string[]];
    };
```

Mode is derived from payload shape: omitting `leg_goals` starts dynamic mode; providing
non-empty `leg_goals` starts predefined mode. Do not expose or require a separate
`leg_goal_mode` field in `delegate_workflow`.

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
const planner = agents.create(
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

## 12. Hooks

Host hooks are callback `HookEntry` values passed to `createAgentSdk`.

- `preToolUse` gates terminal submissions with advisor pass checks.
- `postToolUse` is available for host policy replacement of ordinary tool results.
- `turnBoundary` hooks may publish reminders or status messages through the notifier.

Hook files stay host config:

```text
.eos-agents/hooks.json
.eos-agents/hooks/*.cjs
```

`hook-config.ts` loads every configured hook event into `cfg.hooks`. There is no separate
notification-rule file, loader, compiler, or vocabulary. The SDK never parses hook config
and never publishes notification content by itself.

## 13. Migration Sequencing

1. **SDK extraction:** flatten `eos-agent-core` into `eos-agent-sdk`; remove built-in
   tools, profile/config loading, trigger-rule evaluation, and pursuit from the SDK
   public surface.
2. **Host bootstrap:** create `eos-coding-agent`, move config/profile loaders, hook script
   wrapping, records readers, agent tools, and background task tools into the host.
3. **WorkflowHub:** add the host hub and keep it in the composition root. Construct it
   with available workflow instances and host-owned workflow modules, including
   `pursuitWorkflow`.
4. **Pursuit move:** move Phase 05.3 pursuit into `src/workflows/pursuit/`; preserve
   `pursuit/leg/attempt` vocabulary, scripts, context mirror, planner/worker payloads,
   and scheduler semantics.
5. **Workflow tools:** expose only the generic hub tools `list_workflows`,
   `read_workflow_definition`, and `delegate_workflow`. Do not expose `delegate_pursuit`
   or any other per-workflow/per-instance delegate tool. Build these tools from each
   profile's `workflows` list, not from the full hub registry.
6. **Vocabulary cleanup:** run identifier scans against active TypeScript source,
   profiles, and pursuit scripts to prevent old product terms from leaking back in.

## 14. Acceptance Criteria

- `eos-coding-agent` imports only the `eos-agent-sdk` root package from the SDK.
- `WorkflowHub` remains a first-class host component with construction-time workflow
  module registration and profile-scoped workflow views for list, docs lookup, and
  delegation behavior.
- Removing pursuit requires deleting its workflow import plus one constructor entry from
  `workflows: [...]`; profiles that still reference `pursuit` fail startup validation
  until their `workflows` lists are updated.
- Every model-visible tool is defined under `src/tools/`; the SDK contains no tool
  implementations.
- `delegate_workflow` is the only workflow delegation tool. No active host tool is named
  `delegate_pursuit` or `${workflow}_delegate`.
- `delegate_workflow` exposes a generated discriminated-union schema over the current
  profile's workflow instances, so each workflow keeps its own payload validation without
  receiving its own tool or becoming visible to every agent.
- `list_workflows` is dynamically loaded from the current profile's `workflows` list; it
  must not return workflow instances that the active profile did not declare.
- `.eos-agents/workflow.json` is the only generic workflow instance registry.
- `.eos-agents/pursuit/scripts/` is the only active pursuit initial-message script root.
- Profiles use `pursuit_context_script`; active runtime wiring rejects
  `workflow_context_script`.
- Profiles do not define a role/kind discriminator; active profile validation rejects any
  legacy profile-kind field if present.
- `run_subagent` validates `agent_name` from the caller profile's `subagents` list. The
  default config includes a normal `profile/subagent.md` profile that is launchable only
  when another profile lists `subagent` under `subagents`.
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
rg -n "agent[_-]kind|delegate_pursuit|[a-zA-Z0-9_-]+_delegate|workflow_context_script|workflow_context|workflow_<id>|iteration_<id>|deferred_goal|archived/|focus\\.md|description\\.md|work_item_spec|\\bneeds\\b" eos-coding-agent .eos-agents/profile .eos-agents/pursuit/scripts
rg -n "@eos/(tool|engine|agent-runtime|pursuit)|packages/workflow|\\.eos-agents/workflow/scripts" eos-coding-agent
git diff --check -- docs/plans/agent-core-to-sdk-and-coding-agent-split
```

## 15. Open Questions

- Whether `list_workflows` should include future disabled/config-error rows or only ready
  rows. V1 should include config-error rows so operators can diagnose startup state.
- Whether pursuit should remain a local package forever or move to a shared project when a
  second host needs it.
- Whether `read_agent_run` needs paging before extraction, since SDK records can grow large.
