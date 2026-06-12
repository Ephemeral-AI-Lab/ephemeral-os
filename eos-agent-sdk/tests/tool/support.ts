import {
  agentRunIdFrom,
  toolUseIdFrom,
  type JsonObject,
  type Message,
} from "../../src/contracts/index.js";
import type { BackgroundTaskSupervisor } from "../../src/background/index.js";
import type { AgentEventBody, ToolBatchContext, ToolUseBlock } from "../../src/engine/index.js";

import { HookEngine, type HookEntry } from "../../src/tool/hooks.js";
import type { RunScope } from "../../src/tool/pipeline.js";

export const RUN_ID = agentRunIdFrom("run-fixture");

export interface ScopeFixture {
  scope: RunScope;
  published: string[];
}

/** A run scope over a recording notifier and a stub supervisor. */
export function scopeFixture(entries: HookEntry[] = []): ScopeFixture {
  const published: string[] = [];
  return {
    published,
    scope: {
      runId: RUN_ID,
      backgroundTaskSupervisor: {
        register: () => ({ taskId: "task-stub" }),
        list: () => [],
        cancel: () => Promise.resolve(false),
      } as unknown as BackgroundTaskSupervisor,
      notifier: {
        publish: (message) => {
          published.push(message);
        },
      },
      hooks: new HookEngine(entries),
    },
  };
}

export interface BatchFixture extends ToolBatchContext {
  events: AgentEventBody[];
}

export function batchFixture(options: {
  signal?: AbortSignal;
  llmMessages?: readonly Message[];
} = {}): BatchFixture {
  const events: AgentEventBody[] = [];
  return {
    signal: options.signal ?? new AbortController().signal,
    llmMessages: options.llmMessages ?? [],
    emit: (event) => {
      events.push(event);
    },
    events,
  };
}

export function call(
  id: string,
  name: string,
  input: JsonObject = {},
): ToolUseBlock {
  return { type: "tool_use", tool_use_id: toolUseIdFrom(id), name, input };
}

/** Narrow a result's content to its string form, failing the test otherwise. */
export function contentText(value: unknown): string {
  if (typeof value !== "string") {
    throw new Error(`expected string content, got ${typeof value}`);
  }
  return value;
}
