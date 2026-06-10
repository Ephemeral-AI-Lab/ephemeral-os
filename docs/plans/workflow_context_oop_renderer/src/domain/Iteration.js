import { WorkflowEntityBase } from "./WorkflowEntityBase.js";
import { Markdown } from "./Markdown.js";
import { RunStatus } from "./RunStatus.js";

export class Iteration extends WorkflowEntityBase {
  constructor({
    id,
    status = RunStatus.Running,
    folderPath,
    workflowId,
    goal,
    attempts = [],
  }) {
    super({ id, status, folderPath });
    this.workflowId = workflowId;
    this.goal = goal;
    this.attempts = attempts;
  }

  activeAttempt() {
    return this.attempts.find(attempt => (
      attempt.status === RunStatus.NotStarted || attempt.status === RunStatus.Running
    )) || this.attempts[this.attempts.length - 1];
  }

  renderSpec() {
    const parts = [
      this.statusLine(),
      "",
      "# Iteration Goal",
      this.goal,
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
