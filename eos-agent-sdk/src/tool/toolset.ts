import type { ToolExecutor } from "../engine/index.js";

import type { ToolDefinition } from "./contract.js";
import { toolBatchExecutor } from "./executor.js";
import { unwrapAgentOutcomeFn, type AgentOutcomeFn } from "./outcome.js";
import { bindTool, type RunScope } from "./pipeline.js";
import { bindTerminalTool, type TerminalGate } from "./terminal.js";

export interface BuildToolExecutorInput<T> {
  scope: RunScope;
  /** ALL tools arrive here — the SDK ships none. */
  tools: readonly ToolDefinition[];
  /** Present in terminal-tool mode; absent in text mode. */
  outcome?: { fn: AgentOutcomeFn<T>; gate: TerminalGate };
}

export interface BuiltToolExecutor<T> {
  executor: ToolExecutor;
  /** Defined when an outcome fn is bound (terminal-tool mode). */
  takeAccepted?: () => { value: T } | undefined;
}

/**
 * Bind the supplied definitions (plus the minted terminal tool, when the
 * run has one) through the pipeline and return the engine's executor over
 * a deterministically name-sorted registry (prompt-cache stability).
 * Name-collision validation happens earlier, at `createAgent`.
 */
export function buildToolExecutor<T>(
  input: BuildToolExecutorInput<T>,
): BuiltToolExecutor<T> {
  const bound = input.tools.map((definition) => bindTool(definition, input.scope));
  if (input.outcome === undefined) {
    return { executor: toolBatchExecutor({ tools: sortByName(bound) }) };
  }
  const terminal = bindTerminalTool(
    unwrapAgentOutcomeFn(input.outcome.fn),
    input.scope,
    input.outcome.gate,
  );
  return {
    executor: toolBatchExecutor({
      tools: sortByName([...bound, terminal.bound]),
      terminalName: terminal.bound.name,
    }),
    takeAccepted: terminal.takeAccepted,
  };
}

function sortByName<T extends { name: string }>(tools: T[]): T[] {
  return tools.slice().sort((a, b) => (a.name < b.name ? -1 : 1));
}
