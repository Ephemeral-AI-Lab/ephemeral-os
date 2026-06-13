import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { createAgentSdk, type AgentOutcomeFn } from "eos-agent-sdk";
import {
  ScriptedLlmClient,
  assistantMessage,
  complete,
  toolUseBlock,
  type ScriptedTurn,
} from "eos-agent-sdk/testkit";

import {
  pursuitIdFrom,
  type InitialUserMessage,
  type PlannerOutcomePayload,
  type PursuitId,
  type PursuitSettlement,
  type SubmissionResult,
  type WorkerOutcomePayload,
} from "../../../src/workflows/pursuit/contracts/pursuit.js";
import {
  createPursuitDatabase,
  type PursuitDb,
} from "../../../src/workflows/pursuit/db/index.js";

import type { PursuitAgents } from "../../../src/workflows/pursuit/agent-launcher.js";
import {
  defaultComposeLaunchContext,
  type ComposeLaunchContext,
} from "../../../src/workflows/pursuit/context-engine/composer.js";
import {
  loadPursuitTree,
  type PursuitTree,
} from "../../../src/workflows/pursuit/pursuit-tree.js";
import {
  PursuitService,
  type PursuitServiceDependencies,
} from "../../../src/workflows/pursuit/service.js";

// --- a directive queue: each scripted provider turn waits for one directive ------

type Directive = { events: ReturnType<typeof complete>[] } | { error: Error };

class DirectiveQueue {
  readonly #buffer: Directive[] = [];
  readonly #waiters: ((d: Directive) => void)[] = [];

  push(directive: Directive): void {
    const waiter = this.#waiters.shift();
    if (waiter) waiter(directive);
    else this.#buffer.push(directive);
  }

