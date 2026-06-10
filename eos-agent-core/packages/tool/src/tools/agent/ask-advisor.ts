import { JsonObjectSchema } from "@eos/contracts";
import type { AgentRunOutcome } from "@eos/engine";
import { z } from "zod";

import type { ToolDefinition, ToolOutcome } from "../../contract.js";
import { defineTool } from "../../define.js";
import type { AgentRunCalls, AgentToolUserMessage } from "./index.js";

/** The single site that owns the advisor profile's magic name (§2.6). */
export const ADVISOR_AGENT_NAME = "advisor";

const MAX_READ_BYTES = 262_144;

function userText(text: string): AgentToolUserMessage {
  return { role: "user", content: [{ type: "text", text }] };
}

const AskAdvisorInputSchema = z.object({
  /** The terminal tool the caller intends to call next. */
  tool_name: z.string().min(1),
  /** The exact payload the caller intends to submit. */
  payload: JsonObjectSchema.optional(),
});

export function askAdvisorTool(calls: AgentRunCalls): ToolDefinition {
  return defineTool({
    name: "ask_advisor",
    description:
      "Ask the advisor to review this run's transcript and the terminal payload you intend to submit (pass tool_name and the exact payload). Blocks until the advisor answers; the result is the advisor's submission.",
    input: AskAdvisorInputSchema,
    // The call input itself is not forwarded: this call's tool_use already
    // sits in the transcript the advisor reads, payload included.
    execute: async (_input, ctx) => {
      const callerTranscript = await readWholeTranscript(
        calls,
        ctx.meta.run.transcript_path,
      );
      const advisor = calls.startRun({
        agentName: ADVISOR_AGENT_NAME,
        initialMessages: [
          userText(callerTranscript),
          userText(
            "Read the transcript and verify if the caller submitted the payload correctly.",
          ),
        ],
        // The advisor dies with this tool call's own execution scope (§13.6).
        signal: ctx.signal,
      });
      return mapAdvisorOutcome(await advisor.handle.outcome);
    },
  });
}

async function readWholeTranscript(
  calls: AgentRunCalls,
  path: string,
): Promise<string> {
  let offset = 0;
  let data = "";
  for (;;) {
    const read = await calls.readTranscriptFile(path, offset, MAX_READ_BYTES);
    data += read.data;
    offset = read.next_offset;
    if (read.eof) return data;
  }
}

function mapAdvisorOutcome(outcome: AgentRunOutcome): ToolOutcome {
  switch (outcome.status) {
    case "completed":
      return { content: outcome.submission ?? "advisor run completed without a submission" };
    case "cancelled":
      return { content: `advisor run cancelled: ${outcome.reason}`, isError: true };
    case "failed":
      return { content: `advisor run failed: ${outcome.failure.message}`, isError: true };
  }
}
