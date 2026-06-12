// The assembly module: createAgentSdk plus the config shapes a caller
// needs to hold it. Run mechanics live in src/engine; the records writer
// and llm registry stay internal behind createAgentSdk.
export {
  buildLlmClientRegistry,
  type LlmClientConfig,
  type LlmClientProfile,
  type LlmClientRegistry,
  type LlmRef,
  type ResolvedLlmProfile,
} from "./llm-clients.js";
export { JsonlRunRecorder } from "./records.js";
export {
  createAgentSdk,
  type Agent,
  type AgentSdk,
  type AgentSdkConfig,
  type AgentSpec,
} from "./sdk.js";
