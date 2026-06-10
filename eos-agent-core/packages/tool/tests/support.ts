import {
  toolUseIdFrom,
  type JsonObject,
  type ToolCallResult,
} from "@eos/contracts";
import type { AgentEvent, ToolUseBlock } from "@eos/engine";
import { scriptedRunState } from "@eos/testkit";

import { HookEngine } from "../src/hooks/runner.js";
import type { HookConfigEntry, HookOutput } from "../src/hooks/protocol.js";
import {
  bindTool,
  type HookPayloadFacts,
  type PipelineResult,
} from "../src/pipeline.js";
import { snapshotRunState, type AgentRunState } from "../src/run-state.js";
import type { ToolDefinition } from "../src/contract.js";

export function toolUse(
  id: string,
  name: string,
  input: JsonObject = {},
): ToolUseBlock {
  return { type: "tool_use", tool_use_id: toolUseIdFrom(id), name, input };
}

export const live = (): AbortSignal => new AbortController().signal;

export interface RunPipelineOptions {
  input?: JsonObject;
  entries?: HookConfigEntry[];
  hookPayloadFacts?: () => HookPayloadFacts;
  runState?: AgentRunState;
  signal?: AbortSignal;
}

/** Bind one definition and run a single call through the pipeline. */
export function runPipeline(
  definition: ToolDefinition,
  options: RunPipelineOptions = {},
): Promise<PipelineResult> {
  const bound = bindTool(definition, {
    hooks: new HookEngine(options.entries ?? []),
    hookPayloadFacts: options.hookPayloadFacts,
  });
  const runState = options.runState ?? scriptedRunState();
  return bound.run(
    toolUse("tu_1", definition.name, options.input ?? {}),
    snapshotRunState(runState),
    options.signal ?? live(),
  );
}

/** A PreToolUse callback entry over one tool name (or all when omitted). */
export function preHook(
  run: (payload: { tool_input: JsonObject }) => Promise<HookOutput> | HookOutput,
  matcher?: string,
): HookConfigEntry {
  return {
    event: "PreToolUse",
    matcher,
    hooks: [{ type: "callback", run: (payload) => Promise.resolve(run(payload)) }],
  };
}

export function collector(): {
  events: AgentEvent[];
  emit: (event: AgentEvent) => void;
} {
  const events: AgentEvent[] = [];
  return {
    events,
    emit: (event) => {
      events.push(event);
    },
  };
}

export function resultContent(result: ToolCallResult | PipelineResult): string {
  return typeof result.content === "string"
    ? result.content
    : JSON.stringify(result.content);
}

/** The result's `metadata.hook_warnings`, joined for substring assertions. */
export function hookWarnings(result: ToolCallResult | PipelineResult): string {
  const warnings = result.metadata?.hook_warnings;
  if (!Array.isArray(warnings)) {
    throw new Error("expected metadata.hook_warnings to be present");
  }
  return warnings.map(String).join("\n");
}

/** One macrotask: every already-queued microtask has run by then. */
export function tick(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve();
    }, 0);
  });
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

export function must<T>(value: T | undefined | null): T {
  if (value === undefined || value === null) {
    throw new Error("expected a value to be present");
  }
  return value;
}
