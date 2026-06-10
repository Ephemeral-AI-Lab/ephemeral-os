import type { ToolDefinition } from "../../contract.js";
import { defineSubmissionTool } from "./shared.js";

export function submitAdvisorOutcomeTool(): ToolDefinition {
  return defineSubmissionTool({
    name: "submit_advisor_outcome",
    description:
      "Submit the final outcome of this advisor run. Terminal: a successful call ends the run.",
  });
}
