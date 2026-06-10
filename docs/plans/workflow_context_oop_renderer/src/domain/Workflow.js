{
  const { WorkflowEntityBase, Markdown, RunStatus } = window.WorkflowContextOop;

  class Workflow extends WorkflowEntityBase {
    constructor({ id, status = RunStatus.Running, folderPath, goal, iterations = [] }) {
      super({ id, status, folderPath });
      this.goal = goal;
      this.iterations = iterations;
    }

    activeIteration() {
      return this.iterations.find(iteration => iteration.status === RunStatus.Running)
        || this.iterations[this.iterations.length - 1];
    }

    renderSpec() {
      const parts = [
        this.statusLine(),
        "",
        "# Workflow Goal",
        this.goal,
      ];
      this.iterations.forEach(iteration => {
        parts.push("", `# Iteration ${iteration.id}`, Markdown.shiftHeadings(iteration.renderSpec()));
      });
      return Markdown.join(parts);
    }

    renderBrief() {
      if (this.isNotStarted()) return this.statusLine();
      const parts = [this.statusLine()];
      this.iterations.forEach(iteration => {
        parts.push("", `# Iteration ${iteration.id}`, Markdown.shiftHeadings(iteration.renderBrief()));
      });
      this.appendTerminalReference(parts);
      return Markdown.join(parts);
    }
  }

  window.WorkflowContextOop.Workflow = Workflow;
}
