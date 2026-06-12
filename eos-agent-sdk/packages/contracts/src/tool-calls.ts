import { z } from "zod";

import { ToolUseIdSchema } from "./ids.js";
import { JsonObjectSchema, JsonValueSchema } from "./json.js";

/**
 * One settled tool call as the engine records it. Constructed by the batch
 * executor (which pairs the `tool_use_id`), normalized - `is_error` and
 * `is_terminal` are facts stamped by the pipeline, never tool claims, and
 * the timing brackets `execute()` only. `content` stays structured here;
 * the engine stringifies it exactly once when projecting `tool_result`
 * blocks.
 */
export const ToolCallResultSchema = z.object({
  tool_use_id: ToolUseIdSchema,
  content: JsonValueSchema,
  is_error: z.boolean(),
  is_terminal: z.boolean(),
  /** Epoch milliseconds. */
  tool_start_time: z.number(),
  tool_end_time: z.number(),
  /** Transcript/observability only; never model-facing. */
  metadata: JsonObjectSchema.optional(),
});
export type ToolCallResult = z.infer<typeof ToolCallResultSchema>;
