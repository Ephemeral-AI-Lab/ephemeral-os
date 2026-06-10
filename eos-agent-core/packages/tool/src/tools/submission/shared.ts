import { JsonObjectSchema, type JsonObject } from "@eos/contracts";
import { z } from "zod";

import type { ToolDefinition } from "../../contract.js";
import { defineTool } from "../../define.js";

const SubmissionInputSchema = z.object({
  /** One-paragraph result the parent reads first. */
  summary: z.string().min(1),
  /** Structured result payload; rides the run outcome as `submission`. */
  payload: JsonObjectSchema.optional(),
});

type SubmissionInput = z.infer<typeof SubmissionInputSchema>;

interface SubmissionToolInit {
  name: string;
  description: string;
}

export function defineSubmissionTool({
  name,
  description,
}: SubmissionToolInit): ToolDefinition {
  return defineTool({
    name,
    description,
    input: SubmissionInputSchema,
    isTerminal: true,
    execute: (input: SubmissionInput) => {
      const content: JsonObject = { summary: input.summary };
      if (input.payload !== undefined) content.payload = input.payload;
      return Promise.resolve({ content });
    },
  });
}
