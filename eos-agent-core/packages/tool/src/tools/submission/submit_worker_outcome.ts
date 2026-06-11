import type { ToolDefinition } from "../../contract.js";
import { DESCRIPTION } from "../description_prompts/submit_worker_outcome_prompt.js";
import { defineSubmissionTool } from "./shared.js";

export function submitWorkerOutcomeTool(): ToolDefinition {
  return defineSubmissionTool({
    name: "submit_worker_outcome",
    description: DESCRIPTION,
    isAdvisoryRequired: true,
    advisorPrompt:
      "Review whether the worker's terminal submission accurately reports the completed work and remaining risk.",
  });
}
