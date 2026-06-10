export class InMemoryWorkflowStore {
  constructor(factory) {
    this.factory = factory;
    this.workflow = undefined;
    this.version = 0;
    this.eventLog = [];
  }

  save(workflow, event) {
    this.workflow = this.factory.cloneWorkflow(workflow);
    this.version += 1;
    this.eventLog.unshift({ version: this.version, event });
    this.eventLog = this.eventLog.slice(0, 30);
  }

  clear() {
    this.workflow = undefined;
    this.version = 0;
    this.eventLog = [];
  }

  loadFreshWorkflow() {
    return this.workflow ? this.factory.cloneWorkflow(this.workflow) : undefined;
  }

  currentVersion() {
    return this.version;
  }

  events() {
    return this.eventLog.slice();
  }
}
