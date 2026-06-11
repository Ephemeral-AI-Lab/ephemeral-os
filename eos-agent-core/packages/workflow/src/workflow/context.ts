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

/**
 * §5.3: the workflow outcome is the ordered ledger of every closed
 * iteration's outcome. A cancelled workflow renders a cancellation marker
 * (not a business outcome) ahead of any already closed iterations.
 */
function composeWorkflowOutcome(
  workflow: WorkflowState,
  iterations: readonly IterationState[],
): string {
  const head =
    workflow.status === "Cancelled"
      ? "# Workflow outcome\nworkflow cancelled"
      : "# Workflow outcome";
  const sections = iterations
    .filter(
      (iteration) =>
        iteration.status === "Success" || iteration.status === "Failed",
    )
    .map(
      (iteration) =>
        `## iteration_${iteration.id} [${iteration.status}]\n${composeIterationOutcome(iteration)}`,
    );
  return [head, ...sections].join("\n\n");
}
