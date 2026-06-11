import { isWorkflowEntityTerminal } from "@eos/contracts";

import { composeIterationOutcome } from "../iteration/context.js";
import type { IterationState } from "../iteration/state.js";
import type { EntityFieldFile } from "../work-item/context.js";
import type { WorkflowState } from "./state.js";

export function workflowFieldFiles(
  workflow: WorkflowState,
  iterations: readonly IterationState[],
): EntityFieldFile[] {
  const files: EntityFieldFile[] = [
    { name: "goal.md", content: workflow.goal },
  ];
  if (isWorkflowEntityTerminal(workflow.status)) {
    files.push({
      name: "outcome.md",
      content: composeWorkflowOutcome(workflow, iterations),
    });
  }
  return files;
}

function composeWorkflowOutcome(
  workflow: WorkflowState,
  iterations: readonly IterationState[],
): string {
  const last = iterations.at(-1);
  switch (workflow.status) {
    case "Success":
      return last ? composeIterationOutcome(last) : "workflow completed";
    case "Failed": {
      const failReason = [...iterations]
        .reverse()
        .flatMap((iteration) => [...iteration.attempts].reverse())
        .find((attempt) => attempt.failReason !== null)?.failReason;
      return failReason ?? "workflow failed";
    }
    default:
      return "workflow cancelled";
  }
}
