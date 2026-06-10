{
  const { RunStatus, isTerminalStatus } = window.WorkflowContextOop;

  class WorkflowEntityBase {
    constructor({ id, status = RunStatus.NotStarted, folderPath }) {
      this.id = id;
      this.status = status;
      this.folderPath = folderPath;
    }

    specPath() {
      return `${this.folderPath}/spec.md`;
    }

    briefPath() {
      return `${this.folderPath}/brief.md`;
    }

    statusLine() {
      return `Status: ${this.status}`;
    }

    isNotStarted() {
      return this.status === RunStatus.NotStarted;
    }

    isTerminal() {
      return isTerminalStatus(this.status);
    }

    appendTerminalReference(parts) {
      if (this.isTerminal()) {
        parts.push("", `Reference: ${this.specPath()}`);
      }
    }

    renderSpec() {
      throw new Error("renderSpec must be implemented by subclasses");
    }

    renderBrief() {
      throw new Error("renderBrief must be implemented by subclasses");
    }
  }

  window.WorkflowContextOop.WorkflowEntityBase = WorkflowEntityBase;
}
