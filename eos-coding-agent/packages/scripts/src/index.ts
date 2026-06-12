// Shared subprocess mechanics: execute a shell command with a JSON payload
// on stdin and classify how it settled. Protocol mapping stays with each
// caller (tool hooks, notification trigger rules).
export {
  executeJsonCommand,
  type ExecutedCommandResult,
} from "./execute-json-command.js";
