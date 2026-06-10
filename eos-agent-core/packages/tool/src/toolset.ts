import type { AgentKind } from "@eos/contracts";
import type { NotificationInbox, ToolExecutor } from "@eos/engine";

import type { ToolDefinition } from "./contract.js";
import { toolBatchExecutor } from "./executor.js";
import { HookEngine } from "./hooks/runner.js";
import { bindTool } from "./pipeline.js";
import type { AgentRunState } from "./run-state.js";

// Recorded product targets: the sandbox, agent, and workflow names below
// activate only when their family's factory arrives with its service -
// the assembly skips row names with no constructed definition.
const SANDBOX_TOOLS = [
  "read",
  "multi_read",
  "write",
  "edit",
  "exec_command",
  "command_stdin",
  "read_command_transcript",
  "enter_isolated_workspace",
  "exit_isolated_workspace",
] as const;
const AGENT_TOOLS = ["run_subagent", "ask_advisor", "read_agent_run_transcript"] as const;
const WORKFLOW_TOOLS = ["delegate_workflow", "query_workflow"] as const;
const BACKGROUND_TOOLS = ["list_background_sessions", "cancel_background_session"] as const;
const READ_ONLY_TOOLS = ["read", "multi_read"] as const;

/** The single edit point for kind/tool product decisions. */
export const AGENT_TOOLSET: Record<AgentKind, readonly string[]> = {
  main: [
    ...SANDBOX_TOOLS,
    ...AGENT_TOOLS,
    ...WORKFLOW_TOOLS,
    ...BACKGROUND_TOOLS,
    "submit_main_outcome",
  ],
  worker: [...SANDBOX_TOOLS, ...BACKGROUND_TOOLS, "submit_worker_outcome"],
  subagent: [...SANDBOX_TOOLS, ...BACKGROUND_TOOLS, "submit_subagent_outcome"],
  planner: [...READ_ONLY_TOOLS, "submit_planner_outcome"],
  advisor: [...READ_ONLY_TOOLS, "submit_advisor_outcome"],
};

/**
 * Registration never sees a port: the composition root calls the family
 * factories (each injected with exactly its own services) and hands this
 * function finished definitions only.
 */
export interface BuildToolExecutorInput {
  runState: AgentRunState;
  definitions: ToolDefinition[];
  /** Target for hook `additionalContext` notifications. */
  inbox?: NotificationInbox;
  /** Operator hooks; absent means no hooks, not built-in ones. */
  hookEngine?: HookEngine;
}

/**
 * Intersect `AGENT_TOOLSET[kind]` with the supplied definitions (row names
 * without a definition are skipped; definitions outside the row are
 * excluded), bind every kept definition through the pipeline, and return
 * the engine's `ToolExecutor` over a deterministically sorted registry
 * (prompt-cache stability).
 */
export function buildToolExecutor(input: BuildToolExecutorInput): ToolExecutor {
  const row = new Set<string>(AGENT_TOOLSET[input.runState.kind]);
  const hooks = input.hookEngine ?? new HookEngine([]);
  const tools = input.definitions
    .filter((definition) => row.has(definition.name))
    .sort((a, b) => (a.name < b.name ? -1 : 1))
    .map((definition) => bindTool(definition, { hooks, inbox: input.inbox }));
  return toolBatchExecutor({ runState: input.runState, tools });
}
