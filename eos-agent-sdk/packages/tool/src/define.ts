import type { JsonObject } from "@eos/contracts";
import { z } from "zod";

import {
  ToolNameSchema,
  type ToolCallContext,
  type ToolDefinition,
  type ToolOutcome,
} from "./contract.js";

/** What a tool author writes; flags are optional and default fail-closed. */
export interface ToolDefinitionInit<I> {
  name: string;
  description: string;
  input: z.ZodType<I>;
  /** Default false. */
  isTerminal?: boolean;
  /** Default `isTerminal`: a submission stays solo unless explicitly relaxed. */
  isBatchExecutionForbidden?: boolean;
  /** Default false: a forgotten override degrades to "banned in isolated mode". */
  availableInIsolatedWorkspace?: boolean;
  /** Default false: most tools do not require advisor approval. */
  isAdvisoryRequired?: boolean;
  /** Required and non-empty when `isAdvisoryRequired` is true. */
  advisorPrompt?: string;
  execute: (input: I, ctx: ToolCallContext) => Promise<ToolOutcome>;
}

/**
 * The one construction site for `ToolDefinition`: centralizes the
 * fail-closed defaults and derives the wire `ToolSpec` from the Zod input.
 */
export function defineTool<I>(init: ToolDefinitionInit<I>): ToolDefinition<I> {
  const name = ToolNameSchema.parse(init.name);
  const isAdvisoryRequired = init.isAdvisoryRequired ?? false;
  const advisorPrompt = init.advisorPrompt?.trim();
  if (isAdvisoryRequired && !advisorPrompt) {
    throw new Error(`tool ${name} requires a non-empty advisorPrompt`);
  }
  if (!isAdvisoryRequired && init.advisorPrompt !== undefined) {
    throw new Error(`tool ${name} has advisorPrompt without isAdvisoryRequired`);
  }
  return {
    name,
    description: init.description,
    input: init.input,
    isTerminal: init.isTerminal ?? false,
    isBatchExecutionForbidden: init.isBatchExecutionForbidden ?? init.isTerminal ?? false,
    availableInIsolatedWorkspace: init.availableInIsolatedWorkspace ?? false,
    isAdvisoryRequired,
    ...(advisorPrompt !== undefined && { advisorPrompt }),
    spec: {
      name,
      description: init.description,
      // JSON Schema output is JSON by construction.
      input_schema: z.toJSONSchema(init.input) as JsonObject,
    },
    execute: init.execute,
  };
}
