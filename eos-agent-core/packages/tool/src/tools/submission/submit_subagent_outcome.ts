import type { ToolDefinition } from "../../contract.js";
import { DESCRIPTION } from "../description_prompts/submit_subagent_outcome_prompt.js";
import { defineSubmissionTool } from "./shared.js";

export function submitSubagentOutcomeTool(): ToolDefinition {
  return defineSubmissionTool({
    name: "submit_subagent_outcome",
    description: DESCRIPTION,
  });
}
