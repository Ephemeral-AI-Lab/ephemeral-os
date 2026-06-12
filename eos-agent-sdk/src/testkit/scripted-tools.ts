import { JsonObjectSchema, type JsonObject } from "../contracts/index.js";
import {
  defineTool,
  type ToolCallContext,
  type ToolDefinition,
  type ToolResult,
} from "../tool/index.js";

/** A scripted definition: permissive JSON-object input. */
export function scriptedTool(options: {
  name: string;
  execute: (input: JsonObject, ctx: ToolCallContext) => Promise<ToolResult>;
  description?: string;
}): ToolDefinition<JsonObject> {
  return defineTool({
    name: options.name,
    description: options.description ?? options.name,
    input: JsonObjectSchema,
    execute: options.execute,
  });
}
