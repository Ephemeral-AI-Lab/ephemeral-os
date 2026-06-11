import type {
  AgentRunId,
  DelegateWorkflowInput,
  DelegatedWorkflow,
} from "@eos/contracts";
import type { BackgroundSessionSupervisor } from "@eos/background";
import { z } from "zod";

import type { ToolDefinition } from "../../contract.js";
import { defineTool } from "../../define.js";
import { DESCRIPTION } from "../description_prompts/delegate_workflow_prompt.js";

/** The family's name universe: `delegate_workflow`, alone (§2.18). */
export const WORKFLOW_TOOL_NAMES = ["delegate_workflow"] as const;

const DelegateWorkflowInputSchema = z.object({
  goal: z.string().min(1),
  max_attempts: z.number().int().positive().optional(),
});

/**
 * The workflow family over one bound function plus the per-run supervisor
 * (§2.18). There is no `cancel_workflow` and no read/query tool this
 * round: cancellation rides `cancel_background_session` through the
 * registered handle, and the read surface is deferred.
 */
export function workflowTools(
  delegate: (
    input: DelegateWorkflowInput,
    parent: AgentRunId,
  ) => Promise<DelegatedWorkflow>,
  supervisor: BackgroundSessionSupervisor,
): ToolDefinition[] {
  return [
    defineTool({
      name: "delegate_workflow",
      description: DESCRIPTION,
      input: DelegateWorkflowInputSchema,
      execute: async (input, ctx) => {
        // One open workflow per run: running or settled-but-undelivered.
        const open = supervisor
          .listBackgroundSessions()
          .some((session) => session.type === "workflow");
        if (open) {
          return {
            content:
              "a delegated workflow is already open for this run; wait for its session_settled notification or cancel it first",
            isError: true,
          };
        }
        const workflow = await delegate(input, ctx.meta.run.run_id);
        // Registration precedes the tool result, exactly the subagent
        // pattern: the submission guard covers the workflow before the
        // model's next token.
        supervisor.registerBackgroundSession(
          { type: "workflow", id: workflow.workflowId },
          {
            settled: workflow.terminal.then((terminal) => ({
              status:
                terminal.status === "Success"
                  ? ("completed" as const)
                  : terminal.status === "Cancelled"
                    ? ("cancelled" as const)
                    : ("failed" as const),
              summary: terminal.summary,
            })),
            cancel: (reason) => workflow.cancel(reason),
            describe: () => workflow.describe(),
          },
        );
        return { content: { workflow_id: workflow.workflowId } };
      },
    }),
  ];
}
