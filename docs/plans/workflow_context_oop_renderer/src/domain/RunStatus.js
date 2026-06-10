window.WorkflowContextOop = window.WorkflowContextOop || {};

window.WorkflowContextOop.RunStatus = Object.freeze({
  NotStarted: "NotStarted",
  Running: "Running",
  Success: "Success",
  Failed: "Failed",
});

window.WorkflowContextOop.statusClass = function statusClass(status) {
  const runStatus = window.WorkflowContextOop.RunStatus;
  if (status === runStatus.Running) return "running";
  if (status === runStatus.Success) return "success";
  if (status === runStatus.Failed) return "failed";
  return "not-started";
};

window.WorkflowContextOop.isTerminalStatus = function isTerminalStatus(status) {
  const runStatus = window.WorkflowContextOop.RunStatus;
  return status === runStatus.Success || status === runStatus.Failed;
};
