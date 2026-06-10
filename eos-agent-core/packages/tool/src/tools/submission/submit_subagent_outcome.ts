import type { ToolDefinition } from "../../contract.js";
import { defineSubmissionTool } from "./shared.js";

export function submitSubagentOutcomeTool(): ToolDefinition {
  return defineSubmissionTool({
    name: "submit_subagent_outcome",
    description:
      "Submit the final outcome of this subagent run. Terminal: a successful call ends the run.",
  });
}