  take(): Promise<Directive> {
    const buffered = this.#buffer.shift();
    if (buffered) return Promise.resolve(buffered);
    return new Promise((resolve) => this.#waiters.push(resolve));
  }
}

function pendingTurn(queue: DirectiveQueue): ScriptedTurn {
  return async function* () {
    const directive = await queue.take();
    if ("error" in directive) throw directive.error;
    for (const event of directive.events) {
      await Promise.resolve();
      yield event;
    }
  };
}

// --- scripted launch: one real SDK run, driven by terminal submissions -----------

export interface ScriptedLaunch {
  agentName: string;
  kind: "planner" | "worker";
  messages: readonly InitialUserMessage[];
  submitPlanner(payload: PlannerOutcomePayload): Promise<SubmissionResult>;
  submitWorker(payload: WorkerOutcomePayload): Promise<SubmissionResult>;
  settle(settlement: { status: "failed" | "cancelled" }): void;
}

let toolUseCounter = 0;

function scriptedAgents(launches: ScriptedLaunch[]): PursuitAgents {
  return {
    create<T>(agentName: string, outcome: AgentOutcomeFn<T>) {
      const queue = new DirectiveQueue();
      const client = new ScriptedLlmClient(
        Array.from({ length: 64 }, () => pendingTurn(queue)),
      );
      const sdk = createAgentSdk({
        llmClients: { only: { client, model: "m" } },
      });
      const agent = sdk.createAgent<T>({
        name: agentName,
        llm: "only",
        systemPrompt: "",
        tools: [],
        agentOutcomeFn: outcome,
        maxTurns: 64,
      });
      const kind: "planner" | "worker" =
        agentName === "planner" ? "planner" : "worker";
      return {
        start(input) {
          const run = agent.start(input);
          // Pump terminal-tool completions to awaiting submit() calls. A reject
          // surfaces as an is_error completion; an accept as a clean completion.
          // run_finished is the fallback: when an attempt failure aborts the
          // pursuit and self-interrupts this just-accepted run, no terminal
          // completion is delivered, but onSubmit already wrote the state, so a
          // pending submit resolves ok.
          const results: SubmissionResult[] = [];
          const waiters: ((result: SubmissionResult) => void)[] = [];
          let finished = false;
          const deliver = (result: SubmissionResult): void => {
            const waiter = waiters.shift();
            if (waiter) waiter(result);
            else results.push(result);
          };
          void (async () => {
            for await (const event of run.events()) {
              if (
                event.type === "tool_execution_completed" &&
                (event.name === "submit_planner_outcome" ||
                  event.name === "submit_worker_outcome")
              ) {
                deliver(event.is_error ? { ok: false, error: event.output } : { ok: true });
              } else if (event.type === "run_finished") {
                finished = true;
                while (waiters.length > 0) deliver({ ok: true });
              }
            }
          })().catch(() => undefined);
          const awaitResult = (): Promise<SubmissionResult> => {
            const ready = results.shift();
            if (ready) return Promise.resolve(ready);
            if (finished) return Promise.resolve({ ok: true });
            return new Promise((resolve) => waiters.push(resolve));
          };
          const drive = (
            toolName: string,
            payload: PlannerOutcomePayload | WorkerOutcomePayload,
          ): Promise<SubmissionResult> => {
            toolUseCounter += 1;
            queue.push({
              events: [
                complete(
                  assistantMessage(
                    toolUseBlock(`tu-${String(toolUseCounter)}`, toolName, payload),
                  ),
                ),
              ],
            });
            return awaitResult();
          };
          launches.push({
            agentName,
            kind,
            messages: input.messages as readonly InitialUserMessage[],
            submitPlanner: (payload) => drive("submit_planner_outcome", payload),
            submitWorker: (payload) => drive("submit_worker_outcome", payload),
            settle: () => {
              queue.push({ error: new Error("run died without a submission") });
            },
          });
          return run;
        },
      };
    },
  };
}

// --- harness ---------------------------------------------------------------------

export interface HarnessPursuit {
  pursuit_id: PursuitId;
  cancel(reason?: string): Promise<void>;
  settle(): Promise<PursuitSettlement>;
}

export interface Harness {
  db: PursuitDb;
  service: PursuitService;
  agents: PursuitAgents;
  launches: ScriptedLaunch[];
  contextRoot: string;
  create(
    pursuitGoal?: string,
    options?: { maxAttempts?: number; legGoals?: readonly [string, ...string[]] },
  ): Promise<HarnessPursuit>;
  tree(pursuitId: PursuitId): Promise<PursuitTree>;
}

export function harness(
  overrides: Partial<PursuitServiceDependencies> & {
    compose?: ComposeLaunchContext;
  } = {},
): Harness {
  const db = createPursuitDatabase(":memory:");
  const contextRoot = mkdtempSync(join(tmpdir(), "eos-pursuit-ctx-"));
  const launches: ScriptedLaunch[] = [];
  const agents = scriptedAgents(launches);

  let maxAttempts = 2;
  const service = new PursuitService({
    db,
    compose: overrides.compose ?? defaultComposeLaunchContext,
    contextRoot,
    plannerAgentName: "planner",
    isRegisteredWorkerAgent: (name) => name === "worker",
    logMirrorFailure: () => undefined,
    get defaultMaxAttempts() {
      return maxAttempts;
    },
    ...overrides,
  });

  return {
    db,
    service,
    agents,
    launches,
    contextRoot,
    create: async (pursuitGoal = "ship the feature", options = {}) => {
      maxAttempts = options.maxAttempts ?? 2;
      const handle = await service.createPursuit(
        {
          pursuit_goal: pursuitGoal,
          ...(options.legGoals !== undefined && {
            leg_goals: [...options.legGoals] as [string, ...string[]],
          }),
        },
        { agents },
      );
      return {
        pursuit_id: pursuitIdFrom(handle.pursuitId.replace(/^pursuit_/, "")),
        cancel: (reason) => handle.cancel(reason),
        settle: () => handle.done,
      };
    },
    tree: async (pursuitId) => {
      const tree = await loadPursuitTree(db, pursuitId);
      if (!tree) throw new Error(`pursuit ${pursuitId} not found`);
      return tree;
    },
  };
}

export function plannerPayload(
  overrides: Partial<PlannerOutcomePayload> = {},
): PlannerOutcomePayload {
  return {
    summary: "planned the leg",
    work_items: [
      {
        id: "w1",
        agent_name: "worker",
        title: "implement the leg",
        spec: "write the code for the leg",
        depends_on: [],
      },
    ],
    ...overrides,
  };
}

export function workItem(
  id: string,
  dependsOn: readonly string[] = [],
): PlannerOutcomePayload["work_items"][number] {
  return {
    id,
    agent_name: "worker",
    title: `item ${id}`,
    spec: `spec ${id}`,
    depends_on: [...dependsOn],
  };
}

export function workerPayload(
  overrides: Partial<WorkerOutcomePayload> = {},
): WorkerOutcomePayload {
  return {
    summary: "did the work",
    is_pass: true,
    outcome: "the leg is implemented",
    ...overrides,
  };
}

export async function until(
  check: () => boolean | Promise<boolean>,
  label = "condition",
): Promise<void> {
  for (let attempt = 0; attempt < 500; attempt += 1) {
    if (await check()) return;
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
  throw new Error(`timed out waiting for ${label}`);
}

function messageText(message: InitialUserMessage): string {
  return message.content.map((block) => block.text).join("\n");
}

export function allMessageText(messages: readonly InitialUserMessage[]): string {
  return messages.map(messageText).join("\n---\n");
}
