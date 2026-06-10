import {
  JsonObjectSchema,
  agentRunIdFrom,
  type AgentRunId,
  type JsonValue,
} from "@eos/contracts";
import type {
  AgentRunOutcome,
  BackgroundSupervisor,
  SessionOutcome,
} from "@eos/engine";
import { defineTool, type ToolDefinition, type ToolOutcome } from "@eos/tool";
import { z } from "zod";

import type { StartRunParams, StartedRun, UserMessage } from "./runtime.js";
import type { TranscriptRead } from "./transcript.js";

/** The single site that owns the advisor profile's magic name (§2.6). */
export const ADVISOR_AGENT_NAME = "advisor";

/** The family's name universe: static, so profile validation needs no services. */
export const AGENT_TOOL_NAMES = [
  "run_subagent",
  "ask_advisor",
  "read_agent_run_transcript",
] as const;

const DEFAULT_READ_BYTES = 65_536;
const MAX_READ_BYTES = 262_144;

/**
 * The engine literal `runAgentLoop` disposes sessions with on every finish.
 * Any other cancel reason reaches a subagent only through
 * `cancel_background_session`, so this one string distinguishes the §8
 * disposal cascade from a model-initiated cancel.
 */
const ENGINE_DISPOSE_REASON = "run finished";

/** Narrow bound runtime calls - never a service object (§5). */
export interface AgentRunCalls {
  /** `startRun` recursion with the caller stamped as parent (§2.6). */
  startRun(params: StartRunParams): StartedRun;
  /** Registry lookup over runs this runtime started. */
  transcriptPathOf(runId: AgentRunId): string | undefined;
  /** Byte-offset read over a write-quiesced transcript file. */
  readTranscriptFile(
    path: string,
    offset: number,
    maxBytes: number,
  ): Promise<TranscriptRead>;
}

/** The agent family, one bound definition per `AGENT_TOOL_NAMES` entry. */
export function agentTools(
  calls: AgentRunCalls,
  supervisor: BackgroundSupervisor,
): ToolDefinition[] {
  return [
    runSubagentTool(calls, supervisor),
    askAdvisorTool(calls),
    readAgentRunTranscriptTool(calls),
  ];
}

function userText(text: string): UserMessage {
  return { role: "user", content: [{ type: "text", text }] };
}

// --- run_subagent ------------------------------------------------------------

const RunSubagentInputSchema = z.object({
  agent_name: z.string().min(1),
  prompt: z.string().min(1),
});

function runSubagentTool(
  calls: AgentRunCalls,
  supervisor: BackgroundSupervisor,
): ToolDefinition {
  return defineTool({
    name: "run_subagent",
    description:
      "Start the named agent as a detached background run on the given prompt. Returns its run_id immediately; the outcome arrives later as a session_settled notification.",
    input: RunSubagentInputSchema,
    execute: (input, ctx) => {
      // Deliberately NO signal: a detached run gets a fresh abort root and
      // never dies with the caller's turn. Cancellation reaches it only
      // through the §8 disposal cascade or cancel_background_session.
      const subagent = calls.startRun({
        agentName: input.agent_name,
        initialMessages: [userText(input.prompt)],
      });
      supervisor.register(
        { type: "subagent", id: subagent.runId },
        ctx.meta.tool_use_id,
        {
          settled: subagent.handle.outcome.then(mapSubagentOutcome),
          cancel: async (reason) => {
            subagent.handle.interrupt(
              reason === ENGINE_DISPOSE_REASON ? "caller_disposed" : "model_cancelled",
            );
            await subagent.handle.outcome;
          },
        },
      );
      return Promise.resolve({ content: { run_id: subagent.runId } });
    },
  });
}

function mapSubagentOutcome(outcome: AgentRunOutcome): SessionOutcome {
  switch (outcome.status) {
    case "completed":
      return { status: "completed", summary: submissionSummary(outcome.submission) };
    case "cancelled":
      return { status: "cancelled", summary: outcome.reason };
    case "failed":
      return { status: "failed", summary: outcome.failure.message };
  }
}

function submissionSummary(submission: JsonValue | undefined): string {
  if (
    typeof submission === "object" &&
    submission !== null &&
    !Array.isArray(submission) &&
    typeof submission.summary === "string"
  ) {
    return submission.summary;
  }
  return submission === undefined ? "completed" : JSON.stringify(submission);
}

// --- ask_advisor ---------------------------------------------------------------

const AskAdvisorInputSchema = z.object({
  /** The terminal tool the caller intends to call next. */
  tool_name: z.string().min(1),
  /** The exact payload the caller intends to submit. */
  payload: JsonObjectSchema.optional(),
});

function askAdvisorTool(calls: AgentRunCalls): ToolDefinition {
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

// --- read_agent_run_transcript ---------------------------------------------------

const ReadTranscriptInputSchema = z.object({
  run_id: z.string().min(1),
  /** Byte offset to resume from; 0 reads from the start. */
  offset: z.number().int().min(0).default(0),
  max_bytes: z.number().int().positive().max(MAX_READ_BYTES).default(DEFAULT_READ_BYTES),
});

function readAgentRunTranscriptTool(calls: AgentRunCalls): ToolDefinition {
  return defineTool({
    name: "read_agent_run_transcript",
    description:
      "Read a started agent run's JSONL transcript by byte offset. Returns the chunk, the offset to resume from, and whether the end was reached.",
    input: ReadTranscriptInputSchema,
    execute: async (input) => {
      const path = calls.transcriptPathOf(agentRunIdFrom(input.run_id));
      if (path === undefined) {
        return { content: `unknown agent run: ${input.run_id}`, isError: true };
      }
      const read = await calls.readTranscriptFile(path, input.offset, input.max_bytes);
      return {
        content: { transcript: read.data, next_offset: read.next_offset, eof: read.eof },
      };
    },
  });
}
