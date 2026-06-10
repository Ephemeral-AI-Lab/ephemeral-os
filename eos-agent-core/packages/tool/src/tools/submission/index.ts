import { JsonObjectSchema, type AgentKind, type JsonObject } from "@eos/contracts";
import type { BackgroundSupervisor } from "@eos/engine";
import { z } from "zod";

import type { ToolDefinition } from "../../contract.js";
import { defineTool } from "../../define.js";

const SharedOutcomeSchema = z.object({
  /** One-paragraph result the parent reads first. */
  summary: z.string().min(1),
  /** Structured result payload; rides the run outcome as `submission`. */
  payload: JsonObjectSchema.optional(),
});
type SubmissionInput = z.infer<typeof SharedOutcomeSchema>;

interface SubmissionRow {
  name: string;
  description: string;
  /** Per-kind payload schemas are a later seam; all share one this phase. */
  input: z.ZodType<SubmissionInput>;
}

const submissionRow = (kind: AgentKind): SubmissionRow => ({
  name: `submit_${kind}_outcome`,
  description: `Submit the final outcome of this ${kind} run. Terminal: a successful call ends the run.`,
  input: SharedOutcomeSchema,
});

/** The five submission tools are ONE parameterized definition over this table. */
const SUBMISSIONS: Record<AgentKind, SubmissionRow> = {
  main: submissionRow("main"),
  planner: submissionRow("planner"),
  worker: submissionRow("worker"),
  advisor: submissionRow("advisor"),
  subagent: submissionRow("subagent"),
};

/**
 * The terminal tool for one agent kind. Guards "no open sessions before
 * submission" in plain code (not a hook): the model cannot submit past a
 * running session or a settlement it has not seen yet.
 */
export function submissionTool(
  kind: AgentKind,
  supervisor: BackgroundSupervisor,
): ToolDefinition {
  const row = SUBMISSIONS[kind];
  return defineTool({
    name: row.name,
    description: row.description,
    input: row.input,
    terminal: true,
    execute: (input) => {
      const open = supervisor.openCount();
      if (open > 0) {
        const names = supervisor
          .list()
          .map((session) => `${session.type}:${session.id} (${session.status})`)
          .join(", ");
        return Promise.resolve({
          content: `cannot submit while ${String(open)} background session(s) are open (running or undelivered): ${names}. Cancel them or wait for their completion notices.`,
          isError: true,
        });
      }
      const content: JsonObject = { summary: input.summary };
      if (input.payload !== undefined) content.payload = input.payload;
      // The terminal result's content IS the submission: it rides the
      // result into `outcome.submission` - no separate sink port.
      return Promise.resolve({ content });
    },
  });
}
