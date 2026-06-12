import type { BackgroundSessionSupervisor } from "@eos/background";
import { z } from "zod";

import type { ToolDefinition } from "../../contract.js";
import { defineTool } from "../../define.js";
import { DESCRIPTION } from "../description_prompts/cancel_background_session_prompt.js";

// The session-kind union, narrowed as the spawning families land:
// `command` (sandbox family), `subagent` (agent family), `pursuit`
// (pursuit family). Cancelling a pursuit session IS cancelling the
// pursuit - the handle's cancel runs the full cascade before resolving.
const CancelInputSchema = z.object({
  type: z.enum(["command", "subagent", "pursuit"]),
  id: z.string().min(1),
  reason: z.string().optional(),
});

/** Cancel by native `(type, id)` ref - no minted session-id namespace. */
export function cancelBackgroundSessionTool(
  supervisor: BackgroundSessionSupervisor,
): ToolDefinition {
  return defineTool({
    name: "cancel_background_session",
    description: DESCRIPTION,
    input: CancelInputSchema,
    execute: async ({ type, id, reason }) => {
      const row = supervisor
        .listBackgroundSessions()
        .find((candidate) => candidate.type === type && candidate.id === id);
      if (!row) {
        return { content: `no background session ${type}:${id}`, isError: true };
      }
      if (row.status !== "running") {
        return {
          content: `background session ${type}:${id} already settled (${row.status}); nothing to cancel`,
        };
      }
      const cancelled = await supervisor.cancelBackgroundSession(
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
