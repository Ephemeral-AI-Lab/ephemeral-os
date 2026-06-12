import type {
  AgentRunId,
  DelegatePursuitInput,
  DelegatedPursuit,
} from "@eos/contracts";
import type { BackgroundSessionSupervisor } from "@eos/background";
import { z } from "zod";

import type { ToolDefinition } from "../../contract.js";
import { defineTool } from "../../define.js";
import { DESCRIPTION } from "../description_prompts/delegate_pursuit_prompt.js";

/** The family's name universe: `delegate_pursuit`, alone (§2.18). */
export const PURSUIT_TOOL_NAMES = ["delegate_pursuit"] as const;

const DelegatePursuitInputSchema = z.object({
  goal: z.string().min(1),
  max_attempts: z.number().int().positive().optional(),
});

/**
 * The pursuit family over one bound function plus the per-run supervisor
 * (§2.18). There is no `cancel_pursuit` and no read/query tool this
 * round: cancellation rides `cancel_background_session` through the
 * registered handle, and the read surface is deferred.
 */
export function pursuitTools(
  delegate: (
    input: DelegatePursuitInput,
    parent: AgentRunId,
  ) => Promise<DelegatedPursuit>,
  supervisor: BackgroundSessionSupervisor,
): ToolDefinition[] {
  return [
    defineTool({
      name: "delegate_pursuit",
      description: DESCRIPTION,
      input: DelegatePursuitInputSchema,
      execute: async (input, ctx) => {
        // One open pursuit per run: running or settled-but-undelivered.
        const open = supervisor
          .listBackgroundSessions()
          .some((session) => session.type === "pursuit");
        if (open) {
          return {
            content:
              "a delegated pursuit is already open for this run; wait for its session_settled notification or cancel it first",
            isError: true,
          };
        }
        const pursuit = await delegate(input, ctx.meta.run.run_id);
        // Registration precedes the tool result, exactly the subagent
        // pattern: the submission guard covers the pursuit before the
        // model's next token.
        supervisor.registerBackgroundSession(
          { type: "pursuit", id: pursuit.pursuitId },
          {
            settled: pursuit.terminal.then((terminal) => ({
              status:
                terminal.status === "Success"
                  ? ("completed" as const)
                  : terminal.status === "Cancelled"
                    ? ("cancelled" as const)
                    : ("failed" as const),
              summary: terminal.summary,
            })),
            cancel: (reason) => pursuit.cancel(reason),
            describe: () => pursuit.describe(),
          },
        );
        return { content: { pursuit_id: pursuit.pursuitId } };
      },
    }),
  ];
}
