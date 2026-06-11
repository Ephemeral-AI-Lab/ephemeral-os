import type { ToolDefinition } from "../../contract.js";
import { DESCRIPTION } from "../description_prompts/submit_main_outcome_prompt.js";
import { defineSubmissionTool } from "./shared.js";

export function submitMainOutcomeTool(): ToolDefinition {
  return defineSubmissionTool({
    name: "submit_main_outcome",
    description: DESCRIPTION,
    isAdvisoryRequired: true,
    advisorPrompt:
      "Review whether the main agent's terminal submission faithfully completes the user's goal.",
  });
}
