import type { ToolDefinition } from "../../contract.js";
import { DESCRIPTION } from "../description_prompts/submit_advisor_outcome_prompt.js";
import { defineSubmissionTool } from "./shared.js";

export function submitAdvisorOutcomeTool(): ToolDefinition {
  return defineSubmissionTool({
    name: "submit_advisor_outcome",
    description: DESCRIPTION,
  });
}
