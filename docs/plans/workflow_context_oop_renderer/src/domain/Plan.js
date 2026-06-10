import { WorkflowEntityBase } from "./WorkflowEntityBase.js";
import { Markdown } from "./Markdown.js";
import { RunStatus } from "./RunStatus.js";

export class Plan extends WorkflowEntityBase {
  constructor({
    id,
    status = RunStatus.NotStarted,
    folderPath,
    workflowId,
    iterationId,
    attemptId,
    planSpec = undefined,
    plannerSummary = undefined,
    deferredGoalForNextIteration = undefined,
  }) {
    super({ id, status, folderPath });
    this.workflowId = workflowId;
    this.iterationId = iterationId;
    this.attemptId = attemptId;
    this.planSpec = planSpec;
    this.plannerSummary = plannerSummary;
    this.deferredGoalForNextIteration = deferredGoalForNextIteration;
  }

  renderSpec() {
    if (this.isNotStarted() && !this.planSpec) return this.statusLine();
    return Markdown.join([
      this.statusLine(),
      "",
      "# Plan Spec",
      Markdown.pendingOr(this.planSpec),
      "",
      "# Deferred Goal For Next Iteration",
      this.deferredGoalForNextIteration || "",
    ]);
  }

  renderBrief() {
    if (this.isNotStarted()) return this.statusLine();
    const parts = [
      this.statusLine(),
      "",
      Markdown.pendingOr(this.plannerSummary),
    ];
    this.appendTerminalReference(parts);
    return Markdown.join(parts);
  }
}
