{
  const { WorkflowEntityBase, Markdown, RunStatus } = window.WorkflowContextOop;

  class WorkItem extends WorkflowEntityBase {
    constructor({
      id,
      status = RunStatus.NotStarted,
      folderPath,
      workflowId,
      iterationId,
      attemptId,
      planId,
      workItemSpec,
      needs = [],
      workerSummary = undefined,
      workerOutcome = undefined,
    }) {
      super({ id, status, folderPath });
      this.workflowId = workflowId;
      this.iterationId = iterationId;
      this.attemptId = attemptId;
      this.planId = planId;
      this.workItemSpec = workItemSpec;
      this.needs = needs;
      this.workerSummary = workerSummary;
      this.workerOutcome = workerOutcome;
    }

    renderSpec() {
      return Markdown.join([
        this.statusLine(),
        "",
        "# Spec",
        this.workItemSpec,
        "",
        "# Outcome",
        this.isNotStarted() ? "" : Markdown.pendingOr(this.workerOutcome),
      ]);
    }

    renderBrief() {
      if (this.isNotStarted()) return this.statusLine();
      const parts = [
        this.statusLine(),
        "",
        Markdown.pendingOr(this.workerSummary),
      ];
      this.appendTerminalReference(parts);
      return Markdown.join(parts);
    }
  }

  window.WorkflowContextOop.WorkItem = WorkItem;
}
