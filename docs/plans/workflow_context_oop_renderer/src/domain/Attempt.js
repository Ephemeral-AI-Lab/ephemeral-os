import { WorkflowEntityBase } from "./WorkflowEntityBase.js";
import { Markdown } from "./Markdown.js";
import { RunStatus } from "./RunStatus.js";

export class Attempt extends WorkflowEntityBase {
  constructor({
    id,
    status = RunStatus.NotStarted,
    folderPath,
    workflowId,
    iterationId,
    plan = undefined,
    workItems = [],
  }) {
    super({ id, status, folderPath });
    this.workflowId = workflowId;
    this.iterationId = iterationId;
    this.plan = plan;
    this.workItems = workItems;
  }

  leafWorkItems() {
    const referenced = new Set();
    this.workItems.forEach(item => item.needs.forEach(need => referenced.add(need)));
    return this.workItems.filter(item => !referenced.has(item.id));
  }

  readyWorkItems() {
    return this.workItems.filter(item => (
      item.status === RunStatus.NotStarted && item.needs.every(needId => {
        const dependency = this.workItems.find(candidate => candidate.id === needId);
        return dependency && dependency.status === RunStatus.Success;
      })
    ));
  }

  hasFailedWorkItem() {
    return this.workItems.some(item => item.status === RunStatus.Failed);
  }

  allWorkItemsSucceeded() {
    return this.workItems.length > 0 && this.workItems.every(item => item.status === RunStatus.Success);
  }

  renderSpec() {
    if (this.isNotStarted() && !this.plan && this.workItems.length === 0) {
      return this.statusLine();
    }

    const parts = [
      this.statusLine(),
      "",
      "# Plan",
    ];
    if (this.plan) {
      parts.push(Markdown.shiftHeadings(this.plan.renderSpec()));
    } else {
      parts.push("Pending to Run");
    }
    this.workItems.forEach(item => {
      parts.push("", `# Work Item ${item.id}`, item.renderBrief());
    });
    return Markdown.join(parts);
  }

  renderBrief() {
    if (this.isNotStarted()) return this.statusLine();

    const parts = [
      this.statusLine(),
      "",
      "# Plan",
    ];
    if (this.plan) {
      parts.push(this.plan.renderBrief());
    } else {
      parts.push("Pending to Run");
    }
    this.leafWorkItems().forEach(item => {
      parts.push("", `# Work Item ${item.id}`, item.renderBrief());
    });
    this.appendTerminalReference(parts);
    return Markdown.join(parts);
  }
}
