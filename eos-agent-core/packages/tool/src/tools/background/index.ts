import type { BackgroundSupervisor } from "@eos/engine";

import type { ToolDefinition } from "../../contract.js";
import { cancelBackgroundSessionTool } from "./cancel-background-session.js";
import { listBackgroundSessionsTool } from "./list-background-sessions.js";

/** The background family: list + cancel, closed over the supervisor. */
export function backgroundTools(supervisor: BackgroundSupervisor): ToolDefinition[] {
  return [
    listBackgroundSessionsTool(supervisor),
    cancelBackgroundSessionTool(supervisor),
  ];
}
