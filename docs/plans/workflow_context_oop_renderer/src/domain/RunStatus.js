export const RunStatus = Object.freeze({
  NotStarted: "NotStarted",
  Running: "Running",
  Success: "Success",
  Failed: "Failed",
});

export function statusClass(status) {
  if (status === RunStatus.Running) return "running";
  if (status === RunStatus.Success) return "success";
  if (status === RunStatus.Failed) return "failed";
  return "not-started";
}

export function isTerminalStatus(status) {
  return status === RunStatus.Success || status === RunStatus.Failed;
}
