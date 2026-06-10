import type { BackgroundSupervisor } from "@eos/engine";
import { z } from "zod";

import type { ToolDefinition } from "../../contract.js";
import { defineTool } from "../../define.js";

// `type` is an open string this phase; it narrows to the session-kind
// enum as the spawning families land.
const CancelInputSchema = z.object({
  type: z.string().min(1),
  id: z.string().min(1),
  reason: z.string().optional(),
});

/** Cancel by native `(type, id)` ref - no minted session-id namespace. */
export function cancelBackgroundSessionTool(
  supervisor: BackgroundSupervisor,
): ToolDefinition {
  return defineTool({
    name: "cancel_background_session",
    description:
      "Cancel a running background session by its type and id, as listed by list_background_sessions.",
    input: CancelInputSchema,
    execute: async ({ type, id, reason }) => {
      const row = supervisor
        .list()
        .find((candidate) => candidate.type === type && candidate.id === id);
      if (!row) {
        return { content: `no background session ${type}:${id}`, isError: true };
      }
      if (row.status !== "running") {
        return {
          content: `background session ${type}:${id} already settled (${row.status}); nothing to cancel`,
        };
      }
      const cancelled = await supervisor.cancel(
        { type, id },
        reason ?? "cancelled by request",
      );
      return {
        content: cancelled
          ? `background session ${type}:${id} cancelled`
          : `background session ${type}:${id} already settled; nothing to cancel`,
      };
    },
  });
}
