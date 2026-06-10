{
  const { WorkflowEntityBase, Markdown, RunStatus } = window.WorkflowContextOop;

  class Iteration extends WorkflowEntityBase {
    constructor({
      id,
      status = RunStatus.Running,
      folderPath,
      workflowId,
      goal,
      maxTry = 3,
      attempts = [],
    }) {
      super({ id, status, folderPath });
      this.workflowId = workflowId;
      this.goal = goal;
      this.maxTry = maxTry;
      this.attempts = attempts;
    }

    activeAttempt() {
      return this.attempts.find(attempt => attempt.status === RunStatus.Running)
        || this.attempts.find(attempt => attempt.status === RunStatus.NotStarted)
        || this.attempts[this.attempts.length - 1];
    }

    renderSpec() {
      const parts = [
        this.statusLine(),
        "",
        "# Iteration Goal",
        this.goal,
        "",
        "# Max Try",
        String(this.maxTry),
      ];
      this.attempts.forEach(attempt => {
        parts.push("", `# Attempt ${attempt.id}`, Markdown.shiftHeadings(attempt.renderSpec()));
      });
      return Markdown.join(parts);
    }

    renderBrief() {
      if (this.isNotStarted()) return this.statusLine();
      const parts = [this.statusLine()];
      this.attempts.forEach(attempt => {
        parts.push("", `# Attempt ${attempt.id}`, Markdown.shiftHeadings(attempt.renderBrief()));
      });
      this.appendTerminalReference(parts);
      return Markdown.join(parts);
    }
  }

  window.WorkflowContextOop.Iteration = Iteration;
}
