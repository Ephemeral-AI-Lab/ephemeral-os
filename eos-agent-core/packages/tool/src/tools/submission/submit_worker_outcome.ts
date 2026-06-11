import {
  WorkerOutcomePayloadSchema,
  type JsonObject,
  type SubmissionBinding,
} from "@eos/contracts";

import type { ToolDefinition } from "../../contract.js";
import { defineTool } from "../../define.js";
import { ADVISOR_PROMPT } from "../advisory_prompts/submit_worker_outcome_prompt.js";
import { DESCRIPTION } from "../description_prompts/submit_worker_outcome_prompt.js";

export type WorkerSubmissionBinding = Extract<SubmissionBinding, { kind: "worker" }>;

/**
 * The worker's terminal tool with its per-kind payload schema. A
 * workflow-launched run submits through the §2.19 binding (validate +
 * mutate in one DB transaction, error results correctable in-run); an
 * unbound run keeps the service-free behavior and the payload rides the
 * run outcome as `submission`.
 */
export function submitWorkerOutcomeTool(
  binding?: WorkerSubmissionBinding,
): ToolDefinition {
  return defineTool({
    name: "submit_worker_outcome",
    description: DESCRIPTION,
    input: WorkerOutcomePayloadSchema,
    isTerminal: true,
    isAdvisoryRequired: true,
    advisorPrompt: ADVISOR_PROMPT,
    execute: async (input) => {
      if (!binding) {
        const content: JsonObject = {
          summary: input.summary,
          is_pass: input.is_pass,
          outcome: input.outcome,
        };
        return { content };
      }
      const result = await binding.submit(input);
      if (!result.ok) return { content: result.error, isError: true };
      return { content: { summary: input.summary } };
    },
  });
}
