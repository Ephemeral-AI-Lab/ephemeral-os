import type { ToolDefinition } from "../../contract.js";
import { defineSubmissionTool } from "./shared.js";

export function submitWorkerOutcomeTool(): ToolDefinition {
  return defineSubmissionTool({
    name: "submit_worker_outcome",
    description:
      "Submit the final outcome of this worker run. Terminal: a successful call ends the run.",
    isAdvisoryRequired: true,
    advisorPrompt:
      "Review whether the worker's terminal submission accurately reports the completed work and remaining risk.",
  });
}
