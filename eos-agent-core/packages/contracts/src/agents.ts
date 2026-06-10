import { z } from "zod";

/**
 * The agent kinds the runtime can start. Each kind resolves to one toolset
 * row and one terminal submission tool (`submit_<kind>_outcome`).
 */
export const AgentKindSchema = z.enum([
  "main",
  "planner",
  "worker",
  "advisor",
  "subagent",
]);
export type AgentKind = z.infer<typeof AgentKindSchema>;
