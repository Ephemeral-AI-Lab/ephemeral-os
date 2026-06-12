// The only public package surface: the service and the port/DTO types
// (§14). Entity state, context, and transition modules stay
// package-internal; outside packages construct pursuits exclusively
// through `PursuitService` and the contracts-level DTOs.
export {
  defaultComposeLaunchContext,
  type ComposeLaunchContext,
} from "./context-engine/composer.js";
export {
  type AgentLaunchOptions,
  type AgentLaunchPort,
  type LaunchSettlement,
  type LaunchedAgent,
} from "./launcher.js";
export {
  PursuitService,
  type PursuitServiceDependencies,
} from "./service.js";
