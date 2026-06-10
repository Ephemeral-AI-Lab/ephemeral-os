import type { BackgroundSupervisor } from "@eos/engine";
import { z } from "zod";

import type { ToolDefinition } from "../../contract.js";
import { defineTool } from "../../define.js";

/** Rows for running plus settled-but-undelivered sessions. */
export function listBackgroundSessionsTool(
  supervisor: BackgroundSupervisor,
): ToolDefinition {
  return defineTool({
    name: "list_background_sessions",
    description:
      "List background sessions: running ones plus settled ones whose completion notice has not been delivered yet.",
    input: z.object({}),
    execute: (_input, _ctx) => Promise.resolve({ content: supervisor.list() }),
  });
}
