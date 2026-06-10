// The package surface is the §9 API plus the contracts a caller needs to
// hold it: dependency shapes (including the in-memory llm-client override),
// the run row, the advisor profile's magic name, and the recorder-facing
// transcript line shape (§10 seam). Loaders, registries, and the agent tool
// family stay internal behind createAgentRuntime.
export { ADVISOR_AGENT_NAME } from "./agent-tools.js";
export type {
  LlmClientBinding,
  LlmClientRegistry,
} from "./llm-client-registry.js";
export type { RunSummary } from "./run-registry.js";
export {
  createAgentRuntime,
  type AgentRuntime,
  type AgentRuntimeDependencies,
  type StartRunParams,
  type StartedRun,
  type UserMessage,
} from "./runtime.js";
export type { TranscriptLine, TranscriptRead } from "./transcript.js";
