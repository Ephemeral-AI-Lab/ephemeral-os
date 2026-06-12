import type {
  ToolCallContext,
  ToolDefinition,
  ToolResult,
} from "./contract.js";
import type { z } from "zod";

/** What a tool author writes. No behavior field, no flags. */
export interface ToolDefinitionInit<I> {
  name: string;
  description: string;
  input: z.ZodType<I>;
  execute: (input: I, ctx: ToolCallContext) => Promise<ToolResult>;
}

/** The one construction site for `ToolDefinition`. */
export function defineTool<I>(init: ToolDefinitionInit<I>): ToolDefinition<I> {
  const name = init.name.trim();
  if (name.length === 0) {
    throw new Error("defineTool requires a non-empty name");
  }
  return Object.freeze({
    name,
    description: init.description,
    input: init.input,
    execute: init.execute,
  });
}
