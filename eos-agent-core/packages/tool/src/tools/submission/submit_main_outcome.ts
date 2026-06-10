import type { ToolDefinition } from "../../contract.js";
import { defineSubmissionTool } from "./shared.js";

export function submitMainOutcomeTool(): ToolDefinition {
  return defineSubmissionTool({
    name: "submit_main_outcome",
    description:
      "Submit the final outcome of this main run. Terminal: a successful call ends the run.",
    isAdvisoryRequired: true,
    advisorPrompt:
      "Review whether the main agent's terminal submission faithfully completes the user's goal.",
  });
}
