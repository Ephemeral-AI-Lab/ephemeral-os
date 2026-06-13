// The public package surface: the service factory, the narrow agent slice the
// host adapts to, the compose seam, and the contracts-level DTOs the host needs
// to delegate, compose context, and validate workflow args. Entity state,
// transitions, and projection stay package-internal.
export {
  openPursuitService,
  type OpenPursuitServiceDeps,
  type PursuitService,
  type PursuitServiceDependencies,
} from "./service.js";
export { type PursuitAgents } from "./agent-launcher.js";
export {
  defaultComposeLaunchContext,
  type ComposeLaunchContext,
} from "./context-engine/composer.js";
export {
  CreatePursuitInputSchema,
  ContextScriptOutputSchema,
  InitialUserMessageSchema,
  type ContextScriptOutput,
  type CreatePursuitInput,
  type InitialUserMessage,
  type PlannerContextInput,
  type PursuitHandle,
  type PursuitId,
  type PursuitSettlement,
  type WorkerContextInput,
} from "./contracts/pursuit.js";
