import type { ToolDefinition } from "../../contract.js";
import { defineSubmissionTool } from "./shared.js";

export function submitPlannerOutcomeTool(): ToolDefinition {
  return defineSubmissionTool({
    name: "submit_planner_outcome",
    description:
      "Submit the final outcome of this planner run. Terminal: a successful call ends the run.",
  });
}
