import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  agentRunIdFrom,
  mintAgentRunId,
  type AgentRunId,
  type DelegatedWorkflow,
  type InitialUserMessage,
  type PlannerOutcomePayload,
  type SubmissionResult,
  type WorkerOutcomePayload,
  type WorkflowId,
} from "@eos/contracts";
import { createWorkflowDatabase, type WorkflowDb } from "@eos/db";

import {
  defaultComposeLaunchContext,
  type ComposeLaunchContext,
} from "../src/context-engine/composer.js";
import type {
  AgentLaunchOptions,
  AgentLaunchPort,
  LaunchSettlement,
} from "../src/launcher.js";
import { WorkflowService, type WorkflowServiceDependencies } from "../src/service.js";
import { loadWorkflowTree, type WorkflowTree } from "../src/workflow-tree.js";

const PARENT_RUN = agentRunIdFrom("parent-run");

/** One recorded `port.launch`, drivable like a scripted child run. */
export interface ScriptedLaunch {
  agentName: string;
  messages: readonly InitialUserMessage[];
  options: AgentLaunchOptions | undefined;
  runId: AgentRunId;
  interruptReason: string | undefined;
  settle(settlement: LaunchSettlement): void;
  submitPlanner(payload: PlannerOutcomePayload): Promise<SubmissionResult>;
  submitWorker(payload: WorkerOutcomePayload): Promise<SubmissionResult>;
}

export interface Harness {
  db: WorkflowDb;
  service: WorkflowService;
  launches: ScriptedLaunch[];
  contextRoot: string;
  delegate(goal?: string, maxAttempts?: number): Promise<DelegatedWorkflow>;
  tree(workflowId: WorkflowId): Promise<WorkflowTree>;
}

export function harness(
  overrides: Partial<WorkflowServiceDependencies> & {
    compose?: ComposeLaunchContext;
  } = {},
): Harness {
  const db = createWorkflowDatabase(":memory:");
  const contextRoot = mkdtempSync(join(tmpdir(), "eos-workflow-ctx-"));
  const launches: ScriptedLaunch[] = [];

  const port: AgentLaunchPort = {
    launch(agentName, initialMessages, options) {
      let resolve!: (settlement: LaunchSettlement) => void;
      const outcome = new Promise<LaunchSettlement>((settle) => {
        resolve = settle;
      });
      const launch: ScriptedLaunch = {
        agentName,
        messages: initialMessages,
        options,
        runId: mintAgentRunId(),
        interruptReason: undefined,
        settle: resolve,
        submitPlanner: (payload) => {
          const binding = options?.submission;
          if (binding?.kind !== "planner") {
            throw new Error(`launch of ${agentName} carries no planner binding`);
          }
          return binding.submit(payload);
        },
        submitWorker: (payload) => {
          const binding = options?.submission;
          if (binding?.kind !== "worker") {
            throw new Error(`launch of ${agentName} carries no worker binding`);
          }
          return binding.submit(payload);
        },
      };
      launches.push(launch);
      return {
        runId: launch.runId,
        outcome,
        interrupt: (reason) => {
          launch.interruptReason ??= reason;
        },
      };
    },
  };

  const service = new WorkflowService({
    db,
    port,
    compose: overrides.compose ?? defaultComposeLaunchContext,
    contextRoot,
    plannerAgentName: "planner",
    isRegisteredWorkerAgent: (name) => name === "worker",
    logMirrorFailure: () => undefined,
    ...overrides,
  });

  return {
    db,
    service,
    launches,
    contextRoot,
    delegate: (goal = "ship the feature", maxAttempts) =>
      service.delegate(
        { goal, ...(maxAttempts !== undefined && { max_attempts: maxAttempts }) },
        PARENT_RUN,
      ),
    tree: async (workflowId) => {
      const tree = await loadWorkflowTree(db, workflowId);
      if (!tree) throw new Error(`workflow ${workflowId} not found`);
      return tree;
    },
  };
}

export function plannerPayload(
  overrides: Partial<PlannerOutcomePayload> = {},
): PlannerOutcomePayload {
  return {
    summary: "planned the slice",
    iteration_focus: "the first slice",
    work_items: [
      {
        id: "w1",
        agent_name: "worker",
        description: "implement the slice",
        work_item_spec: "write the code for the slice",
        needs: [],
      },
    ],
    ...overrides,
  };
}

export function workerPayload(
  overrides: Partial<WorkerOutcomePayload> = {},
): WorkerOutcomePayload {
  return {
    summary: "did the work",
    is_pass: true,
    outcome: "the slice is implemented",
    ...overrides,
  };
}

/** Poll until `check` holds; the suite is engine-free, so ticks are cheap. */
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
  return message.content
    .filter((block) => block.type === "text")
    .map((block) => block.text)
    .join("\n");
}

export function allMessageText(messages: readonly InitialUserMessage[]): string {
  return messages.map(messageText).join("\n---\n");
}
